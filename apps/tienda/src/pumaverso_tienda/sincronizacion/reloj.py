"""Última sincronización con central y revisión del reloj local.

El PRD pide bloquear la operación si el reloj marca una fecha anterior a la
última sincronización: así se detecta un reloj que se atrasó (o que alguien
atrasó para extender la vigencia de credenciales y precios).

Cuenta como sincronización tanto una descarga exitosa de usuarios y catálogo
como una confirmación de eventos por parte de central.
"""

import sqlite3
from datetime import UTC, datetime


class RelojAtrasadoError(RuntimeError):
    """El reloj local es anterior a la última sincronización con central."""


def registrar_bajada(conexion: sqlite3.Connection, momento: datetime) -> None:
    """Guarda el momento de la última descarga exitosa desde central."""
    conexion.execute(
        """
        INSERT INTO estado_bajada (id, ultima_bajada_en) VALUES (1, ?)
        ON CONFLICT (id) DO UPDATE SET ultima_bajada_en = excluded.ultima_bajada_en
        """,
        (_formatear(momento),),
    )


def ultima_bajada(conexion: sqlite3.Connection) -> datetime | None:
    """Momento de la última descarga exitosa desde central, o ``None`` si nunca hubo."""
    fila = conexion.execute("SELECT ultima_bajada_en FROM estado_bajada WHERE id = 1").fetchone()
    return datetime.fromisoformat(fila[0]) if fila else None


def ultima_sincronizacion(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str
) -> datetime | None:
    """La más reciente entre la última descarga y la última confirmación de eventos."""
    momentos = [
        m
        for m in (ultima_bajada(conexion), _ultima_confirmacion(conexion, sucursal_id, caja_id))
        if m
    ]
    return max(momentos) if momentos else None


def validar_reloj_local(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str, referencia: datetime
) -> None:
    """Lanza ``RelojAtrasadoError`` si ``referencia`` es anterior a la última sincronización."""
    ultima = ultima_sincronizacion(conexion, sucursal_id, caja_id)
    if ultima is not None and referencia < ultima:
        raise RelojAtrasadoError(
            "El reloj local marca una fecha anterior a la última sincronización con "
            f"central ({ultima.isoformat()}). Corrige la fecha y hora del equipo."
        )


def _ultima_confirmacion(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str
) -> datetime | None:
    fila = conexion.execute(
        """
        SELECT actualizado_en FROM estado_sincronizacion
        WHERE sucursal_id = ? AND caja_id = ?
        """,
        (sucursal_id, caja_id),
    ).fetchone()
    return datetime.fromisoformat(fila[0]) if fila else None


def _formatear(momento: datetime) -> str:
    if momento.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria.")
    return momento.astimezone(UTC).isoformat(timespec="milliseconds")
