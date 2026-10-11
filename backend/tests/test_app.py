from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.db import DatabaseUnavailable
from backend.main import app


class StubService:
    def __init__(self):
        self.calls = []

    def list_students(self, sede, limit, offset):
        self.calls.append((sede, limit, offset))
        return {"estudiantes": [], "total": 0, "limit": limit, "offset": offset}

    def complete_student(self, sede, student_id):
        self.calls.append((sede, student_id))
        return None

    def create_student(self, student):
        self.calls.append(student)
        return {"estudiante": {"id": "7fe322da-1df6-4d3f-a141-38925c19bf80", **student.model_dump()}, "evidencia": {"transaccion": "confirmada"}}


@pytest.fixture
def client():
    previous = app.state.service
    app.state.service = StubService()
    with TestClient(app) as client:
        yield client
    app.state.service = previous


def payload():
    return {"sede": "aguazul", "nombre": "Laura Torres", "programa": "Ingeniería de Sistemas", "semestre": 5, "correo": "laura@example.com", "telefono": "3001234567", "direccion": "Calle 10 # 8-20"}


def test_list_queries_only_selected_sede_and_preserves_pagination(client):
    response = client.get("/estudiantes?sede=aguazul&limit=10&offset=20")
    assert response.status_code == 200
    assert app.state.service.calls == [("aguazul", 10, 20)]


@pytest.mark.parametrize("query", ["sede=bogota", "limit=0", "limit=101", "offset=-1"])
def test_invalid_filters_never_reach_database(client, query):
    assert client.get(f"/estudiantes?{query}").status_code == 422
    assert not app.state.service.calls


def test_invalid_uuid_and_missing_sede_do_not_reach_database(client):
    assert client.get("/estudiantes/no-es-un-uuid/completo?sede=tunja").status_code == 422
    assert client.get("/estudiantes/7fe322da-1df6-4d3f-a141-38925c19bf80/completo").status_code == 422
    assert not app.state.service.calls


def test_complete_query_uses_both_primary_key_components(client):
    student_id = "7fe322da-1df6-4d3f-a141-38925c19bf80"
    response = client.get(f"/estudiantes/{student_id}/completo?sede=duitama")
    assert response.status_code == 404
    assert app.state.service.calls == [("duitama", UUID(student_id))]


def test_registration_validates_contact_and_normalizes_sede(client):
    data = payload()
    data["sede"] = " Aguazul "
    assert client.post("/estudiantes", json=data).status_code == 201
    assert app.state.service.calls[0].sede.value == "aguazul"
    data["correo"] = "correo_invalido"
    assert client.post("/estudiantes", json=data).status_code == 422
    assert len(app.state.service.calls) == 1


@pytest.mark.parametrize("field,value", [("nombre", "Ab"), ("direccion", "C1"), ("telefono", "2001234567"), ("correo", "laura+prueba@example.com")])
def test_sql_constraints_are_rejected_before_starting_transaction(client, field, value):
    data = payload()
    data[field] = value
    assert client.post("/estudiantes", json=data).status_code == 422
    assert not app.state.service.calls


def test_email_and_phone_are_normalized_to_schema_formats(client):
    data = payload()
    data["correo"] = "LAURA@EXAMPLE.COM"
    data["telefono"] = "+57 (300) 123-4567"
    assert client.post("/estudiantes", json=data).status_code == 201
    assert app.state.service.calls[0].telefono == "3001234567"
    assert app.state.service.calls[0].correo == "laura@example.com"


def test_fragment_failure_is_an_identified_503_not_empty_success(client, monkeypatch):
    def unavailable(*args):
        raise DatabaseUnavailable()
    monkeypatch.setattr(app.state.service, "list_students", unavailable)
    response = client.get("/estudiantes?sede=tunja")
    assert response.status_code == 503
    assert response.json()["error"]["codigo"] == "fragmento_no_disponible"


def test_database_timeout_is_bounded_error(client, monkeypatch):
    def timeout(*args):
        raise psycopg.errors.QueryCanceled("mensaje interno con información de conexión")
    monkeypatch.setattr(app.state.service, "list_students", timeout)
    response = client.get("/estudiantes?sede=tunja")
    assert response.status_code == 503
    assert response.json()["error"]["codigo"] == "tiempo_limite"
    assert "información de conexión" not in response.text


def test_frontend_and_api_schema_are_available_without_database(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/health").json()["status"] == "ok"
    assert "/api/estudiantes" in client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize("body", [b'{"sede":', b'{"nombre":"\xff"}'])
def test_malformed_json_uses_contract_error_envelope(client, body):
    response = client.post("/api/estudiantes", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code in (400, 422)
    assert response.json()["error"]["codigo"] == "DATOS_INVALIDOS"
    assert "detail" not in response.json()
    assert not app.state.service.calls
