# App del laboratorio UPTC

FastAPI y frontend HTML/JS separados en carpetas y servidos en el mismo origen.
Implementa el [contrato de Claude](../docs/contrato.md) y utiliza el esquema `uptc`
de `db/`. Los cinco servidores de CockroachDB siguen a cargo de la infraestructura.

## Arranque desde Windows

Desde la raíz del proyecto, con los nodos y el contenedor `init` ya inicializados:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Abrir http://127.0.0.1:8000/ y http://127.0.0.1:8000/docs.
Si el entorno virtual ya existe y tiene las dependencias, basta con la última línea.

Para cambiar la conexión, copiar `backend/.env.example` a `backend/.env`.
Se admiten `DATABASE_URL`, `NODOS_HTTP` y `TIMEOUT_SQL` del contrato.
Como alternativa, `DB_HOSTS` y `DB_PORTS` describen los cinco nodos en orden:
Tunja académico, Duitama, Sogamoso, Aguazul, Central.
Las credenciales permanecen en el backend.

## Arranque de la app en Docker

El Compose principal incluye los cinco nodos, `init` y la API. La API espera
a que `init` termine correctamente:

```powershell
docker compose up -d --build
```

Primero detener cualquier Uvicorn local que use el puerto 8000. Abrir la misma
dirección http://127.0.0.1:8000/. FastAPI también sirve el frontend.
Para reconstruir solo la app: `docker compose up -d --build --no-deps api`.
El archivo `backend/compose.api.yml` se conserva como complemento compatible,
pero ya no es necesario para el arranque normal.

La imagen incluye `db/validacion/`, necesario para las validaciones de la API.
`backend/Dockerfile.dockerignore` excluye entornos virtuales, credenciales,
cachés, pruebas y capturas del contexto de construcción.

## API del contrato

| Ruta | Función |
| --- | --- |
| `GET /health` | Disponibilidad de la API |
| `GET /api/nodos` | Disponibilidad HTTP, IDs reales, conteos y consolas |
| `GET /api/distribucion` | Ubicación esperada y observada, réplicas e índices |
| `GET /api/estudiantes?sede=aguazul&limite=25&desplazamiento=0` | Datos académicos paginados |
| `GET /api/estudiantes/{sede}/{UUID}` | Reconstrucción por JOIN, o datos académicos parciales |
| `POST /api/estudiantes` | Dos INSERT en una transacción distribuida |
| `GET /api/validaciones` | Ejecuta los tres scripts `db/validacion/*.sql` |
| `GET /api/explain?sede=aguazul` | Plan estimado de consulta académica |

El listado permite `programa` por igualdad exacta. El listado y el detalle
permiten `explicar=true`: vuelve a ejecutar la lectura con EXPLAIN ANALYZE
y devuelve `sql_nodes`, `kv_nodes` y el texto completo en la evidencia.
En Estudiantes, al marcar «Incluir EXPLAIN ANALYZE», la interfaz muestra
el nodo de conexión y los nodos KV que leyeron los datos con sus nombres e IDs
actuales de `/api/nodos`. Para todas las sedes, presenta cada plan por separado.
«Ver plan ejecutado» muestra el texto completo. Si falta un nombre, conserva
el ID real y lo indica; no infiere el lector a partir de la sede seleccionada.
El endpoint `/api/explain` se conserva para consultar planes estimados desde
Swagger; la interfaz utiliza la evidencia de las lecturas ejecutadas.

El registro recibe `sede`, `nombre`, `programa`, `semestre`, `correo`,
`telefono` y `direccion`. Se genera un UUID antes de la transacción.
El correo se convierte a minúsculas y un teléfono como `+57 (300) 123-4567`
se normaliza a `3001234567`; después se aplican las restricciones del contrato.

Todas las respuestas de estudiantes incluyen `evidencia.nodo_conexion` y
`evidencia.fragmentos`. El detalle incluye `completo`, `academico`, `contacto`
y `fragmentos_no_disponibles`. Se conservan campos adicionales para mostrar
los fragmentos lado a lado y el registro reconstruido.

Los errores siguen `{"error":{"codigo":"...","mensaje":"..."}}`, con HTTP
400 o 422 para JSON inválido, 422 para campos inválidos,
404 para un registro inexistente, 409 para duplicados
y 503 para consultas o fragmentos inaccesibles.

Las rutas previas sin `/api` se conservan para compatibilidad con las pruebas
iniciales; el frontend utiliza las rutas del contrato.

