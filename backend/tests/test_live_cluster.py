"""Pruebas optativas con CockroachDB real; no modifican las filas precargadas.

PowerShell: $env:RUN_LIVE_TESTS='1'; python -m pytest backend/tests/test_live_cluster.py -q
"""

import os
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.db import Database
from backend.service import ACADEMIC, CONTACT

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="Requiere clúster inicializado; activar RUN_LIVE_TESTS=1")


def test_live_registration_and_lossless_reconstruction():
    student = {"sede": "aguazul", "nombre": "Prueba de integración UPTC", "programa": "Ingeniería de Sistemas", "semestre": 4, "correo": "prueba.integracion@example.com", "telefono": "3000000000", "direccion": "Laboratorio de pruebas"}
    student_id = None
    try:
        with TestClient(app) as client:
            created = client.post("/api/estudiantes", json=student)
            assert created.status_code == 201, created.text
            student_id = created.json()["estudiante"]["id"]
            response = client.get(f"/api/estudiantes/aguazul/{student_id}")
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["completo"] is True
            assert data["contacto"]["correo"] == student["correo"]
            assert data["estudiante"] == {"id": student_id, **student}
            assert set(data["fragmentos"]["academico"]) & set(data["fragmentos"]["contacto"]) == {"sede", "id"}
            locations = data["evidencia"]["ubicaciones"]
            assert len(locations["academico"]["replicas"]) == 1
            assert len(locations["contacto"]["replicas"]) == 1
            assert locations["academico"]["replicas"] != locations["contacto"]["replicas"]
    finally:
        if student_id:
            # Se eliminan exclusivamente los datos ficticios creados por ESTA
            # prueba, identificados por su UUID recién generado.
            with app.state.service.db.connection() as connection:
                connection.execute(f"DELETE FROM {CONTACT} WHERE sede=%s AND id=%s", ("aguazul", student_id))
                connection.execute(f"DELETE FROM {ACADEMIC} WHERE sede=%s AND id=%s", ("aguazul", student_id))


def test_live_transaction_rolls_back_academic_insert_when_second_write_fails():
    student_id = uuid4()
    database = app.state.service.db

    def operation(connection):
        connection.execute(f"INSERT INTO {ACADEMIC} (sede,id,nombre,programa,semestre) VALUES (%s,%s,%s,%s,%s)", ("tunja",student_id,"Prueba rollback","Sistemas",1))
        connection.execute(f"INSERT INTO {CONTACT} (sede,id,correo,telefono,direccion) VALUES (%s,%s,%s,%s,%s)", ("tunja",student_id,None,"3000000000","Laboratorio"))

    with pytest.raises(psycopg.errors.NotNullViolation):
        database.transaction(operation)
    with database.connection() as connection:
        assert connection.execute(f"SELECT count(*) AS n FROM {ACADEMIC} WHERE sede=%s AND id=%s", ("tunja",student_id)).fetchone()["n"] == 0


def test_live_sedes_and_validation_are_available():
    with TestClient(app) as client:
        for sede in ("tunja", "duitama", "sogamoso", "aguazul"):
            response = client.get(f"/api/estudiantes?sede={sede}&limite=2")
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["total"] > 0
            assert all(student["sede"] == sede for student in data["estudiantes"])
        response = client.get("/api/validaciones")
        assert response.status_code == 200, response.text
        assert response.json()["correcto"], response.text
        assert {row["archivo"] for row in response.json()["resultados"]} == {"01_ubicacion.sql", "02_horizontal.sql", "03_vertical.sql"}


def test_live_physical_distribution_program_filter_and_executed_plan():
    with TestClient(app) as client:
        distribution = client.get("/api/distribucion")
        assert distribution.status_code == 200, distribution.text
        fragments = distribution.json()["fragmentos"]
        assert len(fragments) == 5
        assert all(fragment["ubicacion_correcta"] and fragment["disponible"] for fragment in fragments)
        assert sum(fragment["filas"] for fragment in fragments if fragment["particion"]) >= 100
        node_id = next(fragment["node_id"] for fragment in fragments if fragment["particion"] == "aguazul")
        response = client.get("/api/estudiantes", params={"sede": "aguazul", "programa": "Ingeniería Ambiental", "limite": 2, "explicar": "true"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data["estudiantes"]) == 2
        assert all(student["programa"] == "Ingeniería Ambiental" for student in data["estudiantes"])
        assert data["evidencia"]["plan"]["sql_nodes"]
        assert f"n{node_id}" in data["evidencia"]["plan"]["kv_nodes"]


def test_live_connection_uses_another_gateway_if_first_host_is_unavailable():
    settings = app.state.service.db.settings
    # Puerto local sin servidor, sin detener ni modificar ningún nodo de Claude.
    first = replace(settings.nodes[0], port=1)
    database = Database(replace(settings, nodes=(first, *settings.nodes[1:])))
    try:
        with database.connection() as connection:
            gateway = database.gateway(connection)
            assert gateway["port"] != 1
            assert connection.execute(f"SELECT count(*) AS n FROM {ACADEMIC} WHERE sede='aguazul'").fetchone()["n"] > 0
    finally:
        database.close()
