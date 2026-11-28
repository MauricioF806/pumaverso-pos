"""Dependencias compartidas para los endpoints FastAPI del nodo local."""

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pumaverso_tienda.config import get_settings
from pumaverso_tienda.persistencia import abrir_conexion
from pumaverso_tienda.sincronizacion import TransporteCentral, TransporteCentralHttp
from pumaverso_tienda.usuarios import (
    CredencialesExpiradasError,
    CredencialesInvalidasError,
    SesionInvalidaError,
    UsuarioInactivoError,
    UsuarioLocal,
    usuario_de_sesion,
)

_esquema_bearer = HTTPBearer(auto_error=False)


def obtener_conexion() -> Iterator[sqlite3.Connection]:
    """Abre y cierra una conexión SQLite por solicitud HTTP."""
    conexion = abrir_conexion(get_settings().database_path)
    try:
        yield conexion
    finally:
        conexion.close()


def obtener_transporte_central() -> TransporteCentral:
    """Devuelve el cliente HTTP configurado para hablar con central."""
    settings = get_settings()
    return TransporteCentralHttp(settings.central_url, llave=settings.llave_central)


def obtener_token(
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_esquema_bearer)],
) -> str:
    """Extrae el token del encabezado ``Authorization: Bearer <token>``."""
    if credenciales is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta iniciar sesión: envía el encabezado Authorization: Bearer <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credenciales.credentials


def obtener_usuario_actual(
    conexion: Annotated[sqlite3.Connection, Depends(obtener_conexion)],
    token: Annotated[str, Depends(obtener_token)],
) -> UsuarioLocal:
    """Usuario dueño de la sesión. Las operaciones se registran a su nombre (decisión 0006)."""
    try:
        return usuario_de_sesion(conexion, token)
    except (SesionInvalidaError, CredencialesInvalidasError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except (UsuarioInactivoError, CredencialesExpiradasError) as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
