-- 03_vertical.sql
-- Fragmentación vertical: las columnas de contacto viven en una tabla aparte,
-- fijada al nodo tunja-central con una sola réplica.
-- La regla de zona de la tabla se aplica también a todos sus índices (incluido uq_contacto_correo).
USE uptc;

ALTER TABLE estudiantes_contacto CONFIGURE ZONE USING
    num_replicas = 1,
    constraints = '[+sede=tunja, +rol=central]';
