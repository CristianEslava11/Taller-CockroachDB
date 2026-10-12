# Contrato técnico — Taller de Sharding (UPTC)

Acuerdo entre la capa de datos (`docker-compose.yml`, `db/`, `scripts/`) y la aplicación
(`backend/`, `frontend/`). Todo lo que aparece aquí fue ejecutado y verificado contra el
clúster real (CockroachDB v26.2.7). Si algo debe cambiar, se cambia primero en este documento.

---

## 1. Contexto y decisiones

- Directorio ficticio de estudiantes de la UPTC con cuatro sedes: **Tunja (central), Duitama, Sogamoso y Aguazul**.
- Motor: **CockroachDB v26.2.7** (imagen `cockroachdb/cockroach:v26.2.7`), 5 nodos, modo `--insecure` (solo demostración local).
- Backend: **Python + FastAPI**. Frontend: **HTML + JavaScript + CSS** sin framework, servido por el mismo backend.
- Los 5 contenedores representan **servidores independientes, cada uno con su propio volumen de datos, ejecutados en un mismo computador**. Es una condición de la demostración y así se documenta en el informe.

## 2. Topología del clúster

| Nodo (servicio y hostname) | Localidad | Guarda | SQL (host) | DB Console (host) |
|---|---|---|---|---|
| `tunja-academico` | `sede=tunja,rol=academico` | partición `tunja` de `estudiantes_academicos` | `localhost:26257` | http://localhost:8080 |
| `duitama-academico` | `sede=duitama,rol=academico` | partición `duitama` | `localhost:26258` | http://localhost:8081 |
| `sogamoso-academico` | `sede=sogamoso,rol=academico` | partición `sogamoso` | `localhost:26259` | http://localhost:8082 |
| `aguazul-academico` | `sede=aguazul,rol=academico` | partición `aguazul` | `localhost:26260` | http://localhost:8083 |
| `tunja-central` | `sede=tunja,rol=central` | toda la tabla `estudiantes_contacto` | `localhost:26261` | http://localhost:8084 |

- Dentro de la red Docker `uptc`, cada nodo escucha en `<hostname>:26257` (SQL) y `<hostname>:8080` (HTTP).
- **Nombre de un nodo** = `<sede>-<rol>`, derivado de su localidad. Es la forma de identificarlo en la API y en la interfaz.
- **El `node_id` NO es fijo**: CockroachDB lo asigna según el orden en que arrancan los nodos. Nunca debe quedar escrito en el código; se obtiene en tiempo de ejecución (sección 7).
- Usuario SQL: `root`, sin contraseña. Base de datos: `uptc`.

## 3. Esquema

Tabla lógica: `estudiante(sede, id, nombre, programa, semestre, correo, telefono, direccion)`.
Se divide en dos **fragmentos verticales** que comparten la clave `(sede, id)`.

### 3.1 `estudiantes_academicos` (fragmento vertical 1, fragmentado horizontalmente por sede)

| Columna | Tipo | Restricción |
|---|---|---|
| `sede` | `STRING` | `NOT NULL`, `IN ('tunja','duitama','sogamoso','aguazul')` |
| `id` | `UUID` | `NOT NULL`, por defecto `gen_random_uuid()` |
| `nombre` | `STRING` | `NOT NULL`, longitud 3–120 |
| `programa` | `STRING` | `NOT NULL`, longitud 3–120 |
| `semestre` | `INT2` | `NOT NULL`, entre 1 y 12 |

- `PRIMARY KEY (sede, id)` (`pk_estudiantes_academicos`).
- Índice `idx_academicos_programa (sede, programa)`, particionado igual que la tabla.

### 3.2 `estudiantes_contacto` (fragmento vertical 2)

| Columna | Tipo | Restricción |
|---|---|---|
| `sede` | `STRING` | `NOT NULL` |
| `id` | `UUID` | `NOT NULL` |
| `correo` | `STRING` | `NOT NULL`, `UNIQUE`, regex `^[a-z0-9._-]+@[a-z0-9.-]+\.[a-z]{2,}$` (solo minúsculas) |
| `telefono` | `STRING` | `NOT NULL`, regex `^3[0-9]{9}$` (celular colombiano de 10 dígitos) |
| `direccion` | `STRING` | `NOT NULL`, longitud 5–200 |

