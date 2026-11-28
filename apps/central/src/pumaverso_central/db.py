"""Conexión y esquema inicial en PostgreSQL para los servicios centrales."""

import psycopg
from psycopg.rows import dict_row

ESQUEMA_CENTRAL_SQL = """
CREATE SCHEMA IF NOT EXISTS sincronizacion;
CREATE SCHEMA IF NOT EXISTS usuarios;
CREATE SCHEMA IF NOT EXISTS catalogo;
CREATE SCHEMA IF NOT EXISTS inventario;

CREATE TABLE IF NOT EXISTS sincronizacion.estado_cajas (
    sucursal_id                    TEXT        NOT NULL,
    caja_id                        TEXT        NOT NULL,
    ultimo_consecutivo_confirmado  INTEGER     NOT NULL DEFAULT 0,
    actualizado_en                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sucursal_id, caja_id)
);

CREATE TABLE IF NOT EXISTS sincronizacion.eventos_recibidos (
    sucursal_id      TEXT        NOT NULL,
    caja_id          TEXT        NOT NULL,
    consecutivo      INTEGER     NOT NULL CHECK (consecutivo > 0),
    tipo             TEXT        NOT NULL,
    version_esquema  INTEGER     NOT NULL CHECK (version_esquema > 0),
    contenido        JSONB       NOT NULL,
    usuario_id       TEXT        NOT NULL,
    registrado_en    TIMESTAMPTZ NOT NULL,
    hash             TEXT        NOT NULL,
    recibido_en      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sucursal_id, caja_id, consecutivo)
);

CREATE TABLE IF NOT EXISTS sincronizacion.cola_eventos (
    id               BIGSERIAL   PRIMARY KEY,
    sucursal_id      TEXT        NOT NULL,
    caja_id          TEXT        NOT NULL,
    consecutivo      INTEGER     NOT NULL,
    tipo             TEXT        NOT NULL,
    contenido        JSONB       NOT NULL,
    usuario_id       TEXT        NOT NULL,
    registrado_en    TIMESTAMPTZ NOT NULL,
    procesado_en     TIMESTAMPTZ NULL,
    UNIQUE (sucursal_id, caja_id, consecutivo)
);

-- Eventos que fallaron al procesarse: se apartan para revisión y no frenan la cola.
ALTER TABLE sincronizacion.cola_eventos ADD COLUMN IF NOT EXISTS fallido_en TIMESTAMPTZ NULL;
ALTER TABLE sincronizacion.cola_eventos ADD COLUMN IF NOT EXISTS error TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_cola_eventos_pendientes
    ON sincronizacion.cola_eventos (id)
    WHERE procesado_en IS NULL;

CREATE TABLE IF NOT EXISTS usuarios.usuarios (
    usuario_id     TEXT        PRIMARY KEY,
    nombre         TEXT        NOT NULL,
    rol            TEXT        NOT NULL,
    hash_pin       TEXT        NOT NULL,
    activo         BOOLEAN     NOT NULL DEFAULT TRUE,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS catalogo.productos (
    sku                   TEXT        PRIMARY KEY,
    nombre                TEXT        NOT NULL,
    precio_centavos       INTEGER     NOT NULL CHECK (precio_centavos >= 0),
    precio_actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    costo_centavos        INTEGER     NOT NULL CHECK (costo_centavos >= 0),
    costo_actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activo                BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS inventario.existencias_sucursal (
    sucursal_id    TEXT        NOT NULL,
    sku            TEXT        NOT NULL,
    cantidad       INTEGER     NOT NULL DEFAULT 0,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sucursal_id, sku)
);

CREATE TABLE IF NOT EXISTS inventario.cola_auditoria (
    id             BIGSERIAL   PRIMARY KEY,
    sucursal_id    TEXT        NOT NULL,
    caja_id        TEXT        NOT NULL,
    consecutivo    INTEGER     NOT NULL,
    usuario_id     TEXT        NOT NULL,
    motivo         TEXT        NOT NULL,
    registrado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (sucursal_id, caja_id, consecutivo)
);

-- Detección de inventario fantasma (decisión 0009).
CREATE INDEX IF NOT EXISTS idx_eventos_recibidos_ventas
    ON sincronizacion.eventos_recibidos (sucursal_id, registrado_en)
    WHERE tipo = 'venta';

CREATE TABLE IF NOT EXISTS inventario.franjas_evaluadas (
    sucursal_id    TEXT        NOT NULL,
    franja_inicio  TIMESTAMPTZ NOT NULL,
    resultado      TEXT        NOT NULL,
    evaluada_en    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (sucursal_id, franja_inicio)
);

CREATE TABLE IF NOT EXISTS inventario.sospechas_fantasma (
    id                               BIGSERIAL        PRIMARY KEY,
    sucursal_id                      TEXT             NOT NULL,
    sku                              TEXT             NOT NULL,
    franja_inicio                    TIMESTAMPTZ      NOT NULL,
    franja_fin                       TIMESTAMPTZ      NOT NULL,
    unidades_esperadas               DOUBLE PRECISION NOT NULL,
    unidades_vendidas                INTEGER          NOT NULL,
    umbral_unidades                  INTEGER          NOT NULL,
    probabilidad                     DOUBLE PRECISION NOT NULL,
    dias_historia                    INTEGER          NOT NULL,
    existencia_registrada            INTEGER          NOT NULL,
    venta_perdida_estimada_centavos  INTEGER          NOT NULL DEFAULT 0,
    franjas_afectadas                INTEGER          NOT NULL DEFAULT 1,
    estado                           TEXT             NOT NULL
        CHECK (estado IN ('pendiente_conteo', 'escalada', 'resuelta')),
    detectada_en                     TIMESTAMPTZ      NOT NULL,
    escalada_en                      TIMESTAMPTZ      NULL,
    resuelta_en                      TIMESTAMPTZ      NULL,
    resolucion                       TEXT             NULL,
    cantidad_contada                 INTEGER          NULL,
    resuelta_por                     TEXT             NULL,
    UNIQUE (sucursal_id, sku, franja_inicio)
);

-- Nunca dos sospechas abiertas del mismo producto en la misma sucursal.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sospecha_abierta_unica
    ON inventario.sospechas_fantasma (sucursal_id, sku)
    WHERE estado <> 'resuelta';
"""


def abrir_conexion_central(database_url: str) -> psycopg.Connection:
    """Abre una conexión a PostgreSQL con filas tipo diccionario."""
    return psycopg.connect(database_url, row_factory=dict_row)


def inicializar_esquema_central(conn: psycopg.Connection) -> None:
    """Crea los esquemas y tablas de los servicios centrales si no existen."""
    with conn.transaction():
        conn.execute(ESQUEMA_CENTRAL_SQL)
