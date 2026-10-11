-- 02_horizontal.sql
-- Fragmentación horizontal de estudiantes_academicos por sede.
-- Cada partición (filas de una sede) se fija a un único nodo académico, con una sola réplica,
-- para que la ubicación sea exclusiva y demostrable.
USE uptc;

-- La clave primaria (sede, id) y el índice (sede, programa) empiezan por sede,
-- así que ambos se pueden particionar por lista de sedes.
ALTER TABLE estudiantes_academicos PARTITION BY LIST (sede) (
    PARTITION tunja    VALUES IN ('tunja'),
    PARTITION duitama  VALUES IN ('duitama'),
    PARTITION sogamoso VALUES IN ('sogamoso'),
    PARTITION aguazul  VALUES IN ('aguazul')
);

ALTER INDEX estudiantes_academicos@idx_academicos_programa PARTITION BY LIST (sede) (
    PARTITION tunja    VALUES IN ('tunja'),
    PARTITION duitama  VALUES IN ('duitama'),
    PARTITION sogamoso VALUES IN ('sogamoso'),
    PARTITION aguazul  VALUES IN ('aguazul')
);

-- Zona de la tabla: rangos sin filas (fuera de las particiones) quedan en algún nodo académico,
-- nunca en tunja-central.
ALTER TABLE estudiantes_academicos CONFIGURE ZONE USING
    num_replicas = 1,
    constraints = '[+rol=academico]';

-- "OF INDEX tabla@*" aplica la regla a la partición homónima de la clave primaria y de cada índice.
ALTER PARTITION tunja OF INDEX estudiantes_academicos@* CONFIGURE ZONE USING
    num_replicas = 1,
    constraints = '[+sede=tunja, +rol=academico]';

ALTER PARTITION duitama OF INDEX estudiantes_academicos@* CONFIGURE ZONE USING
    num_replicas = 1,
    constraints = '[+sede=duitama, +rol=academico]';

ALTER PARTITION sogamoso OF INDEX estudiantes_academicos@* CONFIGURE ZONE USING
    num_replicas = 1,
    constraints = '[+sede=sogamoso, +rol=academico]';

ALTER PARTITION aguazul OF INDEX estudiantes_academicos@* CONFIGURE ZONE USING
    num_replicas = 1,
    constraints = '[+sede=aguazul, +rol=academico]';
