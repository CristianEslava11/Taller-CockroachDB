-- 01_ubicacion.sql
-- Prueba 1: cada fragmento está exclusivamente en el nodo previsto.
-- Revisa todos los rangos de ambas tablas (clave primaria e índices) y compara la localidad
-- real de sus réplicas con la esperada.
--   * Rango de la partición <sede> de estudiantes_academicos -> {sede=<sede>,rol=academico}
--   * Rango sin partición de estudiantes_academicos           -> una réplica en un nodo académico
--   * Cualquier rango de estudiantes_contacto                 -> {sede=tunja,rol=central}
-- No usa WITH DETAILS: la ubicación sale de los metadatos replicados del clúster, por lo que
-- esta consulta responde aunque un nodo esté apagado (la de 02_horizontal.sql cuenta las claves
-- que hay en los rangos sin partición).
-- Columnas de salida: prueba, detalle, ok (formato común de todas las validaciones).
WITH rangos AS (
    SELECT 'estudiantes_academicos' AS tabla, index_name, range_id, start_key, replicas,
           replica_localities
    FROM [SHOW RANGES FROM TABLE estudiantes_academicos WITH INDEXES]
    UNION ALL
    SELECT 'estudiantes_contacto', index_name, range_id, start_key, replicas, replica_localities
    FROM [SHOW RANGES FROM TABLE estudiantes_contacto WITH INDEXES]
),
esperado AS (
    SELECT *,
           CASE
               WHEN tabla = 'estudiantes_contacto' THEN 'sede=tunja,rol=central'
               WHEN start_key NOT LIKE '%PrefixEnd%' AND substring(start_key FROM '/"([a-z]+)"') IS NOT NULL
                   THEN 'sede=' || substring(start_key FROM '/"([a-z]+)"') || ',rol=academico'
           END AS localidad_esperada
    FROM rangos
)
SELECT 'ubicacion' AS prueba,
       tabla || '@' || index_name || ' r' || range_id::STRING || ' ' || start_key
           || ' -> ' || array_to_string(replica_localities, ' | ')
           || ' (esperado: ' || COALESCE(localidad_esperada, 'nodo academico, rango sin particion') || ')'
           AS detalle,
       CASE
           WHEN localidad_esperada IS NOT NULL
               THEN replica_localities = ARRAY[localidad_esperada]
           ELSE array_length(replicas, 1) = 1
                AND replica_localities[1] LIKE '%rol=academico'
       END AS ok
FROM esperado
ORDER BY tabla, index_name, start_key;
