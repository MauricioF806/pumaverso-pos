"""Migraciones del esquema de la base de datos local.

Cada migración tiene un número de versión consecutivo y un script SQL. La
versión aplicada se guarda en ``PRAGMA user_version`` dentro del propio archivo
de la base de datos.

Reglas para agregar una migración:

1. Agregar una entrada nueva al final de ``MIGRACIONES`` con el siguiente número.
2. Nunca modificar una migración que ya se integró a ``main``: las sucursales
   que ya la aplicaron no la vuelven a ejecutar.
"""

import sqlite3

from pumaverso_tienda.persistencia.transacciones import transaccion

_0001_REGISTRO_DE_EVENTOS = """
CREATE TABLE eventos (
    sucursal_id      TEXT    NOT NULL,
    caja_id          TEXT    NOT NULL,
    consecutivo      INTEGER NOT NULL CHECK (consecutivo > 0),
    tipo             TEXT    NOT NULL,
    version_esquema  INTEGER NOT NULL CHECK (version_esquema > 0),
    contenido        TEXT    NOT NULL CHECK (json_valid(contenido)),
    usuario_id       TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    hash             TEXT    NOT NULL,
    PRIMARY KEY (sucursal_id, caja_id, consecutivo)
) STRICT;

-- Append-only obligado por la base de datos (decisión 0005).
CREATE TRIGGER eventos_no_se_modifican
BEFORE UPDATE ON eventos
BEGIN
    SELECT RAISE(ABORT, 'El registro de eventos es append-only: no se puede modificar un evento');
END;

CREATE TRIGGER eventos_no_se_borran
BEFORE DELETE ON eventos
BEGIN
    SELECT RAISE(ABORT, 'El registro de eventos es append-only: no se puede borrar un evento');
END;

-- Qué eventos ya confirmó central. Vive aparte para no modificar los eventos.
CREATE TABLE estado_sincronizacion (
    sucursal_id                    TEXT    NOT NULL,
    caja_id                        TEXT    NOT NULL,
    ultimo_consecutivo_confirmado  INTEGER NOT NULL CHECK (ultimo_consecutivo_confirmado >= 0),
    actualizado_en                 TEXT    NOT NULL,
    PRIMARY KEY (sucursal_id, caja_id)
) STRICT;
"""

_0002_CATALOGO_Y_USUARIOS = """
CREATE TABLE usuarios_locales (
    usuario_id     TEXT    PRIMARY KEY,
    nombre         TEXT    NOT NULL,
    rol            TEXT    NOT NULL,
    hash_pin       TEXT    NOT NULL,
    activo         INTEGER NOT NULL CHECK (activo IN (0, 1)),
    actualizado_en TEXT    NOT NULL
) STRICT;

CREATE TABLE productos_catalogo (
    sku                   TEXT    PRIMARY KEY,
    nombre                TEXT    NOT NULL,
    precio_centavos       INTEGER NOT NULL CHECK (precio_centavos >= 0),
    precio_actualizado_en TEXT    NOT NULL,
    costo_centavos        INTEGER NOT NULL CHECK (costo_centavos >= 0),
    costo_actualizado_en  TEXT    NOT NULL,
    activo                INTEGER NOT NULL CHECK (activo IN (0, 1))
) STRICT;
"""

_0003_INVENTARIO_Y_POS = """
CREATE TABLE existencias_locales (
    sku            TEXT    PRIMARY KEY REFERENCES productos_catalogo(sku),
    cantidad       INTEGER NOT NULL,
    actualizado_en TEXT    NOT NULL
) STRICT;

CREATE TABLE alertas_inventario (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sucursal_id    TEXT    NOT NULL,
    caja_id        TEXT    NOT NULL,
    consecutivo    INTEGER NOT NULL,
    tipo_alerta    TEXT    NOT NULL,
    detalle        TEXT    NOT NULL,
    creada_en      TEXT    NOT NULL
) STRICT;

CREATE TABLE ventas_canceladas (
    sucursal_id          TEXT    NOT NULL,
    caja_id              TEXT    NOT NULL,
    consecutivo_original INTEGER NOT NULL,
    consecutivo_evento   INTEGER NOT NULL,
    cancelada_en         TEXT    NOT NULL,
    PRIMARY KEY (sucursal_id, caja_id, consecutivo_original)
) STRICT;
"""

# Decisión 0006: la vigencia offline se cuenta desde que la tienda recibe los
# datos, y las operaciones se hacen con una sesión iniciada con PIN.
_0004_SESIONES_Y_ULTIMA_BAJADA = """
CREATE TABLE estado_bajada (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    ultima_bajada_en  TEXT    NOT NULL
) STRICT;

CREATE TABLE sesiones (
    token_hash  TEXT PRIMARY KEY,
    usuario_id  TEXT NOT NULL,
    creada_en   TEXT NOT NULL,
    expira_en   TEXT NOT NULL
) STRICT;
"""

MIGRACIONES: tuple[tuple[int, str], ...] = (
    (1, _0001_REGISTRO_DE_EVENTOS),
    (2, _0002_CATALOGO_Y_USUARIOS),
    (3, _0003_INVENTARIO_Y_POS),
    (4, _0004_SESIONES_Y_ULTIMA_BAJADA),
)


class MigracionError(RuntimeError):
    """El esquema de la base de datos no se puede llevar a la versión esperada."""


def version_actual(conexion: sqlite3.Connection) -> int:
    """Devuelve la versión del esquema aplicada en la base de datos."""
    return conexion.execute("PRAGMA user_version").fetchone()[0]


def aplicar_migraciones(conexion: sqlite3.Connection) -> int:
    """Aplica en orden las migraciones pendientes y devuelve la versión final.

    Cada migración se aplica en su propia transacción: si falla, la base de
    datos queda en la versión anterior. Ejecutarla de nuevo no repite nada.
    """
    _validar_numeracion()
    actual = version_actual(conexion)
    ultima = MIGRACIONES[-1][0] if MIGRACIONES else 0

    if actual > ultima:
        raise MigracionError(
            f"La base de datos está en la versión {actual}, más nueva que la "
            f"versión {ultima} que conoce esta aplicación."
        )

    for version, sql in MIGRACIONES:
        if version <= actual:
            continue
        with transaccion(conexion):
            conexion.executescript(sql)
            conexion.execute(f"PRAGMA user_version = {version}")

    return version_actual(conexion)


def _validar_numeracion() -> None:
    versiones = [version for version, _ in MIGRACIONES]
    if versiones != list(range(1, len(versiones) + 1)):
        raise MigracionError(
            f"Las migraciones deben numerarse 1, 2, 3… sin huecos; se encontró {versiones}."
        )
