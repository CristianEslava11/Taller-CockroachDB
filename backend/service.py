"""Operaciones SQL y evidencias; ninguna ubicación se inventa en el frontend."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import re
from pathlib import Path
from uuid import uuid4

import psycopg

from backend.config import SEDES
from backend.db import Database, DatabaseUnavailable
from backend.models import EstudianteCreate
from backend.metadata import ClusterHTTP, SnapshotCache

ACADEMIC = "estudiantes_academicos"
CONTACT = "estudiantes_contacto"
ACADEMIC_FIELDS = "sede, id, nombre, programa, semestre"
CONTACT_FIELDS = "sede, id, correo, telefono, direccion"
logger = logging.getLogger(__name__)
JOIN_SQL = f"""SELECT a.sede, a.id, a.nombre, a.programa, a.semestre,
                     c.correo, c.telefono, c.direccion
              FROM {ACADEMIC} AS a
              JOIN {CONTACT} AS c ON a.sede = c.sede AND a.id = c.id
              WHERE a.sede = %s AND a.id = %s"""


def now():
    return datetime.now(timezone.utc).isoformat()


def public_error(exc):
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return "La consulta superó el tiempo límite; el fragmento podría no estar disponible."
    return "No se pudo consultar la base de datos o el fragmento solicitado."


class ShardingService:
    def __init__(self, database: Database):
        self.db = database
        self.cache = SnapshotCache(ttl=3)
        self.http = ClusterHTTP(database.resolver)

    def list_students(self, sede, limit, offset, programa=None, explicar=False):
        if sede:
            return self._list_single(sede, limit, offset, programa, explicar)
        if not sede:
            def read(campus):
                try:
                    return campus, self._list_single(campus, limit + offset, 0, programa, explicar), None
                except (psycopg.Error, DatabaseUnavailable) as exc:
                    return campus, None, exc
            with ThreadPoolExecutor(max_workers=4) as executor:
                responses = list(executor.map(read, SEDES))
            successful = [data for _, data, error in responses if error is None]
            missing = [campus for campus, _, error in responses if error is not None]
            if not successful:
                raise DatabaseUnavailable("No se pudo consultar ningún fragmento académico.")
            rows = sorted((row for data in successful for row in data["estudiantes"]), key=lambda row: (row["sede"], row["nombre"], str(row["id"])))
            evidence = dict(successful[0]["evidencia"])
            evidence["consultas_por_sede"] = [data["evidencia"] for data in successful]
            evidence["fragmentos"] = [fragment for data in successful for fragment in data["evidencia"].get("fragmentos", [])]
            return {"estudiantes": rows[offset:offset + limit], "total": sum(data["total"] for data in successful), "limit": limit, "offset": offset, "parcial": bool(missing), "sedes_no_disponibles": missing, "evidencia": evidence}

    def _list_single(self, sede, limit, offset, programa, explicar):
        where = " WHERE sede = %s" if sede else ""
        params = (sede,) if sede else ()
        if programa:
            where += " AND programa = %s"
            params += (programa,)
        with self.db.connection() as connection:
            gateway = self.db.gateway(connection)
            total = connection.execute(f"SELECT count(*) AS n FROM {ACADEMIC}{where}", params).fetchone()["n"]
            sql = f"SELECT {ACADEMIC_FIELDS} FROM {ACADEMIC}{where} ORDER BY sede, nombre, id LIMIT %s OFFSET %s"
            rows = connection.execute(sql, (*params, limit, offset)).fetchall()
            evidence = {"nodo_conexion": gateway, "fragmento": "academico", "sede": sede}
            if explicar:
                evidence["plan"] = self.execution_plan(connection, sql, (*params, limit, offset))
        self.attach_fragment_evidence(evidence, sede, include_contact=False)
        return {"estudiantes": rows, "total": total, "limit": limit, "offset": offset, "parcial": False, "sedes_no_disponibles": [], "evidencia": evidence}

    def complete_student(self, sede, student_id, explicar=False):
        partial = False
        try:
            with self.db.connection() as connection:
                gateway = self.db.gateway(connection)
                row = connection.execute(JOIN_SQL, (sede, student_id)).fetchone()
                plan = self.execution_plan(connection, JOIN_SQL, (sede, student_id)) if explicar and row else None
        except psycopg.errors.QueryCanceled:
            # Tras el rollback del JOIN se abre otra transacción. La lectura
            # académica también debe responder antes de ofrecer un detalle parcial.
            with self.db.connection() as connection:
                gateway = self.db.gateway(connection)
                sql = f"SELECT {ACADEMIC_FIELDS} FROM {ACADEMIC} WHERE sede=%s AND id=%s"
                row = connection.execute(sql, (sede, student_id)).fetchone()
                plan = self.execution_plan(connection, sql, (sede, student_id)) if explicar and row else None
            partial = True
        if not row:
            return None
        academic = {key: row[key] for key in ("sede", "id", "nombre", "programa", "semestre")}
        contact = None if partial else {key: row[key] for key in ("sede", "id", "correo", "telefono", "direccion")}
        evidence = {"nodo_conexion": gateway, "ubicaciones": None, "consulta": JOIN_SQL.strip()}
        if plan:
            evidence["plan"] = plan
        try:
            if not partial:
                with self.db.connection() as connection:
                    evidence["ubicaciones"] = self.row_locations(connection, sede, student_id)
        except Exception as exc:
            logger.warning("Reconstrucción disponible; ubicación no comprobada (%s)", type(exc).__name__)
            evidence["advertencia"] = "Registro reconstruido; no se pudo comprobar su ubicación en este momento."
        self.attach_fragment_evidence(evidence, sede)
        return {"estudiante": row, "fragmentos": {"academico": academic, "contacto": contact}, "academico": academic, "contacto": None if partial else {key: contact[key] for key in ("correo", "telefono", "direccion")}, "completo": not partial, "fragmentos_no_disponibles": [CONTACT] if partial else [],
                "evidencia": evidence}

    def create_student(self, student: EstudianteCreate):
        student_id = uuid4()
        sede = student.sede.value

        def insert(connection):
            gateway = self.db.gateway(connection)
            connection.execute(
                f"INSERT INTO {ACADEMIC} ({ACADEMIC_FIELDS}) VALUES (%s, %s, %s, %s, %s)",
                (sede, student_id, student.nombre, student.programa, student.semestre),
            )
            try:
                connection.execute(
                    f"INSERT INTO {CONTACT} ({CONTACT_FIELDS}) VALUES (%s, %s, %s, %s, %s)",
                    (sede, student_id, str(student.correo), student.telefono, student.direccion),
                )
            except psycopg.errors.QueryCanceled as exc:
                exc.fragmentos = [CONTACT]
                raise
            return gateway

        gateway = self.db.transaction(insert)
        # La evidencia se consulta DESPUÉS del COMMIT. Si falla su consulta no
        # se informa que el INSERT falló, ni se invita a registrar un duplicado.
        evidence = {"nodo_conexion": gateway, "transaccion": "confirmada", "ubicaciones": None}
        try:
            with self.db.connection() as connection:
                evidence["ubicaciones"] = self.row_locations(connection, sede, student_id)
        except Exception as exc:
            logger.warning("Registro confirmado; evidencia de ubicación no disponible (%s)", type(exc).__name__)
            evidence["advertencia"] = "Registro confirmado; no se pudo comprobar la ubicación en este momento."
        self.attach_fragment_evidence(evidence, sede)
        return {"estudiante": {"id": student_id, **student.model_dump()}, "evidencia": evidence}

    def node_metadata(self, connection=None):
        return self.cache.get("nodos", self._load_node_metadata)

    def _load_node_metadata(self):
        endpoints = dict(self.db.settings.http_urls)
        # Un nodo vivo conserva la lista de localidades de los nodos caídos.
        ordered = sorted(self.db.settings.nodes, key=lambda node: node.name != "duitama-academico")
        for node in ordered:
            try:
                payload = self.http.get(endpoints[node.name].rstrip("/") + "/api/v2/nodes/", json_body=True)
                result = []
                for item in payload["nodes"]:
                    tiers = {tier["key"]: tier["value"] for tier in item["locality"]["tiers"]}
                    result.append({"node_id": int(item["node_id"]), "nombre": f"{tiers.get('sede')}-{tiers.get('rol')}", "locality": ",".join(f"{key}={value}" for key, value in tiers.items())})
                return result
            except (OSError, ValueError, KeyError):
                continue
        raise DatabaseUnavailable("No se pudo consultar la lista de nodos por HTTP.", "metadatos_no_disponibles")

    def node_ready(self, node):
        return self.cache.get("salud:" + node.name, lambda: self._load_node_ready(node))

    def _load_node_ready(self, node):
        try:
            return self.http.get(dict(self.db.settings.http_urls)[node.name].rstrip("/") + "/health?ready=1") == 200
        except (OSError, ValueError, KeyError):
            return False

    @staticmethod
    def execution_plan(connection, sql, params):
        # CockroachDB no admite placeholders en EXPLAIN ANALYZE. ClientCursor
        # adapta y escapa los valores con psycopg, sin interpolación manual.
        with psycopg.ClientCursor(connection) as cursor:
            rows = cursor.execute("EXPLAIN ANALYZE " + sql, params).fetchall()
        lines = [str(next(iter(row.values()))) for row in rows]
        text = "\n".join(lines)
        def nodes(label):
            matches = re.findall(label + r":\s*([^\n]+)", text)
            return sorted(set(re.findall(r"\bn\d+\b", " ".join(matches))))
        return {"sql_nodes": nodes("sql nodes"), "kv_nodes": nodes("kv nodes"), "texto": lines}

    def attach_fragment_evidence(self, evidence, sede, include_contact=True):
        try:
            physical = self.contract_distribution(count_rows=False)
            evidence["fragmentos"] = [
                {"tabla": fragment["tabla"], "particion": fragment["particion"], "nodo": fragment["nodo_real"], "node_id": fragment["node_id"], "disponible": fragment["disponible"]}
                for fragment in physical["fragmentos"]
                if fragment["particion"] == sede or (include_contact and fragment["tabla"] == CONTACT)
            ]
        except Exception as exc:
            logger.warning("Evidencia adicional no comprobada (%s)", type(exc).__name__)
            evidence["fragmentos"] = []
            evidence.setdefault("advertencia", "La lectura o escritura terminó; la ubicación física no pudo comprobarse.")

    @staticmethod
    def range_summary(row):
        return {key: row.get(key) for key in ("index_name", "range_id", "start_key", "end_key", "replicas", "replica_localities", "lease_holder", "lease_holder_locality") if key in row}

    def row_locations(self, connection, sede, student_id):
        # SHOW RANGE FOR ROW recibe los componentes de la clave primaria.
        return {
            "academico": self.range_summary(connection.execute(f"SHOW RANGE FROM TABLE {ACADEMIC} FOR ROW (%s::STRING, %s::UUID)", (sede, student_id)).fetchone()),
            "contacto": self.range_summary(connection.execute(f"SHOW RANGE FROM TABLE {CONTACT} FOR ROW (%s::STRING, %s::UUID)", (sede, student_id)).fetchone()),
        }

    def cluster_nodes(self):
        try:
            metadata = {item["nombre"]: item for item in self.node_metadata()}
        except DatabaseUnavailable:
            metadata = {}
        def probe(node):
            result = {"nombre": node.name, "etiqueta": node.label, "sede": node.sede, "rol": node.role,
                      "host": node.host, "puerto": node.port, "disponible": self.node_ready(node), "node_id": metadata.get(node.name, {}).get("node_id"), "registros": None,
                      "consola": dict(self.db.settings.http_urls).get(node.name), "fragmentos": [CONTACT if node.role == "central" else ACADEMIC]}
            result["vivo"] = result["disponible"]
            if not result["disponible"]:
                result["error"] = "El nodo no responde a la comprobación HTTP de disponibilidad."
                return result
            try:
                with self.db.connection() as connection:
                    # Los conteos no deben esperar los 5 s de una lectura de datos
                    # cuando una observación de salud acaba de quedar obsoleta.
                    connection.execute("SET LOCAL statement_timeout = '300ms'")
                    try:
                        query = f"SELECT count(*) AS n FROM {CONTACT}" if node.role == "central" else f"SELECT count(*) AS n FROM {ACADEMIC} WHERE sede = %s"
                        result["registros"] = connection.execute(query, () if node.role == "central" else (node.sede,)).fetchone()["n"]
                    except psycopg.Error as exc:
                        result["error_conteo"] = public_error(exc)
            except Exception as exc:
                result["error"] = "No se pudo establecer una conexión SQL con este nodo."
            return result

        with ThreadPoolExecutor(max_workers=len(self.db.settings.nodes)) as executor:
            nodes = list(executor.map(probe, self.db.settings.nodes))
        return {"nodos": nodes, "consultado_en": now(), "nota": "Disponibilidad según HTTP /health?ready=1. El conteo no demuestra ubicación física; consulta los rangos."}

    def distribution(self):
        return self.cache.get("rangos", self._load_distribution)

    def _load_distribution(self):
        with self.db.connection() as connection:
            gateway = self.db.gateway(connection)
            nodes = self.node_metadata(connection)
            academic = connection.execute(f"SHOW RANGES FROM TABLE {ACADEMIC} WITH INDEXES").fetchall()
            contact = connection.execute(f"SHOW RANGES FROM TABLE {CONTACT} WITH INDEXES").fetchall()
            partitions = connection.execute(f"SHOW PARTITIONS FROM TABLE {ACADEMIC}").fetchall()
        return {"nodos": nodes, "fragmentos": [
            {"tabla": ACADEMIC, "tipo": "academico", "rangos": [self.range_summary(row) for row in academic]},
            {"tabla": CONTACT, "tipo": "contacto", "rangos": [self.range_summary(row) for row in contact]},
        ], "particiones": [{key: str(value) if value is not None else None for key, value in row.items()} for row in partitions],
            "evidencia": {"nodo_conexion": gateway, "consultado_en": now()}}

    def contract_distribution(self, count_rows=True):
        raw = self.distribution()
        node_by_id = {node["node_id"]: node for node in raw["nodos"]}
        with ThreadPoolExecutor(max_workers=5) as executor:
            ready = dict(zip((node.name for node in self.db.settings.nodes), executor.map(self.node_ready, self.db.settings.nodes)))
        grouped = {item["tabla"]: item["rangos"] for item in raw["fragmentos"]}
        fragments = []
        for campus in (*SEDES, None):
            table = ACADEMIC if campus else CONTACT
            expected = f"{campus}-academico" if campus else "tunja-central"
            ranges = grouped[table]
            if campus:
                ranges = [row for row in ranges if "PrefixEnd" not in str(row.get("start_key")) and re.search(r'/"' + campus + r'"(?:/|$)', str(row.get("start_key")))]
            replica_ids = sorted({replica for row in ranges for replica in row.get("replicas", [])})
            names = [node_by_id.get(replica, {}).get("nombre") for replica in replica_ids]
            observed = names[0] if len(names) == 1 else None
            correct = bool(ranges) and all(len(row.get("replicas", [])) == 1 for row in ranges) and names == [expected]
            available = bool(names) and all(name is not None and ready.get(name, False) for name in names)
            fragments.append({"tabla": table, "tipo": "horizontal" if campus else "vertical", "particion": campus, "nodo_esperado": expected, "nodo_real": observed, "node_id": replica_ids[0] if len(replica_ids) == 1 else None, "ubicacion_correcta": correct, "disponible": available, "filas": None, "rangos": ranges})
        if count_rows:
            def count(fragment):
                if not fragment["disponible"]:
                    return
                try:
                    with self.db.connection() as connection:
                        connection.execute("SET LOCAL statement_timeout = '300ms'")
                        where = " WHERE sede=%s" if fragment["particion"] else ""
                        params = (fragment["particion"],) if fragment["particion"] else ()
                        fragment["filas"] = connection.execute(f"SELECT count(*) AS n FROM {fragment['tabla']}{where}", params).fetchone()["n"]
                except (psycopg.Error, DatabaseUnavailable):
                    fragment["error_conteo"] = "No se pudo completar el conteo dentro del tiempo límite."
            with ThreadPoolExecutor(max_workers=5) as executor:
                list(executor.map(count, fragments))
        return {"fragmentos": fragments, "nodos": raw["nodos"], "evidencia": raw["evidencia"], "consultado_en": raw["evidencia"]["consultado_en"], "cache_metadatos_segundos": 3}

    def contract_validate(self):
        results = []
        directory = Path(__file__).resolve().parents[1] / "db" / "validacion"
        for filename in ("01_ubicacion.sql", "02_horizontal.sql", "03_vertical.sql"):
            try:
                sql = (directory / filename).read_text(encoding="utf-8")
                with self.db.connection() as connection:
                    rows = connection.execute(sql).fetchall()
                for index, row in enumerate(rows):
                    results.append({**row, "archivo": filename, "id": f"{filename}:{index}", "nombre": row["prueba"].capitalize(), "estado": "ok" if row["ok"] else "fallo"})
            except (OSError, psycopg.Error, DatabaseUnavailable) as exc:
                results.append({"prueba": filename, "archivo": filename, "nombre": filename, "ok": False, "estado": "no_comprobado", "detalle": public_error(exc)})
        correct = bool(results) and all(item["ok"] for item in results)
        return {"ok": correct, "correcto": correct, "resultados": results, "consultado_en": now(), "nota": "Se ejecutaron los tres scripts SQL del contrato: ubicación de rangos e índices, fragmentación horizontal y vertical. La caída de nodos y la reconstrucción campo por campo se demuestran con scripts/validar.sh."}

    def explain(self, sede):
        with self.db.connection() as connection:
            gateway = self.db.gateway(connection)
            rows = connection.execute(f"EXPLAIN SELECT {ACADEMIC_FIELDS} FROM {ACADEMIC} WHERE sede = %s", (sede,)).fetchall()
        return {"sede": sede, "plan": [str(next(iter(row.values()))) for row in rows], "evidencia": {"nodo_conexion": gateway}}

    def validate(self):
        return self.contract_validate()
