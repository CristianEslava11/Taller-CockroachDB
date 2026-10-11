from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest

from backend.config import Settings
from backend.db import Database, DatabaseUnavailable
from backend.service import ShardingService


def test_detail_keeps_academic_data_after_contact_timeout(monkeypatch):
    service = ShardingService(Database(Settings.from_env()))
    student = {"sede": "aguazul", "id": uuid4(), "nombre": "Prueba parcial", "programa": "Sistemas", "semestre": 4}
    events = []

    class Connection:
        def execute(self, sql, params):
            if "JOIN" in sql:
                raise psycopg.errors.QueryCanceled()
            return self

        def fetchone(self):
            return student

    @contextmanager
    def connection():
        try:
            yield Connection()
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    monkeypatch.setattr(service.db, "connection", connection)
    monkeypatch.setattr(service.db, "gateway", lambda connection: {"node_id": 1})
    monkeypatch.setattr(service, "attach_fragment_evidence", lambda *args, **kwargs: None)
    result = service.complete_student("aguazul", student["id"])
    assert events == ["rollback", "commit"]
    assert result["completo"] is False
    assert result["academico"] == student
    assert result["contacto"] is None
    assert result["fragmentos_no_disponibles"] == ["estudiantes_contacto"]


def test_partial_list_paginates_only_available_sedes(monkeypatch):
    service = ShardingService(Database(Settings.from_env()))

    def single(sede, limit, offset, programa, explicar):
        if sede == "sogamoso":
            raise psycopg.errors.QueryCanceled()
        return {"estudiantes": [{"id": uuid4(), "sede": sede, "nombre": "Prueba"}], "total": 1, "evidencia": {"nodo_conexion": {"node_id": 1}, "fragmentos": []}}

    monkeypatch.setattr(service, "_list_single", single)
    result = service.list_students(None, 1, 1)
    assert result["parcial"] is True
    assert result["sedes_no_disponibles"] == ["sogamoso"]
    assert result["total"] == 3
    assert [row["sede"] for row in result["estudiantes"]] == ["duitama"]


def test_all_failed_sedes_cannot_be_reported_as_empty_success(monkeypatch):
    service = ShardingService(Database(Settings.from_env()))

    def fail(*args):
        raise psycopg.errors.QueryCanceled()

    monkeypatch.setattr(service, "_list_single", fail)
    with pytest.raises(DatabaseUnavailable):
        service.list_students(None, 25, 0)