- `PRIMARY KEY (sede, id)` (`pk_estudiantes_contacto`).
- `FOREIGN KEY (sede, id) REFERENCES estudiantes_academicos (sede, id) ON DELETE CASCADE` (`fk_contacto_academico`). Por eso no pueden existir contactos huérfanos.
- Índice único `uq_contacto_correo (correo)`.

### 3.3 Nombres de las restricciones (útiles para traducir errores)

`ck_academicos_sede`, `ck_academicos_nombre`, `ck_academicos_programa`, `ck_academicos_semestre`,
`ck_contacto_correo`, `ck_contacto_telefono`, `ck_contacto_direccion`, `uq_contacto_correo`,
`fk_contacto_academico`.

### 3.4 Datos precargados (`db/04_seed.sql`)

100 estudiantes ficticios: Tunja 40, Duitama 25, Sogamoso 25 y Aguazul 10. Los `id` son aleatorios y cambian cada vez que se recrea el clúster.

- Los códigos de sede van **en minúscula** en la base y en la API. La interfaz muestra `Tunja`, `Duitama`, `Sogamoso` y `Aguazul`.
- Programas por sede (para los filtros del frontend):

| Sede | Programas |
|---|---|
| tunja | Ingeniería de Sistemas y Computación, Medicina, Derecho, Licenciatura en Matemáticas, Ingeniería Civil, Enfermería |
| duitama | Ingeniería Electromecánica, Administración Turística y Hotelera, Diseño Industrial, Licenciatura en Tecnología |
| sogamoso | Ingeniería Geológica, Ingeniería de Minas, Ingeniería Industrial, Ingeniería Electrónica, Contaduría Pública |
| aguazul | Administración de Empresas, Ingeniería Ambiental, Tecnología en Gestión Agropecuaria |

## 4. Reglas de ubicación y replicación

| Fragmento | Regla de zona | Réplicas |
|---|---|---|
| Partición `<sede>` de `estudiantes_academicos` (clave primaria **e** índice, vía `OF INDEX estudiantes_academicos@*`) | `constraints = '[+sede=<sede>, +rol=academico]'` | 1 |
| Resto de `estudiantes_academicos` (rangos sin partición, siempre vacíos) | `constraints = '[+rol=academico]'` | 1 |
| `estudiantes_contacto` (clave primaria e `uq_contacto_correo`) | `constraints = '[+sede=tunja, +rol=central]'` | 1 |
| Datos internos del clúster (system, meta, liveness) | por defecto | 3 a 5, sin cambios |

- **Una sola réplica por fragmento**: así la ubicación es exclusiva y se puede demostrar la pérdida de disponibilidad. En producción se usarían réplicas por zona. Esta diferencia debe explicarse en el informe.
- Los datos internos conservan su replicación, de modo que el clúster sigue funcionando con **un** nodo apagado. **Se apaga un solo nodo a la vez.** Con dos nodos caídos, algunos rangos internos podrían perder quórum.
- La ubicación **converge** pocos segundos después de crear las reglas (24 s en las pruebas). El contenedor `init` no termina hasta que `db/validacion/01_ubicacion.sql` no reporta fallas.

## 5. Puesta en marcha

Requisito: Docker Desktop corriendo.

```bash
docker compose up -d --build    # 5 nodos + init + api (app en http://localhost:8000, Swagger en /docs)
docker compose logs -f init     # termina con "[init] Listo."
bash scripts/validar.sh         # pruebas 1-6 desde SQL (apaga y reinicia tunja-central)
```

El servicio `api` (imagen construida con `backend/Dockerfile`) espera a que `init` termine bien, es decir, con el esquema creado y los fragmentos ya en su nodo. Sus variables de entorno son las de la sección 6, con los hosts de la red Docker. `backend/compose.api.yml` queda reemplazado por este servicio.

