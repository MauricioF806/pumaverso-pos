"""Qué eventos faltan por enviar a central.

El avance de la sincronización se guarda en la tabla ``estado_sincronizacion``
y no en los eventos, porque los eventos nunca se modifican (decisión 0005).

Central confirma por consecutivo: confirmar el 15 significa que recibió del 1
al 15 sin huecos. Por eso basta con guardar el último consecutivo confirmado.
"""

import sqlite3
from datetime import UTC, datetime

from pumaverso_tienda.eventos.registro import Evento, listar_eventos, ultimo_consecutivo
from pumaverso_tienda.persistencia.transacciones import transaccion


class ConfirmacionInvalidaError(ValueError):
    """Central confirmó un consecutivo que no tiene sentido para esta caja."""


def ultimo_consecutivo_confirmado(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str
) -> int:
    """Último consecutivo que central confirmó haber recibido; 0 si ninguno."""
    fila = conexion.execute(
        """
        SELECT ultimo_consecutivo_confirmado FROM estado_sincronizacion
        WHERE sucursal_id = ? AND caja_id = ?
        """,
        (sucursal_id, caja_id),
    ).fetchone()
    return fila[0] if fila else 0


def eventos_pendientes(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str, *, limite: int = 100
) -> list[Evento]:
    """Eventos que central todavía no confirma, en orden de consecutivo."""
    desde = ultimo_consecutivo_confirmado(conexion, sucursal_id, caja_id) + 1
    return listar_eventos(conexion, sucursal_id, caja_id, desde_consecutivo=desde, limite=limite)


def confirmar_hasta(
    conexion: sqlite3.Connection,
    sucursal_id: str,
    caja_id: str,
    consecutivo: int,
    *,
    ahora: datetime | None = None,
) -> None:
    """Registra que central recibió todos los eventos hasta ``consecutivo``.

    Es idempotente: confirmar dos veces el mismo número no cambia nada. La
    confirmación nunca retrocede ni puede pasar del último evento registrado.
    """
    if conexion.in_transaction:
        _confirmar(conexion, sucursal_id, caja_id, consecutivo, ahora)
    else:
        with transaccion(conexion):
            _confirmar(conexion, sucursal_id, caja_id, consecutivo, ahora)


def _confirmar(
    conexion: sqlite3.Connection,
    sucursal_id: str,
    caja_id: str,
    consecutivo: int,
    ahora: datetime | None,
) -> None:
    actual = ultimo_consecutivo_confirmado(conexion, sucursal_id, caja_id)
    ultimo = ultimo_consecutivo(conexion, sucursal_id, caja_id)

    if consecutivo < actual:
        raise ConfirmacionInvalidaError(
            f"La confirmación no puede retroceder: ya estaba en {actual} y llegó {consecutivo}."
        )
    if consecutivo > ultimo:
        raise ConfirmacionInvalidaError(
            f"Se confirmó el consecutivo {consecutivo}, pero el último registrado es {ultimo}."
        )
    if consecutivo == actual:
        return

    momento = (ahora or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="milliseconds")
    conexion.execute(
        """
        INSERT INTO estado_sincronizacion (
            sucursal_id, caja_id, ultimo_consecutivo_confirmado, actualizado_en
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT (sucursal_id, caja_id) DO UPDATE SET
            ultimo_consecutivo_confirmado = excluded.ultimo_consecutivo_confirmado,
            actualizado_en = excluded.actualizado_en
        """,
        (sucursal_id, caja_id, consecutivo, momento),
    )
