"""Sesiones de caja: quién está operando, comprobado con su PIN (decisión 0006).

Al iniciar sesión con usuario y PIN, la tienda entrega un token. Cada operación
debe presentarlo; el usuario de la operación se toma de la sesión y no de lo que
diga la petición. Solo se guarda el hash SHA-256 del token, nunca el token.
"""

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pumaverso_tienda.persistencia.transacciones import transaccion
from pumaverso_tienda.usuarios.servicio import (
    UsuarioLocal,
    autenticar_usuario,
    validar_usuario_operativo,
)

# Lo que dura un turno. Además, la sesión deja de servir en cuanto el usuario
# se da de baja o vencen sus credenciales locales.
DURACION_SESION = timedelta(hours=12)


class SesionInvalidaError(PermissionError):
    """El token no existe, ya se cerró o expiró."""


@dataclass(frozen=True, slots=True)
class Sesion:
    """Sesión recién iniciada. ``token`` solo se conoce en este momento."""

    token: str
    usuario: UsuarioLocal
    expira_en: datetime


def iniciar_sesion(
    conexion: sqlite3.Connection,
    usuario_id: str,
    pin: str,
    *,
    ahora: datetime | None = None,
) -> Sesion:
    """Valida usuario y PIN y crea una sesión nueva."""
    momento = _asegurar_zona_horaria(ahora)
    usuario = autenticar_usuario(conexion, usuario_id, pin, ahora=momento)
    token = secrets.token_urlsafe(32)
    expira = momento + DURACION_SESION

    with transaccion(conexion):
        # Limpieza: las sesiones vencidas ya no sirven para nada.
        conexion.execute("DELETE FROM sesiones WHERE expira_en < ?", (_formatear(momento),))
        conexion.execute(
            "INSERT INTO sesiones (token_hash, usuario_id, creada_en, expira_en) VALUES (?, ?, ?, ?)",
            (_hash_token(token), usuario.usuario_id, _formatear(momento), _formatear(expira)),
        )
    return Sesion(token=token, usuario=usuario, expira_en=expira)


def usuario_de_sesion(
    conexion: sqlite3.Connection,
    token: str,
    *,
    ahora: datetime | None = None,
) -> UsuarioLocal:
    """Devuelve el usuario dueño de la sesión si sigue vigente y puede operar.

    Lanza ``SesionInvalidaError`` si el token no sirve, o los errores de
    ``validar_usuario_operativo`` si el usuario se dio de baja o venció.
    """
    momento = _asegurar_zona_horaria(ahora)
    fila = conexion.execute(
        "SELECT usuario_id, expira_en FROM sesiones WHERE token_hash = ?",
        (_hash_token(token),),
    ).fetchone()
    if fila is None or datetime.fromisoformat(fila["expira_en"]) <= momento:
        raise SesionInvalidaError("La sesión no existe o expiró; inicia sesión de nuevo.")
    return validar_usuario_operativo(conexion, fila["usuario_id"], ahora=momento)


def cerrar_sesion(conexion: sqlite3.Connection, token: str) -> None:
    """Invalida la sesión. Cerrar una sesión que no existe no es un error."""
    with transaccion(conexion):
        conexion.execute("DELETE FROM sesiones WHERE token_hash = ?", (_hash_token(token),))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _asegurar_zona_horaria(ahora: datetime | None) -> datetime:
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria.")
    return momento.astimezone(UTC)


def _formatear(momento: datetime) -> str:
    return momento.astimezone(UTC).isoformat(timespec="milliseconds")
