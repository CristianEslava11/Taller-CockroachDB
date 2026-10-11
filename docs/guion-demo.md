# Guion de la demostración (≈ 10 minutos)

## Antes de presentar (el mismo día)

1. Abrir **Docker Desktop** y esperar a que esté corriendo.
2. Recrear el clúster. Esto reinicia los 7 días sin licencia y deja los datos limpios (≈ 1 min):

   ```bash
   docker compose down -v
   docker compose up -d --build
   ```

3. Comprobar que todo está bien: `bash scripts/validar.sh --sin-fallos`. Debe terminar con "todas las pruebas pasaron".
4. Dejar abiertas:
   - **Pestaña 1:** la app, http://localhost:8000
   - **Pestaña 2:** la DB Console, http://localhost:8080, en la vista de nodos (*Overview*)
   - **Terminal** (Git Bash) en la carpeta del proyecto
5. Tener a mano un estudiante para registrar en vivo, por ejemplo: Aguazul · "Ana Pérez Rojas" · Ingeniería Ambiental · semestre 2 · `ana.perez@uptc.edu.co` · `3104567890` · "Calle 5 # 4-20, Aguazul".

---

## 1. Introducción · 1 min

**Decir:** "Diseñamos un directorio de estudiantes de la UPTC con cuatro sedes y lo fragmentamos de dos formas. **Horizontalmente**, cada sede guarda las filas de sus estudiantes. **Verticalmente**, los datos de contacto se separan de los académicos y van a un servidor central en Tunja. Usamos CockroachDB con cinco nodos."

**Mostrar:** el diagrama de arquitectura del informe (sección 4).

## 2. Diseño · 1,5 min

**Mostrar** `db/02_horizontal.sql` y `db/03_vertical.sql`.

**Decir:**
- "`PARTITION BY LIST (sede)` define los fragmentos horizontales. La clave primaria empieza por `sede`, y por eso las filas de una sede quedan juntas."
- "`CONFIGURE ZONE ... constraints` fija cada fragmento al nodo con esa localidad. Usamos **una sola réplica** para que cada fragmento exista solo en su nodo y se pueda demostrar."
- "La tabla de contacto repite la clave `(sede, id)`. Así se reconstruye el registro con un JOIN."

## 3. El mapa del clúster · 1 min

**App → Distribución.** Mostrar los 5 nodos disponibles y cuántos registros tiene cada uno (Tunja 40, Duitama 25, Sogamoso 25, Aguazul 10, Central 100 contactos).

**App → Fragmentos.** Cada fragmento aparece con su **nodo esperado** y su **nodo real**, consultados al clúster con `SHOW RANGES`.

**DB Console (pestaña 2).** Los mismos 5 nodos con sus localidades. Esto confirma que son servidores distintos con su propio almacenamiento.

## 4. Fragmentación horizontal · 1,5 min

**App → Estudiantes.** Elegir la sede **Sogamoso**, marcar **"Incluir EXPLAIN ANALYZE"** y pulsar **Consultar**. Bajo la tabla aparece, por ejemplo: **"Conexión: Duitama (n1) · Datos leídos en: Sogamoso (n4)"**. Los números de nodo cambian cada vez que se recrea el clúster.

**Decir:** "El plan de ejecución real muestra dos cosas distintas. El **nodo de conexión** es el que recibió la consulta, y puede ser cualquiera. Los datos los leyó **solo el nodo de Sogamoso**: la consulta tocó una sola partición."

Si preguntan por el detalle, abrir **"Ver plan ejecutado"** (líneas `sql nodes` / `kv nodes`).

Si da tiempo:
- Cambiar a **Tunja** y mostrar que cambia el nodo que lee los datos.
- Elegir **"Todas las sedes"** y mostrar que aparece un plan por sede, cada uno leído en su propio nodo.

## 5. Fragmentación vertical y registro · 2 min

**App → Registrar.** Registrar al estudiante preparado (Aguazul).

**Decir:** "Un solo registro escribe en **dos servidores**: los datos académicos van a Aguazul y el contacto a Central. Las dos escrituras van en **una transacción distribuida**: o quedan las dos o ninguna."

Mostrar la evidencia de dónde quedó cada parte.

**App → Fragmentos (reconstrucción).** Reconstruir a ese estudiante y mostrar V1 (académico) + V2 (contacto) = el registro completo.

## 6. Caída de un servidor · 2 min ★