Otros comandos:

```bash
bash scripts/validar.sh --sin-fallos                                  # solo pruebas 1-4
docker exec -it duitama-academico cockroach sql --insecure -d uptc    # consola SQL
docker compose stop sogamoso-academico                                # simular caída de un nodo
docker compose start sogamoso-academico
docker compose down -v                                                # borrar todo y empezar de cero
```

- `init` es idempotente. Si la base `uptc` ya existe, no recrea el esquema ni recarga datos.
- En Git Bash sobre Windows, los scripts exportan `MSYS_NO_PATHCONV=1` para que Git Bash no reescriba las rutas de los comandos `docker`.

### 5.1 Licencia

En CockroachDB v24.3 y posteriores, un clúster sin licencia tiene todas las funciones (incluido el particionado) durante **7 días desde su inicialización**. Después queda limitado a 5 transacciones SQL simultáneas. Hay dos opciones:

1. **Recrear el clúster** antes de la presentación con `docker compose down -v && docker compose up -d`. Así se reinicia el periodo de 7 días, y todo es reproducible.
2. **Licencia Enterprise Free** (gratuita para estudiantes). Se obtiene en el Cloud Console de Cockroach Labs (Organization → Enterprise Licenses → Create License → Free). Se copia `.env.example` a `.env`, se pega en `COCKROACH_LICENSE` y `init` la aplica. Requiere que la telemetría siga activa, como está por defecto.

## 6. Conexión del backend

| Variable | Valor cuando el backend corre en el host (desarrollo) |
|---|---|
| `DATABASE_URL` | `postgresql://root@localhost:26258,localhost:26259,localhost:26260,localhost:26257,localhost:26261/uptc?sslmode=disable&connect_timeout=3&application_name=uptc-api` |
| `NODOS_HTTP` | `tunja-academico=http://localhost:8080,duitama-academico=http://localhost:8081,sogamoso-academico=http://localhost:8082,aguazul-academico=http://localhost:8083,tunja-central=http://localhost:8084` |
| `TIMEOUT_SQL` | `5s` |

Dentro de Docker (integración), los hosts pasan a ser `<nodo>:26257` y `http://<nodo>:8080`.

Reglas:

- **Conexión con varios hosts.** libpq/psycopg 3 prueban los hosts en orden. `tunja-central` va al final para que su caída no afecte la conexión. Esto se verificó con psycopg 3: con `duitama-academico` apagado, la conexión pasó a otro nodo, la consulta de Duitama dio `57014` y las otras tres sedes respondieron. El pool debe verificar las conexiones antes de usarlas, por ejemplo con `psycopg_pool.ConnectionPool(check=ConnectionPool.check_connection)`, y descartar las que apuntan a un nodo caído.
- **Tiempo límite.** Cada operación SQL lleva `statement_timeout = TIMEOUT_SQL`. Sin tiempo límite, una consulta sobre un fragmento apagado se queda esperando indefinidamente.
- **Reintentos.** Se reintenta la transacción completa ante SQLSTATE `40001` (conflicto de serialización), hasta 3 veces con espera exponencial corta. **No** se reintenta ante `57014` (tiempo límite): significa que un fragmento no está disponible.
- **Nodo de conexión ≠ nodo de almacenamiento.** El nodo al que se conectó el backend solo coordina la consulta. Los nodos que guardan los datos se obtienen del clúster (sección 7) y nunca se deducen del host de conexión. Si el backend cambia de nodo de conexión porque uno cayó, eso **no** es recuperación de un fragmento y no debe presentarse así.
- **Prohibido `crdb_internal` y `system`.** En v26 están restringidos (error `42501`). No se debe activar `allow_unsafe_internals`. Todas las consultas de la sección 7 usan interfaces soportadas.

## 7. Consultas de introspección (verificadas)

