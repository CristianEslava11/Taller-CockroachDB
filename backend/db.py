"""Conexiones PostgreSQL a CockroachDB y reintentos de serialización."""

import logging
import time
from contextlib import contextmanager
from threading import Lock

import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool, PoolTimeout

from backend.config import Node, Settings
from backend.metadata import BoundedResolver

logger = logging.getLogger(__name__)


class DatabaseUnavailable(Exception):
    def __init__(self, message="No se pudo acceder al fragmento solicitado.", code="fragmento_no_disponible"):
        super().__init__(message)
        self.code = code


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.resolver = BoundedResolver()
        self._pool = None
        self._pool_lock = Lock()

    def _connection_info(self):
        if self.settings.database_url:
            params = conninfo_to_dict(self.settings.database_url)
        else:
            params = dict(host=",".join(node.host for node in self.settings.nodes), port=",".join(str(node.port) for node in self.settings.nodes), dbname=self.settings.database, user=self.settings.user, password=self.settings.password, sslmode=self.settings.sslmode, connect_timeout=str(self.settings.connect_timeout))
        params["options"] = (params.get("options", "") + f" -c statement_timeout={self.settings.statement_timeout_ms}").strip()
        return make_conninfo(**params)

    def _resolved_info(self, conninfo):
        params = conninfo_to_dict(conninfo)
        if params.get("hostaddr"):
            return conninfo
        hosts = params["host"].split(",")
        ports = params.get("port", "26257").split(",")
        if len(ports) == 1:
            ports *= len(hosts)
        resolved = []
        for host, port in zip(hosts, ports):
            try:
                resolved.append((host, port, self.resolver.resolve(host)))
            except OSError:
                continue
        if not resolved:
            raise psycopg.OperationalError("Ningún host SQL pudo resolverse dentro del tiempo límite.")
        params.update(host=",".join(item[0] for item in resolved), port=",".join(item[1] for item in resolved), hostaddr=",".join(item[2] for item in resolved))
        return make_conninfo(**params)

    def pool(self):
        with self._pool_lock:
            if self._pool is None:
                database = self

                class ResolvedConnection(psycopg.Connection):
                    @classmethod
                    def connect(cls, conninfo="", **kwargs):
                        return super().connect(database._resolved_info(conninfo), **kwargs)

                self._pool = ConnectionPool(self._connection_info(), connection_class=ResolvedConnection, kwargs={"row_factory": dict_row}, min_size=2, max_size=8, timeout=5, check=ConnectionPool.check_connection, open=True, name="uptc-api")
            return self._pool

    def close(self):
        with self._pool_lock:
            if self._pool:
                self._pool.close()
                self._pool = None
        self.resolver.close()

    @contextmanager
    def connection(self, node: Node | None = None):
        if node is None:
            # El pool comprueba y descarta conexiones rotas antes de entregarlas.
            pool = self.pool()
            try:
                connection = pool.getconn()
            except (PoolTimeout, psycopg.OperationalError) as exc:
                raise DatabaseUnavailable("No hay un nodo de conexión disponible.", "conexion_no_disponible") from exc
            try:
                # El commit queda fuera de la devolución al pool. Así una pérdida
                # durante commit sigue propagándose como confirmación incierta.
                with connection.transaction():
                    yield connection
            finally:
                pool.putconn(connection)
            return
        # Libpq prueba hosts alternativos al establecer una conexión. No replica
        # ni recupera un fragmento que tiene su única copia en un nodo caído.
        nodes = (node,) if node else self.settings.nodes
        try:
            kwargs = dict(
                host=",".join(item.host for item in nodes),
                port=",".join(str(item.port) for item in nodes),
                dbname=self.settings.database,
                user=self.settings.user,
                password=self.settings.password,
                sslmode=self.settings.sslmode,
                connect_timeout=self.settings.connect_timeout,
                options=f"-c statement_timeout={self.settings.statement_timeout_ms}",
                row_factory=dict_row,
            )
            if self.settings.database_url and node is None:
                # La URL del contrato puede ordenar los gateways libremente.
                kwargs = {"options": kwargs["options"], "row_factory": dict_row}
            connection = psycopg.connect(self.settings.database_url if node is None else "", **kwargs)
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailable("No hay un nodo de conexión disponible.", "conexion_no_disponible") from exc
        with connection:
            yield connection

    @staticmethod
    def gateway(connection):
        row = connection.execute("SHOW node_id").fetchone()
        locality = connection.execute("SHOW LOCALITY").fetchone()
        tiers = dict(part.split("=", 1) for part in str(next(iter(locality.values()))).split(",") if "=" in part)
        return {"node_id": int(next(iter(row.values()))), "nombre": f"{tiers.get('sede')}-{tiers.get('rol')}", "host": connection.info.host, "port": connection.info.port}

    def transaction(self, operation):
        # Solo se reintenta SQLSTATE 40001: nunca una desconexión con resultado
        # de COMMIT incierto. El UUID se crea antes del bucle de reintentos.
        for attempt in range(self.settings.transaction_retries + 1):
            operation_finished = False
            try:
                with self.connection() as connection:
                    result = operation(connection)
                    operation_finished = True
                return result
            except psycopg.errors.SerializationFailure:
                if attempt == self.settings.transaction_retries:
                    raise DatabaseUnavailable("La transacción no pudo confirmarse después de los reintentos.", "transaccion_reintentable")
                time.sleep(min(0.05 * 2**attempt, 0.4))
            except psycopg.OperationalError as exc:
                if operation_finished:
                    raise DatabaseUnavailable("La conexión se perdió durante la confirmación. El resultado puede ser incierto; comprueba los registros antes de volver a enviar el formulario.", "confirmacion_incierta") from exc
                raise
        raise AssertionError("Bucle de reintentos agotado")