**Terminal:**

```bash
docker compose stop tunja-central
```

Esperar unos segundos a que la app actualice el estado (lo hace cada 8 s, o con el botón **Actualizar**). Central aparece como no disponible.

1. **Estudiantes:** el listado de todas las sedes **sigue funcionando**. "Los datos académicos no están en Central."
2. **Reconstruir** al estudiante registrado: sale **parcial**, con los datos académicos y sin contacto. "Se perdieron solo las columnas que guarda ese servidor."
3. **Registrar** otro estudiante: falla tras 5 s con `FRAGMENTO_NO_DISPONIBLE`. "La transacción se revirtió: no quedó medio estudiante."
4. **Fragmentos:** el contacto sigue apareciendo **ubicado** en `tunja-central`, pero no disponible. "La ubicación sale de metadatos replicados; los datos no estaban replicados."

**Terminal:**

```bash
docker compose start tunja-central
```

Después de unos 5 s, volver a reconstruir: el registro sale completo otra vez.

## 7. Validación · 1 min

**App → Validación → Ejecutar validaciones.** Las 32 comprobaciones en verde: ubicación de cada rango, cobertura sin solapamiento y reglas de la fragmentación vertical.

Mencionar que `scripts/validar.sh` hace además las pruebas de atomicidad y de caída de forma automática.

## 8. Cierre · 30 s

**Decir:** "El sharding se resolvió en el motor, sin lógica de enrutamiento en la aplicación. La fragmentación aísla los fallos: al caer un nodo, solo se pierden sus filas o sus columnas. Como usamos una sola réplica, ese fragmento depende de su nodo. En producción se agregarían réplicas dentro de cada sede."

---

## Plan B (si algo falla durante la exposición)

- **La app no responde.** Ejecutar `docker compose up -d` (vuelve a levantar lo que falte). Si no basta, mostrar todo desde SQL:

  ```bash
  docker exec -it duitama-academico cockroach sql --insecure -d uptc
  ```

  ```sql
  SELECT index_name, start_key, replica_localities
    FROM [SHOW RANGES FROM TABLE estudiantes_academicos WITH INDEXES];
  SELECT index_name, replica_localities
    FROM [SHOW RANGES FROM TABLE estudiantes_contacto WITH INDEXES];
  EXPLAIN ANALYZE SELECT * FROM estudiantes_academicos WHERE sede = 'sogamoso';
  ```

- **http://localhost:8000 no abre después de reiniciar Docker Desktop o el computador.** A veces Docker no vuelve a publicar el puerto de la API (`docker ps` muestra `8000/tcp` sin `127.0.0.1:8000->`). Se arregla con `docker compose up -d --force-recreate --no-deps api`.
- **Hay que demostrar todo de una vez:** `bash scripts/validar.sh` (≈ 1 min). Ejecuta las 6 pruebas, incluida la caída y la recuperación de Central.
- **Central no vuelve:** `docker compose start tunja-central` y esperar unos 10 s.
- **No apagar dos nodos a la vez.** Algunos datos internos del clúster podrían perder quórum.

## Preguntas probables

| Pregunta | Respuesta corta |
|---|---|
| ¿Por qué una sola réplica? | Para que cada fragmento exista en un solo servidor y se pueda demostrar. En producción habría varias réplicas por sede (informe 5.3). |
| ¿Es sharding real si todo corre en un computador? | Cada nodo es un servidor CockroachDB independiente con su propio almacenamiento. La configuración es la misma con máquinas separadas: solo cambian las direcciones. |
| ¿Por qué `sede` como clave de shard? | Las consultas son por sede, y al ir como prefijo de la clave primaria agrupa físicamente las filas de cada sede. |
| ¿Qué pasa con una consulta sin `sede`? | Debe visitar todas las particiones. La app consulta sede por sede y marca como parcial lo que no responde. |
| ¿Cómo se garantiza que no quede medio estudiante? | Las dos escrituras van en una transacción distribuida (ACID). Se probó con un error de validación y con el nodo caído: en ambos casos, 0 filas a medias. |
| ¿Por qué no *column families* para lo vertical? | Separan columnas solo al guardarlas, dentro del mismo rango y del mismo nodo. No reparten columnas entre servidores. |
| ¿El índice secundario también se fragmenta? | Sí. Se particiona por sede igual que la tabla, y la prueba 1 verifica su ubicación. |
