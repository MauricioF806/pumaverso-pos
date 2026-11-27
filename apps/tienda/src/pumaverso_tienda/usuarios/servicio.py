"""Gestión local de usuarios y validación de credenciales offline (TTL 24h)."""

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pumaverso_tienda.persistencia.transacciones import transaccion

TTL_CREDENCIALES = timedelta(hours=24)

# Roles que pueden aprobar cancelaciones y ajustes de inventario (decisión 0006).
ROLES_APROBADORES = frozenset({"gerente", "supervisor"})


class CredencialesInvalidasError(ValueError):
    """El usuario no existe o el PIN proporcionado es incorrecto."""


class UsuarioInactivoError(PermissionError):
    """El usuario fue dado de baja y no puede operar en la sucursal."""


class CredencialesExpiradasError(PermissionError):
    """Las credenciales locales superaron el TTL de 24 horas sin sincronizar."""


class AprobacionInvalidaError(PermissionError):
    """Quien aprueba no existe, no está vigente, no tiene un rol aprobador o es el mismo operador."""


@dataclass(frozen=True, slots=True)
class UsuarioLocal:
    """Usuario almacenado en la base de datos de la sucursal."""

    usuario_id: str
    nombre: str
    rol: str
    hash_pin: str
    activo: bool
    actualizado_en: str


def generar_hash_pin(pin: str, *, sal: str | None = None) -> str:
    """Genera un hash PBKDF2-SHA256 con sal para almacenar el PIN local."""
    if not pin or not pin.strip():
        raise ValueError("El PIN no puede estar vacío.")
    sal_hex = sal if sal is not None else secrets.token_hex(16)
    derivado = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), sal_hex.encode("utf-8"), 100_000
    ).hex()
    return f"{sal_hex}${derivado}"


def verificar_pin(pin: str, hash_guardado: str) -> bool:
    """Compara un PIN en texto plano contra su hash guardado."""
    if "$" not in hash_guardado:
        return False
    sal_hex, _ = hash_guardado.split("$", 1)
    esperado = generar_hash_pin(pin, sal=sal_hex)
    return hmac.compare_digest(esperado, hash_guardado)


def guardar_usuario_local(
    conexion: sqlite3.Connection,
    *,
    usuario_id: str,
    nombre: str,
    rol: str,
    hash_pin: str,
    activo: bool = True,
    actualizado_en: datetime | None = None,
) -> UsuarioLocal:
    """Inserta o actualiza un usuario recibido desde el servidor central."""
    momento = _normalizar_utc(actualizado_en)
    if conexion.in_transaction:
        _upsert_usuario(conexion, usuario_id, nombre, rol, hash_pin, activo, momento)
    else:
        with transaccion(conexion):
            _upsert_usuario(conexion, usuario_id, nombre, rol, hash_pin, activo, momento)
    guardado = obtener_usuario(conexion, usuario_id)
    assert guardado is not None
    return guardado


def obtener_usuario(conexion: sqlite3.Connection, usuario_id: str) -> UsuarioLocal | None:
    """Consulta un usuario local por su identificador."""
    fila = conexion.execute(
        """
        SELECT usuario_id, nombre, rol, hash_pin, activo, actualizado_en
        FROM usuarios_locales WHERE usuario_id = ?
        """,
        (usuario_id,),
    ).fetchone()
    if fila is None:
        return None
    return UsuarioLocal(
        usuario_id=fila["usuario_id"],
        nombre=fila["nombre"],
        rol=fila["rol"],
        hash_pin=fila["hash_pin"],
        activo=bool(fila["activo"]),
        actualizado_en=fila["actualizado_en"],
    )


def autenticar_usuario(
    conexion: sqlite3.Connection,
    usuario_id: str,
    pin: str,
    *,
    ahora: datetime | None = None,
) -> UsuarioLocal:
    """Valida el PIN y que el usuario pueda operar (activo y vigente)."""
    usuario = obtener_usuario(conexion, usuario_id)
    if usuario is None or not verificar_pin(pin, usuario.hash_pin):
        raise CredencialesInvalidasError("Usuario o PIN incorrectos.")
    return validar_usuario_operativo(conexion, usuario_id, ahora=ahora)


def validar_usuario_operativo(
    conexion: sqlite3.Connection,
    usuario_id: str,
    *,
    ahora: datetime | None = None,
) -> UsuarioLocal:
    """Comprueba que el usuario existe, está activo y su vigencia de 24 horas no venció.

    La vigencia se cuenta desde que la tienda recibió los datos del usuario en
    la última sincronización (decisión 0006).
    """
    usuario = obtener_usuario(conexion, usuario_id)
    if usuario is None:
        raise CredencialesInvalidasError(f"El usuario '{usuario_id}' no existe localmente.")
    if not usuario.activo:
        raise UsuarioInactivoError(f"El usuario '{usuario_id}' está dado de baja.")

    referencia = _asegurar_zona_horaria(ahora)
    recibido = datetime.fromisoformat(usuario.actualizado_en)
    if referencia - recibido > TTL_CREDENCIALES:
        raise CredencialesExpiradasError(
            "Las credenciales locales superaron el TTL de 24 horas sin sincronizar."
        )
    return usuario


def validar_aprobador(
    conexion: sqlite3.Connection,
    aprobador_id: str,
    *,
    operador_id: str,
    ahora: datetime | None = None,
) -> UsuarioLocal:
    """Comprueba que ``aprobador_id`` puede aprobar una operación de ``operador_id``.

    Debe ser un usuario operativo con rol aprobador y distinto del operador:
    nadie aprueba su propia cancelación o ajuste.
    """
    if aprobador_id == operador_id:
        raise AprobacionInvalidaError("Nadie puede aprobar su propia operación.")
    try:
        aprobador = validar_usuario_operativo(conexion, aprobador_id, ahora=ahora)
    except (CredencialesInvalidasError, UsuarioInactivoError, CredencialesExpiradasError) as error:
        raise AprobacionInvalidaError(f"El aprobador no es válido: {error}") from error
    if aprobador.rol not in ROLES_APROBADORES:
        raise AprobacionInvalidaError(
            f"El usuario '{aprobador_id}' tiene rol '{aprobador.rol}' y no puede aprobar."
        )
    return aprobador


def _upsert_usuario(
    conexion: sqlite3.Connection,
    usuario_id: str,
    nombre: str,
    rol: str,
    hash_pin: str,
    activo: bool,
    actualizado_en: str,
) -> None:
    conexion.execute(
        """
        INSERT INTO usuarios_locales (
            usuario_id, nombre, rol, hash_pin, activo, actualizado_en
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (usuario_id) DO UPDATE SET
            nombre = excluded.nombre,
            rol = excluded.rol,
            hash_pin = excluded.hash_pin,
            activo = excluded.activo,
            actualizado_en = excluded.actualizado_en
        """,
        (usuario_id, nombre, rol, hash_pin, int(activo), actualizado_en),
    )


def _asegurar_zona_horaria(ahora: datetime | None) -> datetime:
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria.")
    return momento.astimezone(UTC)


def _normalizar_utc(ahora: datetime | None) -> str:
    return _asegurar_zona_horaria(ahora).isoformat(timespec="milliseconds")
