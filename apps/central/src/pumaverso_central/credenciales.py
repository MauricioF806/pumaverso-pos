"""Credenciales de la API de central (decisión 0010).

Hay dos tipos de llave, y las dos se envían como ``Authorization: Bearer <llave>``:

- **Llave de sucursal:** la usa la tienda para sincronizar. Solo sirve para su
  propia sucursal: S001 no puede enviar ventas ni leer datos como S002. Central
  guarda solo el SHA-256 de la llave, nunca la llave.
- **Llave de administración:** ``CENTRAL_LLAVE_ADMIN`` en el ``.env`` de central.
  Sirve para todo: usuarios, productos, la cola, el inventario fantasma y para
  registrar las llaves de las sucursales.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

LONGITUD_MINIMA_LLAVE = 32


@dataclass(frozen=True, slots=True)
class Credencial:
    """Quién hace la petición."""

    es_admin: bool
    sucursal_id: str | None = None

    def puede_actuar_por(self, sucursal_id: str) -> bool:
        return self.es_admin or self.sucursal_id == sucursal_id


def huella_llave(llave: str) -> str:
    return hashlib.sha256(llave.encode("utf-8")).hexdigest()


def generar_llave() -> str:
    """Llave nueva al azar: 43 caracteres, 256 bits."""
    return secrets.token_urlsafe(32)


def identificar(
    conn: psycopg.Connection, llave: str, *, llave_admin: str | None
) -> Credencial | None:
    """Devuelve a quién pertenece la llave, o ``None`` si no es válida."""
    if llave_admin and hmac.compare_digest(llave.encode(), llave_admin.encode()):
        return Credencial(es_admin=True)
    # En su propia transacción: así la conexión queda libre para la operación que sigue.
    with conn.transaction():
        fila = conn.execute(
            "SELECT sucursal_id FROM sincronizacion.credenciales_sucursal WHERE huella_llave = %s",
            (huella_llave(llave),),
        ).fetchone()
    if fila is None:
        return None
    return Credencial(es_admin=False, sucursal_id=fila["sucursal_id"])


def registrar_llave_sucursal(conn: psycopg.Connection, sucursal_id: str, llave: str) -> None:
    """Guarda (o reemplaza) la llave de una sucursal. La anterior deja de servir."""
    if len(llave) < LONGITUD_MINIMA_LLAVE:
        raise ValueError(f"La llave debe tener al menos {LONGITUD_MINIMA_LLAVE} caracteres.")
    try:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO sincronizacion.credenciales_sucursal
                    (sucursal_id, huella_llave, actualizada_en)
                VALUES (%s, %s, %s)
                ON CONFLICT (sucursal_id) DO UPDATE SET
                    huella_llave = excluded.huella_llave,
                    actualizada_en = excluded.actualizada_en
                """,
                (sucursal_id, huella_llave(llave), datetime.now(UTC)),
            )
    except psycopg.errors.UniqueViolation as error:
        raise ValueError("Esa llave ya la usa otra sucursal; cada una necesita la suya.") from error


def revocar_llave_sucursal(conn: psycopg.Connection, sucursal_id: str) -> bool:
    """Borra la llave de una sucursal. Devuelve ``False`` si no tenía."""
    with conn.transaction():
        cursor = conn.execute(
            "DELETE FROM sincronizacion.credenciales_sucursal WHERE sucursal_id = %s",
            (sucursal_id,),
        )
    return cursor.rowcount > 0
