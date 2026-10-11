from contextlib import contextmanager

import psycopg
import pytest

from backend.config import Settings
from backend.db import Database, DatabaseUnavailable
from backend.models import EstudianteCreate
from backend.service import ShardingService


def test_serialization_retries_the_whole_transaction_after_rollback(monkeypatch):
    database = Database(Settings.from_env())
    events = []

    @contextmanager
    def connection():
        events.append("begin")
        try:
            yield object()
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    attempts = 0

    def operation(connection):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise psycopg.errors.SerializationFailure()
        return "confirmado"

    monkeypatch.setattr(database, "connection", connection)
    monkeypatch.setattr("backend.db.time.sleep", lambda seconds: None)
    assert database.transaction(operation) == "confirmado"
    assert events == ["begin", "rollback", "begin", "commit"]


def test_connection_loss_with_uncertain_commit_is_not_retried(monkeypatch):
    database = Database(Settings.from_env())
    attempts = []

    @contextmanager
    def connection():
        yield object()
        raise psycopg.OperationalError("Conexión perdida durante commit")

    monkeypatch.setattr(database, "connection", connection)
    with pytest.raises(DatabaseUnavailable) as error:
        database.transaction(lambda connection: attempts.append("insert"))
    assert attempts == ["insert"]
    assert error.value.code == "confirmacion_incierta"


def test_failure_in_second_fragment_rolls_back_first_fragment(monkeypatch):
    database = Database(Settings.from_env())
    committed = []
    pending = []

    class Connection:
        def execute(self, sql, params):
            if "estudiantes_contacto" in sql:
                raise psycopg.errors.QueryCanceled("Central no disponible")
            pending.append(params)

    @contextmanager
    def connection():
        try:
            yield Connection()
        except Exception:
            pending.clear()
            raise
        else:
            committed.extend(pending)

    monkeypatch.setattr(database, "connection", connection)
    monkeypatch.setattr(database, "gateway", lambda connection: {"node_id": 2})
    student = EstudianteCreate(sede="tunja", nombre="Laura Torres", programa="Sistemas", semestre=3, correo="laura@example.com", telefono="3001234567", direccion="Calle 10")
    with pytest.raises(psycopg.errors.QueryCanceled):
        ShardingService(database).create_student(student)
    assert pending == []
    assert committed == []


def test_location_failure_after_commit_does_not_report_failed_registration(monkeypatch):
    database = Database(Settings.from_env())
    inserts = []

    class Connection:
        def execute(self, sql, params):
            inserts.append((sql, params))

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(database, "connection", connection)
    monkeypatch.setattr(database, "gateway", lambda connection: {"node_id": 2})
    service = ShardingService(database)
    monkeypatch.setattr(service, "row_locations", lambda *args: (_ for _ in ()).throw(DatabaseUnavailable()))
    student = EstudianteCreate(sede="aguazul", nombre="Laura Torres", programa="Sistemas", semestre=3, correo="laura@example.com", telefono="3001234567", direccion="Calle 10")
    response = service.create_student(student)
    assert len(inserts) == 2
    assert inserts[0][1][1] == inserts[1][1][1] == response["estudiante"]["id"]
    assert response["evidencia"]["transaccion"] == "confirmada"
    assert "advertencia" in response["evidencia"]
