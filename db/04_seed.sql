-- 04_seed.sql
-- Datos ficticios: 100 estudiantes (Tunja 40, Duitama 25, Sogamoso 25, Aguazul 10).
-- Cada estudiante se inserta en ambos fragmentos verticales dentro de la misma transacción.
USE uptc;

BEGIN;

INSERT INTO estudiantes_academicos (sede, nombre, programa, semestre)
SELECT s.sede,
       (ARRAY['Laura','Andrés','Camila','Santiago','Valentina','Juan','María','Felipe',
              'Daniela','Sebastián','Paula','Nicolás','Natalia','Diego','Sofía','Julián'])[1 + (g.n * 7 + s.k) % 16]
       || ' ' ||
       (ARRAY['Rodríguez','Gómez','Pérez','Martínez','López','Díaz','Sánchez','Rojas',
              'Moreno','Castro','Vargas','Suárez','Medina','Becerra','Niño','Cárdenas'])[1 + (g.n * 11 + s.k * 3) % 16]
       || ' ' ||
       (ARRAY['Rodríguez','Gómez','Pérez','Martínez','López','Díaz','Sánchez','Rojas',
              'Moreno','Castro','Vargas','Suárez','Medina','Becerra','Niño','Cárdenas'])[1 + (g.n * 5 + s.k) % 16],
       s.programas[1 + g.n % array_length(s.programas, 1)],
       (1 + (g.n * 3 + s.k) % 10)::INT2
FROM (VALUES
        ('tunja',    40, 1, ARRAY['Ingeniería de Sistemas y Computación', 'Medicina', 'Derecho',
                              'Licenciatura en Matemáticas', 'Ingeniería Civil', 'Enfermería']::STRING[]),
        ('duitama',  25, 2, ARRAY['Ingeniería Electromecánica', 'Administración Turística y Hotelera',
                              'Diseño Industrial', 'Licenciatura en Tecnología']::STRING[]),
        ('sogamoso', 25, 3, ARRAY['Ingeniería Geológica', 'Ingeniería de Minas', 'Ingeniería Industrial',
                              'Ingeniería Electrónica', 'Contaduría Pública']::STRING[]),
        ('aguazul',  10, 4, ARRAY['Administración de Empresas', 'Ingeniería Ambiental',
                              'Tecnología en Gestión Agropecuaria']::STRING[])
     ) AS s (sede, total, k, programas),
     generate_series(1, 40) AS g (n)
WHERE g.n <= s.total;

INSERT INTO estudiantes_contacto (sede, id, correo, telefono, direccion)
SELECT t.sede,
       t.id,
       regexp_replace(
           translate(lower(split_part(t.nombre, ' ', 1) || '.' || split_part(t.nombre, ' ', 2)),
                     'áéíóúñü', 'aeiounu'),
           '[^a-z.]', '', 'g')
         || '.' || t.sede || lpad(t.rn::STRING, 3, '0') || '@uptc.edu.co',
       '3' || lpad(((t.rn * 7919 + length(t.sede) * 104729) % 1000000000)::STRING, 9, '0'),
       'Calle ' || (t.rn % 60 + 1)::STRING || ' # ' || (t.rn % 30 + 2)::STRING || '-'
         || (t.rn % 90 + 10)::STRING || ', ' || initcap(t.sede)
FROM (
    SELECT sede, id, nombre, row_number() OVER (PARTITION BY sede ORDER BY id) AS rn
    FROM estudiantes_academicos
) AS t;

COMMIT;
