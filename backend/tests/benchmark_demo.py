"""Medición HTTP real. Detiene únicamente Central y lo restaura en finally.

Desde la raíz: python -m backend.tests.benchmark_demo
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from backend.main import app

BASE = "http://127.0.0.1:8000"


def request(path, body=None):
    started = time.perf_counter()
    req = Request(BASE + path, data=body, headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urlopen(req, timeout=12) as response:
            status, data = response.status, json.load(response)
    except HTTPError as response:
        status, data = response.code, json.load(response)
    return status, data, round(time.perf_counter() - started, 4)


def main():
    results = {"fecha_utc": datetime.now(timezone.utc).isoformat(), "muestras": []}
    student_name = "Prueba rendimiento " + str(uuid4())
    database = app.state.service.db
    stopped = False
    try:
        status, listed, _ = request("/api/estudiantes?sede=tunja&limite=1")
        assert status == 200
        detail_path = "/api/estudiantes/tunja/" + listed["estudiantes"][0]["id"]
        paths = ["/api/estudiantes?sede=tunja", "/api/estudiantes?sede=duitama", "/api/estudiantes", "/api/distribucion", "/api/nodos"]
        for path in paths:
            status, _, seconds = request(path)
            assert status == 200
            results["muestras"].append({"estado": "todos_activos", "ruta": path, "segundos": seconds})
        stopped = True
        subprocess.run(["docker", "compose", "stop", "tunja-central"], check=True)
        for round_number in range(3):
            for path in paths:
                status, data, seconds = request(path)
                assert status == 200, (path, status, data)
                results["muestras"].append({"estado": "central_apagado", "ronda": round_number, "ruta": path, "segundos": seconds})
                print(f"Central apagado, ronda {round_number}: {path} -> {seconds:.3f} s", flush=True)
                if path == "/api/distribucion":
                    contact = next(fragment for fragment in data["fragmentos"] if fragment["tabla"] == "estudiantes_contacto")
                    assert contact["nodo_real"] == "tunja-central"
                    if round_number > 0:
                        assert contact["disponible"] is False
            if round_number < 2:
                time.sleep(3.2)  # Incluye actualizaciones al vencer la caché.
        status, detail, seconds = request(detail_path)
        assert status == 200 and detail["completo"] is False and detail["contacto"] is None
        results["detalle_parcial_segundos"] = seconds
        payload = {"sede": "tunja", "nombre": student_name, "programa": "Sistemas", "semestre": 1, "correo": "rendimiento." + str(uuid4()) + "@example.com", "telefono": "3001234567", "direccion": "Laboratorio de prueba"}
        status, error, seconds = request("/api/estudiantes", json.dumps(payload).encode())
        assert status == 503 and error["error"]["sqlstate"] == "57014", error
        with database.connection() as connection:
            assert connection.execute("SELECT count(*) AS n FROM estudiantes_academicos WHERE nombre=%s", (student_name,)).fetchone()["n"] == 0
        results["registro_fallido_segundos"] = seconds
        status, validation, seconds = request("/api/validaciones")
        assert status == 200
        assert any(row["archivo"] == "03_vertical.sql" and row["estado"] == "no_comprobado" for row in validation["resultados"])
        results["validacion_con_caida_segundos"] = seconds
        for body in (b'{"sede":', b'{"nombre":"\xff"}'):
            status, error, _ = request("/api/estudiantes", body)
            assert status in (400, 422) and error["error"]["codigo"] == "DATOS_INVALIDOS"
        results["atomicidad_y_errores"] = True
    finally:
        if stopped:
            subprocess.run(["docker", "compose", "start", "tunja-central"], check=True)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status, detail, _ = request(detail_path)
                if status == 200 and detail["completo"]:
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("Central se inició, pero el contacto no se recuperó dentro del límite.")
        with database.connection() as connection:
            connection.execute("DELETE FROM estudiantes_academicos WHERE nombre=%s", (student_name,))
            results["estudiantes_finales"] = connection.execute("SELECT count(*) AS n FROM estudiantes_academicos").fetchone()["n"]
        database.close()
    status, validation, _ = request("/api/validaciones")
    assert status == 200 and validation["ok"]
    results["validaciones_finales"] = len(validation["resultados"])
    slowest = max(sample["segundos"] for sample in results["muestras"] if sample["estado"] == "central_apagado")
    results["maximo_consultas_sin_contacto_segundos"] = slowest
    target = Path(__file__).resolve().parents[1] / "evidencias" / "rendimiento.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    assert slowest < 1, f"Se superó la meta de 1 s: {slowest}"
    print(f"OK: máximo {slowest:.3f} s; Central restaurado; {results['estudiantes_finales']} estudiantes; {results['validaciones_finales']} validaciones.", flush=True)


if __name__ == "__main__":
    main()