## Comportamiento ante fallos

- `psycopg_pool` reutiliza entre dos y ocho conexiones y comprueba su estado
  antes de entregarlas. Libpq puede probar otros gateways al conectar.
- La resolución DNS tiene una espera máxima de 150 ms por hostname; continúa
  en segundo plano sin duplicar una resolución pendiente. HTTP conecta a la IP
  resuelta con un límite de 250 ms por operación de socket.
- El estado procede de `/health?ready=1`; los IDs y localidades reales,
  de `/api/v2/nodes/`. Salud, nodos y rangos se guardan durante tres segundos
  y comparten una sola actualización por clave entre peticiones concurrentes.
  La disponibilidad mostrada puede tardar esos tres segundos en actualizarse,
  además del intervalo de refresco del frontend. `consultado_en` identifica
  la consulta real de rangos; `cache_metadatos_segundos` comunica su vigencia.
- Los estudiantes, las escrituras y las validaciones SQL no usan esta caché.
  Los conteos del mapa tienen un límite SQL de 300 ms: si fallan, se muestran
  sin conteo. Las lecturas y escrituras de estudiantes conservan `TIMEOUT_SQL`
  (cinco segundos por defecto).
- El gateway se obtiene con `SHOW node_id` y `SHOW LOCALITY`.
  No se habilita `allow_unsafe_internals` ni se consulta `crdb_internal`.
- La ubicación se comprueba con `SHOW RANGES ... WITH INDEXES`.
  Esta evidencia procede de metadatos y puede responder con un fragmento apagado.
- El detalle completo también comprueba los rangos de la fila con `SHOW RANGE
  ... FOR ROW`. Si falla esta evidencia adicional después de una lectura o
  un commit exitoso, se devuelve una advertencia y se conserva el éxito.
- Si el JOIN supera el tiempo límite, se revierte esa lectura y se consulta
  solo el fragmento académico en una nueva conexión. Si responde, el detalle
  es parcial; si también falla, se devuelve 503.
- Sin filtro de sede, se consultan las cuatro particiones por separado y se
  pagina el conjunto disponible. `total` cuenta solo las sedes consultables;
  `parcial` y `sedes_no_disponibles` identifican las omisiones.
- Solo se reintenta SQLSTATE `40001`, hasta tres veces. Una desconexión durante
  el commit devuelve `CONFIRMACION_INCIERTA` sin repetir la escritura.
- Cambiar de gateway no recupera un fragmento con una sola réplica.

CockroachDB no acepta placeholders de servidor en EXPLAIN ANALYZE. Se utiliza
[ClientCursor de psycopg](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#client-side-binding-cursors)
para adaptar los parámetros, sin interpolación manual de valores.

## Pruebas

Desde la raíz:

```powershell
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
```

Las pruebas de integración requieren el clúster inicializado:

```powershell
$env:RUN_LIVE_TESTS = '1'
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
Remove-Item Env:RUN_LIVE_TESTS
```

Se comprueban el registro, reconstrucción campo por campo, réplicas distintas,
rollback de la segunda escritura, acceso a las cuatro sedes, validaciones SQL,
distribución de índices, filtro por programa, nodos del plan ejecutado y conexión
por otro gateway. El registro temporal se elimina exclusivamente por su UUID.

Los casos de detalle y listado parcial se prueban también con fallos controlados.
La demostración real de caída de Central se coordina con
`bash scripts/validar.sh`, que detiene y restaura el nodo. No se ejecuta al
cargar una página ni al pulsar el botón de validaciones.

Para medir la API que está corriendo en Docker, desde Windows:

```powershell
backend/.venv/Scripts/python.exe -m backend.tests.benchmark_demo
```

Esta prueba detiene Central, mide cinco rutas durante tres rondas, incluyendo
el vencimiento de la caché, y verifica detalle parcial, rollback y errores JSON.
Restaura Central en `finally`, elimina exclusivamente su registro de prueba y
vuelve a ejecutar las validaciones. No ejecutar simultáneamente con otra demo
que esté deteniendo el mismo nodo.

La medición guardada en [evidencias/rendimiento.json](evidencias/rendimiento.json)
registró entre 0,014 y 0,209 s en las quince consultas que no requieren contacto.
Las operaciones que necesitan el fragmento caído tardaron aproximadamente cinco
segundos, correspondientes al límite SQL. Al finalizar había 100 estudiantes
y las 32 comprobaciones SQL pasaron.

No hay login ni datos reales. Es una demostración académica local.
