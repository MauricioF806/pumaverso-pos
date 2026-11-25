"""Usuarios locales, autenticación offline y vigencia de credenciales."""

from pumaverso_tienda.usuarios.servicio import (
    TTL_CREDENCIALES,
    CredencialesExpiradasError,
    CredencialesInvalidasError,
    UsuarioInactivoError,
    UsuarioLocal,
    autenticar_usuario,
    generar_hash_pin,
    guardar_usuario_local,
    obtener_usuario,
    verificar_pin,
)

__all__ = [
    "TTL_CREDENCIALES",
    "CredencialesExpiradasError",
    "CredencialesInvalidasError",
    "UsuarioInactivoError",
    "UsuarioLocal",
    "autenticar_usuario",
    "generar_hash_pin",
    "guardar_usuario_local",
    "obtener_usuario",
    "verificar_pin",
]