| Necesidad | Consulta | ¿Funciona con un nodo apagado? |
|---|---|---|
| Nodo de conexión | `SHOW node_id;` y `SHOW LOCALITY;` | Sí |
| Lista de nodos (`node_id` → nombre) | HTTP `GET <nodo>/api/v2/nodes/`. Se usa `nodes[].node_id` y `nodes[].locality.tiers` (`sede`, `rol`) | Sí (se consulta un nodo vivo) |
| ¿Nodo vivo? | HTTP `GET <nodo>/health?ready=1` con 1 s de tiempo límite: `200` = vivo | — |
| Ubicación de cada rango | `SELECT index_name, start_key, replicas, replica_localities FROM [SHOW RANGES FROM TABLE <tabla> WITH INDEXES];` | **Sí**: lee metadatos replicados |
| Ubicación + tamaño | lo mismo `WITH DETAILS, INDEXES`, agregando `(span_stats->>'live_count')::INT` | **No**: espera al nodo caído |
| Rango de una fila | `SHOW RANGE FROM TABLE <tabla> FOR ROW ('<sede>', '<uuid>');` | No |
| Nodos que participaron en una consulta | `EXPLAIN ANALYZE <consulta>`: en el texto, `sql nodes: n1` es el coordinador y `kv nodes: n4` son los nodos que leyeron datos | Solo si la consulta termina |

Interpretación de `start_key` en `SHOW RANGES`:

- `…/1/"tunja"` es la partición `tunja` de la clave primaria (índice 1). `…/2/"tunja"` es la del índice `idx_academicos_programa`.
- Las claves que terminan en `/PrefixEnd`, `<TableMin>` o `<IndexMin>` son rangos sin partición, siempre vacíos.
- Para obtener la partición: `substring(start_key FROM '/"([a-z]+)"')`, siempre que `start_key NOT LIKE '%PrefixEnd%'`.
- `estudiantes_contacto` ocupa un solo rango que contiene ambos índices.

Ejemplo real de la salida de `EXPLAIN ANALYZE` para `SELECT * FROM estudiantes_academicos WHERE sede='sogamoso'` con conexión por `duitama-academico`:

```
• scan
  sql nodes: n1          ← duitama-academico (coordinador)
  kv nodes: n4           ← sogamoso-academico (dueño de la partición)
  table: estudiantes_academicos@pk_estudiantes_academicos
  spans: [/'sogamoso' - /'sogamoso']
```

## 8. API REST

- Prefijo `/api`. El frontend (`frontend/`) se sirve como archivos estáticos en `/` desde el mismo FastAPI, así que no hace falta CORS.
- Documentación interactiva en `/docs`.
- JSON en español y `snake_case`. Sedes en minúscula. `id` en formato UUID.

### 8.1 Objeto `evidencia` (lo incluyen todas las respuestas que leen o escriben estudiantes)

```json
{
  "nodo_conexion": { "node_id": 1, "nombre": "duitama-academico" },
  "fragmentos": [
    { "tabla": "estudiantes_academicos", "particion": "tunja", "nodo": "tunja-academico", "node_id": 5, "disponible": true },
    { "tabla": "estudiantes_contacto",   "particion": null,    "nodo": "tunja-central",   "node_id": 2, "disponible": true }
  ],
  "plan": { "sql_nodes": ["n1"], "kv_nodes": ["n5", "n2"] }
}
```

- `nodo_conexion` sale de `SHOW node_id` en la misma conexión que ejecutó la consulta.
- `fragmentos` sale de `SHOW RANGES ... WITH INDEXES` (rango con datos de la clave primaria). `disponible` sale de `/health?ready=1` del nodo dueño.
- `plan` es **opcional**. Solo aparece si se pide `?explicar=true`, y en ese caso el backend vuelve a ejecutar la lectura con `EXPLAIN ANALYZE` y extrae `sql nodes` y `kv nodes`.

### 8.2 Endpoints

