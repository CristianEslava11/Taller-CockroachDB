-- 01_schema.sql
-- Esquema lógico del directorio de estudiantes UPTC.
-- La tabla lógica "estudiante" se divide en dos fragmentos verticales:
--   estudiantes_academicos : datos académicos (fragmentado además horizontalmente por sede)
--   estudiantes_contacto   : datos de contacto (todas las sedes, nodo tunja-central)
-- Ambos fragmentos comparten la clave (sede, id), que permite reconstruir el registro con un JOIN.

CREATE DATABASE IF NOT EXISTS uptc;
USE uptc;

-- Fragmento vertical 1: datos académicos.
CREATE TABLE estudiantes_academicos (
    sede     STRING      NOT NULL,
    id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    nombre   STRING      NOT NULL,
    programa STRING      NOT NULL,
    semestre INT2        NOT NULL,
    CONSTRAINT pk_estudiantes_academicos PRIMARY KEY (sede, id),
    CONSTRAINT ck_academicos_sede     CHECK (sede IN ('tunja', 'duitama', 'sogamoso', 'aguazul')),
    CONSTRAINT ck_academicos_nombre   CHECK (length(nombre) BETWEEN 3 AND 120),
    CONSTRAINT ck_academicos_programa CHECK (length(programa) BETWEEN 3 AND 120),
    CONSTRAINT ck_academicos_semestre CHECK (semestre BETWEEN 1 AND 12)
);

-- Índice secundario para filtrar por programa dentro de una sede.
-- También se particiona por sede (ver 02_horizontal.sql) para que viva junto a sus filas.
CREATE INDEX idx_academicos_programa ON estudiantes_academicos (sede, programa);

-- Fragmento vertical 2: datos de contacto.
CREATE TABLE estudiantes_contacto (
    sede      STRING NOT NULL,
    id        UUID   NOT NULL,
    correo    STRING NOT NULL,
    telefono  STRING NOT NULL,
    direccion STRING NOT NULL,
    CONSTRAINT pk_estudiantes_contacto PRIMARY KEY (sede, id),
    CONSTRAINT uq_contacto_correo UNIQUE (correo),
    CONSTRAINT ck_contacto_correo    CHECK (correo ~ '^[a-z0-9._-]+@[a-z0-9.-]+\.[a-z]{2,}$'),
    CONSTRAINT ck_contacto_telefono  CHECK (telefono ~ '^3[0-9]{9}$'),
    CONSTRAINT ck_contacto_direccion CHECK (length(direccion) BETWEEN 5 AND 200),
    -- Todo contacto pertenece a un estudiante existente (sin contactos huérfanos).
    CONSTRAINT fk_contacto_academico FOREIGN KEY (sede, id)
        REFERENCES estudiantes_academicos (sede, id) ON DELETE CASCADE
);
