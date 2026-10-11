"""API y servidor del frontend local del taller."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.config import Settings
from backend.db import Database, DatabaseUnavailable
from backend.models import EstudianteCreate, Sede
from backend.service import ShardingService

logger = logging.getLogger(__name__)
settings = Settings.from_env()


@asynccontextmanager
async def lifespan(instance):
    database = getattr(instance.state.service, "db", None)
    if database:
        database.pool()
    try:
        yield
    finally:
        if database:
            database.close()


app = FastAPI(title="UPTC · Laboratorio de sharding", version="1.0.0", description="Estudiantes ficticios. Fragmentación horizontal por sede y vertical entre nodos académicos y Central.", lifespan=lifespan)
app.state.service = ShardingService(Database(settings))
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@app.exception_handler(DatabaseUnavailable)
def unavailable_handler(request: Request, exc: DatabaseUnavailable):
    code = exc.code
    if request.url.path.startswith("/api/"):
        code = "SIN_CONEXION" if code == "conexion_no_disponible" else "CONFIRMACION_INCIERTA" if code == "confirmacion_incierta" else "FRAGMENTO_NO_DISPONIBLE"
    return JSONResponse(status_code=503, content={"error": {"codigo": code, "mensaje": str(exc)}})


@app.exception_handler(psycopg.Error)
def database_error_handler(request: Request, exc: psycopg.Error):
    logger.warning("Consulta %s falló (%s)", request.url.path, exc.sqlstate)
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return JSONResponse(status_code=409, content={"error": {"codigo": "CORREO_DUPLICADO" if request.url.path.startswith("/api/") else "registro_duplicado", "mensaje": "Ya existe un registro con ese identificador o correo electrónico.", "sqlstate": exc.sqlstate}})
    if isinstance(exc, psycopg.errors.CheckViolation):
        return JSONResponse(status_code=422, content={"error": {"codigo": "DATOS_INVALIDOS", "mensaje": "Los datos no cumplen las restricciones de la base.", "sqlstate": exc.sqlstate}})
    code = "tiempo_limite" if isinstance(exc, psycopg.errors.QueryCanceled) else "consulta_no_disponible"
    if request.url.path.startswith("/api/"):
        code = "FRAGMENTO_NO_DISPONIBLE"
    fragments = getattr(exc, "fragmentos", [])
    message = "No se pudo completar la operación. Comprueba la disponibilidad de los fragmentos."
    if isinstance(exc, psycopg.errors.QueryCanceled):
        message = f"El fragmento {', '.join(fragments) if fragments else 'solicitado'} no respondió dentro del tiempo límite. Si era un registro, la transacción se revirtió."
    return JSONResponse(status_code=503, content={"error": {"codigo": code, "mensaje": message, "sqlstate": exc.sqlstate, "fragmentos": fragments}})


@app.exception_handler(RequestValidationError)
def invalid_request(request: Request, exc: RequestValidationError):
    messages = [f"{'.'.join(map(str, error['loc'][1:]))}: {error['msg']}" for error in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {"codigo": "DATOS_INVALIDOS", "mensaje": " · ".join(messages)}})


@app.exception_handler(StarletteHTTPException)
def http_error(request: Request, exc: StarletteHTTPException):
    code = "NO_ENCONTRADO" if exc.status_code == 404 else "DATOS_INVALIDOS" if exc.status_code == 400 else "ERROR_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"error": {"codigo": code, "mensaje": str(exc.detail)}})


@app.get("/health", tags=["Estado"])
def health():
    return {"status": "ok", "servicio": "api", "nota": "Este endpoint indica disponibilidad de la API, no de los fragmentos."}


@app.get("/estudiantes", include_in_schema=False)
def list_students(sede: Sede | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    return app.state.service.list_students(sede.value if sede else None, limit, offset)


@app.post("/estudiantes", status_code=201, include_in_schema=False)
@app.post("/api/estudiantes", status_code=201, tags=["Estudiantes"])
def create_student(student: EstudianteCreate):
    return app.state.service.create_student(student)


@app.get("/estudiantes/{student_id}/completo", include_in_schema=False)
def complete_student(student_id: UUID, sede: Sede):
    result = app.state.service.complete_student(sede.value, student_id)
    if result is None:
        raise HTTPException(404, "No existe un estudiante completo con esa sede e identificador.")
    return result


@app.get("/cluster/nodos", include_in_schema=False)
@app.get("/api/nodos", tags=["Clúster"])
def cluster_nodes():
    return app.state.service.cluster_nodes()


@app.get("/cluster/distribucion", include_in_schema=False)
def cluster_distribution():
    return app.state.service.distribution()


@app.get("/pruebas/validacion", include_in_schema=False)
def validate():
    return app.state.service.validate()


@app.get("/pruebas/explain", include_in_schema=False)
@app.get("/api/explain", tags=["Pruebas"])
def explain(sede: Sede):
    return app.state.service.explain(sede.value)


@app.get("/api/estudiantes", tags=["Estudiantes"])
def contract_list(sede: Sede | None = None, programa: str | None = Query(None, min_length=3, max_length=120), limite: int = Query(50, ge=1, le=100), desplazamiento: int = Query(0, ge=0, le=10000), explicar: bool = False):
    return app.state.service.list_students(sede.value if sede else None, limite, desplazamiento, programa, explicar)


@app.get("/api/estudiantes/{sede}/{student_id}", tags=["Estudiantes"])
def contract_detail(sede: Sede, student_id: UUID, explicar: bool = False):
    result = app.state.service.complete_student(sede.value, student_id, explicar)
    if result is None:
        raise HTTPException(404, "No existe un estudiante con esa sede e identificador.")
    return result


@app.get("/api/distribucion", tags=["Clúster"])
def contract_distribution():
    return app.state.service.contract_distribution()


@app.get("/api/validaciones", tags=["Pruebas"])
def contract_validation():
    return app.state.service.contract_validate()


frontend = Path(__file__).resolve().parents[1] / "frontend"
if frontend.is_dir():
    app.mount("/static", StaticFiles(directory=str(frontend)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(frontend / "index.html")