| Método y ruta | Función |
|---|---|
| `GET /api/nodos` | Los 5 nodos: nombre, sede, rol, `node_id`, `vivo`, URL de su DB Console y fragmentos que guarda |
| `GET /api/distribucion` | Cada fragmento con su nodo esperado, su nodo real, si coincide, si está disponible y cuántas filas tiene |
| `GET /api/estudiantes?sede=&programa=&limite=50&desplazamiento=0&explicar=false` | Lista de datos **académicos** (sin contacto) |
| `GET /api/estudiantes/{sede}/{id}?explicar=false` | Registro completo reconstruido con JOIN |
| `POST /api/estudiantes` | Registra un estudiante en ambos fragmentos, en una sola transacción |
| `GET /api/validaciones` | Ejecuta `db/validacion/*.sql` y devuelve los resultados |

**`GET /api/nodos`** → `200`

```json
{
  "nodos": [
    { "nombre": "tunja-central", "sede": "tunja", "rol": "central", "node_id": 2, "vivo": true,
      "consola": "http://localhost:8084",
      "fragmentos": ["estudiantes_contacto"] }
  ]
}
```

**`GET /api/distribucion`** → `200`. Las filas se cuentan con `SELECT count(*) ... WHERE sede = $1` (o con `count(*)` de contacto) usando el tiempo límite. Si el nodo dueño está caído, `filas` es `null` y `disponible` es `false`, pero la ubicación se sigue mostrando porque viene de los metadatos.

```json
{
  "fragmentos": [
    { "tabla": "estudiantes_academicos", "tipo": "horizontal", "particion": "duitama",
      "nodo_esperado": "duitama-academico", "nodo_real": "duitama-academico",
      "ubicacion_correcta": true, "disponible": true, "filas": 25 },
    { "tabla": "estudiantes_contacto", "tipo": "vertical", "particion": null,
      "nodo_esperado": "tunja-central", "nodo_real": "tunja-central",
      "ubicacion_correcta": true, "disponible": false, "filas": null }
  ]
}
```

**`GET /api/estudiantes`**

- Con `sede`: una sola consulta que filtra por la clave de shard (`WHERE sede = $1`), así que toca solo esa partición.
- Sin `sede`: una consulta por sede y luego se combinan los resultados. Si alguna sede no responde dentro del tiempo límite, se devuelven las demás con `"parcial": true` y `"sedes_no_disponibles": ["sogamoso"]`.
- `programa` filtra por igualdad exacta y aprovecha `idx_academicos_programa`.
- Orden: `sede`, `nombre`.

```json
{
  "parcial": false,
  "sedes_no_disponibles": [],
  "total": 25,
  "estudiantes": [
    { "sede": "duitama", "id": "0ff4b509-b493-4660-acf3-c7b7e0bf105e",
      "nombre": "Camila Rojas Díaz", "programa": "Diseño Industrial", "semestre": 4 }
  ],
  "evidencia": { "...": "ver 8.1" }
}
```

**`GET /api/estudiantes/{sede}/{id}`**

- Primero se intenta reconstruir con `SELECT ... FROM estudiantes_academicos a JOIN estudiantes_contacto c USING (sede, id) WHERE a.sede = $1 AND a.id = $2`.
- Si el JOIN termina con `57014`, se consulta **solo** el fragmento académico y se responde `200` con `"completo": false`, `"contacto": null` y `"fragmentos_no_disponibles": ["estudiantes_contacto"]`.
- Si tampoco responde la parte académica, `503`. Si no existe, `404`.

```json
{
  "completo": true,
  "fragmentos_no_disponibles": [],
  "academico": { "sede": "tunja", "id": "…", "nombre": "…", "programa": "Medicina", "semestre": 3 },
  "contacto":  { "correo": "laura.gomez.tunja001@uptc.edu.co", "telefono": "3001234567", "direccion": "Calle 2 # 3-11, Tunja" },
  "evidencia": { "...": "ver 8.1" }
}
```

**`POST /api/estudiantes`**

Cuerpo:

```json
{ "sede": "aguazul", "nombre": "Ana Pérez", "programa": "Ingeniería Ambiental", "semestre": 2,
  "correo": "ana.perez@uptc.edu.co", "telefono": "3104567890", "direccion": "Calle 5 # 4-20, Aguazul" }
```

