-- 03_vertical.sql
-- Prueba 3: los fragmentos verticales cumplen completitud, disyunción y reconstrucción.
-- Columnas de salida: prueba, detalle, ok.
WITH columnas AS (
    SELECT table_name, column_name
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN ('estudiantes_academicos', 'estudiantes_contacto')
),
comunes AS (
    SELECT COALESCE(string_agg(column_name, ',' ORDER BY column_name), '') AS lista
    FROM (SELECT column_name FROM columnas GROUP BY column_name
          HAVING count(DISTINCT table_name) = 2)
),
todas AS (
    SELECT string_agg(column_name, ',' ORDER BY column_name) AS lista
    FROM (SELECT DISTINCT column_name FROM columnas)
),
conteos AS (
    SELECT (SELECT count(*) FROM estudiantes_academicos)::INT AS academicos,
           (SELECT count(*) FROM estudiantes_contacto)::INT   AS contactos,
           (SELECT count(*) FROM estudiantes_academicos a
                JOIN estudiantes_contacto c USING (sede, id))::INT AS reconstruidos,
           (SELECT count(*) FROM estudiantes_academicos a
                WHERE NOT EXISTS (SELECT 1 FROM estudiantes_contacto c
                                  WHERE c.sede = a.sede AND c.id = a.id))::INT AS sin_contacto,
           (SELECT count(*) FROM estudiantes_contacto c
                WHERE NOT EXISTS (SELECT 1 FROM estudiantes_academicos a
                                  WHERE a.sede = c.sede AND a.id = c.id))::INT AS huerfanos
)
SELECT 'vertical' AS prueba,
       'disyuncion: columnas compartidas=' || lista || ' (esperado: id,sede)' AS detalle,
       lista = 'id,sede' AS ok
FROM comunes
UNION ALL
SELECT 'vertical',
       'completitud: columnas=' || lista,
       lista = 'correo,direccion,id,nombre,programa,sede,semestre,telefono'
FROM todas
UNION ALL
SELECT 'vertical',
       'reconstruccion: academicos=' || academicos::STRING || ', contactos=' || contactos::STRING
           || ', JOIN=' || reconstruidos::STRING,
       academicos = contactos AND contactos = reconstruidos
FROM conteos
UNION ALL
SELECT 'vertical', 'estudiantes sin contacto=' || sin_contacto::STRING, sin_contacto = 0 FROM conteos
UNION ALL
SELECT 'vertical', 'contactos huerfanos=' || huerfanos::STRING, huerfanos = 0 FROM conteos;
