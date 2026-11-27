"""Usuarios locales, autenticación offline y vigencia de credenciales."""

from pumaverso_tienda.usuarios.servicio import (
    ROLES_APROBADORES,
    TTL_CREDENCIALES,
    AprobacionInvalidaError,
    CredencialesExpiradasError,
    CredencialesInvalidasError,
    UsuarioInactivoError,
    UsuarioLocal,
    autenticar_usuario,
    generar_hash_pin,
    guardar_usuario_local,
    obtener_usuario,
    validar_aprobador,
    validar_usuario_operativo,
    verificar_pin,
)
from pumaverso_tienda.usuarios.sesiones import (
    DURACION_SESION,
    Sesion,
    SesionInvalidaError,
    cerrar_sesion,
    iniciar_sesion,
    usuario_de_sesion,
)

__all__ = [
    "DURACION_SESION",
    "ROLES_APROBADORES",
    "TTL_CREDENCIALES",
    "AprobacionInvalidaError",
    "CredencialesExpiradasError",
    "CredencialesInvalidasError",
    "Sesion",
    "SesionInvalidaError",
    "UsuarioInactivoError",
    "UsuarioLocal",
    "autenticar_usuario",
    "cerrar_sesion",
    "generar_hash_pin",
    "guardar_usuario_local",
    "iniciar_sesion",
    "obtener_usuario",
    "usuario_de_sesion",
    "validar_aprobador",
    "validar_usuario_operativo",
    "verificar_pin",
]