- El backend valida lo mismo que la base de datos (sección 3) y convierte `correo` a minúsculas.
- Genera el `id` (`uuid4`) y ejecuta ambos `INSERT` en **una** transacción, con reintentos ante `40001`.
- Responde `201` con el registro completo y la `evidencia` de dónde quedó cada fragmento.

### 8.3 Errores

Formato único para todo lo que no sea 2xx:

```json
{ "error": { "codigo": "FRAGMENTO_NO_DISPONIBLE", "mensaje": "El fragmento estudiantes_contacto (tunja-central) no respondió en 5s. La transacción se revirtió.", "sqlstate": "57014", "fragmentos": ["estudiantes_contacto"] } }
```

| HTTP | `codigo` | Cuándo |
|---|---|---|
| 422 | `DATOS_INVALIDOS` | Falla la validación del backend o un `CHECK` de la base (`23514`) |
| 404 | `NO_ENCONTRADO` | El estudiante no existe |
| 409 | `CORREO_DUPLICADO` | Violación de `uq_contacto_correo` (`23505`) |
| 503 | `FRAGMENTO_NO_DISPONIBLE` | Tiempo límite (`57014`): el nodo dueño del fragmento está apagado. Un registro **nunca** queda a medias |
| 503 | `SIN_CONEXION` | Ningún host de `DATABASE_URL` acepta conexiones |

### 8.4 `GET /api/validaciones`

Ejecuta, en orden, `db/validacion/01_ubicacion.sql`, `02_horizontal.sql` y `03_vertical.sql`. Las tres devuelven las columnas `prueba, detalle, ok`. La respuesta es:

```json
{
  "ok": true,
  "resultados": [
    { "prueba": "horizontal", "detalle": "particion tunja: filas SQL=40, claves en su rango=40", "ok": true }
  ]
}
```

Si un archivo termina con tiempo límite (por ejemplo, `02` o `03` con un nodo caído), se agrega una fila con `ok: false` y el error, y se continúa con los demás. Las pruebas de caída (5 y 6) **no** se lanzan desde la API, porque apagan contenedores. Se hacen en la terminal y la interfaz las refleja mediante `/api/nodos` y `/api/distribucion`.

## 9. Validación y comportamiento ante fallos

### 9.1 Criterios (implementados en `scripts/validar.sh`, todos en verde)

1. **Ubicación exclusiva.** Cada rango con datos está **solo** en el nodo previsto, incluidos los índices.
2. **Horizontal.** Por cada sede, las filas de SQL coinciden con las claves de su rango. Total = suma de particiones, 0 claves fuera de las particiones y 0 `id` repetidos entre sedes.
3. **Vertical.** Columnas compartidas = `{sede, id}` (disyunción). Unión = las 8 columnas (completitud). Académicos = contactos = filas del JOIN, sin huérfanos. Un registro conocido se reconstruye **campo por campo**.
4. **Atomicidad.** Si el `INSERT` de contacto falla por un `CHECK`, tampoco queda la parte académica.
5. **Caída de `tunja-central`.** Las consultas académicas de todas las sedes responden. Contacto, JOIN y registro fallan con `57014` en 5 s, y el registro fallido no deja la parte académica. La ubicación sigue visible desde los metadatos.
6. **Reinicio de `tunja-central`.** Los contactos vuelven en unos 2–7 s y la validación vertical vuelve a pasar.

### 9.2 Qué debe mostrar la app cuando cae un nodo

| Nodo apagado | Consultas académicas | Contacto, detalle y registro |
|---|---|---|
| `tunja-central` | Funcionan todas las sedes | Contacto no disponible. El detalle sale parcial (`completo: false`) y el registro da 503 sin dejar datos a medias |
| `<sede>-academico` | Esa sede da 503 (o queda en `sedes_no_disponibles`). Las demás funcionan | El detalle y el registro de esa sede dan 503. Las demás sedes funcionan |
| Nodo de conexión del backend | El pool se conecta a otro host y todo sigue igual, salvo los fragmentos que guardaba ese nodo | Igual. Cambiar de conexión **no** recupera fragmentos |
