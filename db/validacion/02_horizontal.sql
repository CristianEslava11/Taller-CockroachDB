-- 02_horizontal.sql
-- Prueba 2: la fragmentación horizontal cubre todos los datos sin solapamientos.
-- Compara el conteo lógico (SQL) con el conteo físico (claves guardadas en el rango
-- de cada partición de la clave primaria).
-- Columnas de salida: prueba, detalle, ok.
WITH sedes (sede) AS (
    VALUES ('tunja'), ('duitama'), ('sogamoso'), ('aguazul')
),
logico AS (
    SELECT sede, count(*) AS filas FROM estudiantes_academicos GROUP BY sede
),
rangos_pk AS (
    SELECT CASE WHEN start_key NOT LIKE '%PrefixEnd%'
                THEN substring(start_key FROM '/"([a-z]+)"') END AS sede,
           (span_stats->>'live_count')::INT AS claves
    FROM [SHOW RANGES FROM TABLE estudiantes_academicos WITH DETAILS, INDEXES]
    WHERE index_name = 'pk_estudiantes_academicos'
),
fisico AS (
    SELECT sede, sum(claves)::INT AS claves FROM rangos_pk WHERE sede IS NOT NULL GROUP BY sede
),
totales AS (
    SELECT (SELECT count(*) FROM estudiantes_academicos)::INT                     AS total,
           (SELECT COALESCE(sum(claves), 0) FROM fisico)::INT                     AS en_particiones,
           (SELECT COALESCE(sum(claves), 0) FROM rangos_pk WHERE sede IS NULL)::INT AS fuera,
           (SELECT count(*) FROM (SELECT id FROM estudiantes_academicos
                                  GROUP BY id HAVING count(*) > 1))::INT          AS ids_repetidos
)
SELECT 'horizontal' AS prueba,
       'particion ' || s.sede || ': filas SQL=' || COALESCE(l.filas, 0)::STRING
           || ', claves en su rango=' || COALESCE(f.claves, 0)::STRING AS detalle,
       COALESCE(l.filas, 0) = COALESCE(f.claves, 0) AS ok
FROM sedes AS s
LEFT JOIN logico AS l USING (sede)
LEFT JOIN fisico AS f USING (sede)
UNION ALL
SELECT 'horizontal',
       'cobertura: total=' || total::STRING || ', suma de particiones=' || en_particiones::STRING,
       total = en_particiones
FROM totales
UNION ALL
SELECT 'horizontal',
       'claves fuera de toda particion=' || fuera::STRING,
       fuera = 0
FROM totales
UNION ALL
SELECT 'horizontal',
       'sin solapamiento: ids repetidos entre sedes=' || ids_repetidos::STRING,
       ids_repetidos = 0
FROM totales;
