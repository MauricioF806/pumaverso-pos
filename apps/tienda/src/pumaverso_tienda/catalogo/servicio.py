"""Catálogo local de productos y validación de vigencia de precios y costos."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pumaverso_tienda.persistencia.transacciones import transaccion

TTL_PRECIO = timedelta(hours=72)
TTL_COSTO = timedelta(days=30)


class ProductoNoEncontradoError(KeyError):
    """El SKU consultado no existe en el catálogo local."""


class ProductoInactivoError(ValueError):
    """El producto está deshabilitado para venta."""


class PrecioExpiradoError(PermissionError):
    """El precio del SKU superó el TTL de 72 horas y bloquea su venta."""


@dataclass(frozen=True, slots=True)
class ProductoCatalogo:
    """Producto registrado en el catálogo local de la sucursal."""

    sku: str
    nombre: str
    precio_centavos: int
    precio_actualizado_en: str
    costo_centavos: int
    costo_actualizado_en: str
    activo: bool


@dataclass(frozen=True, slots=True)
class ResultadoValidacionProducto:
    """Resultado de verificar si un SKU puede venderse y estado de su costo."""

    producto: ProductoCatalogo
    costo_requiere_revision: bool


def guardar_producto(
    conexion: sqlite3.Connection,
    *,
    sku: str,
    nombre: str,
    precio_centavos: int,
    precio_actualizado_en: datetime,
    costo_centavos: int,
    costo_actualizado_en: datetime,
    activo: bool = True,
) -> ProductoCatalogo:
    """Inserta o actualiza un producto recibido desde central."""
    if precio_centavos < 0 or costo_centavos < 0:
        raise ValueError("El precio y el costo no pueden ser negativos.")
    precio_utc = _normalizar_utc(precio_actualizado_en)
    costo_utc = _normalizar_utc(costo_actualizado_en)

    if conexion.in_transaction:
        _upsert_producto(
            conexion, sku, nombre, precio_centavos, precio_utc, costo_centavos, costo_utc, activo
        )
    else:
        with transaccion(conexion):
            _upsert_producto(
                conexion,
                sku,
                nombre,
                precio_centavos,
                precio_utc,
                costo_centavos,
                costo_utc,
                activo,
            )
    guardado = obtener_producto(conexion, sku)
    assert guardado is not None
    return guardado


def obtener_producto(conexion: sqlite3.Connection, sku: str) -> ProductoCatalogo | None:
    """Consulta un producto del catálogo local por SKU."""
    fila = conexion.execute(
        """
        SELECT sku, nombre, precio_centavos, precio_actualizado_en,
               costo_centavos, costo_actualizado_en, activo
        FROM productos_catalogo WHERE sku = ?
        """,
        (sku,),
    ).fetchone()
    if fila is None:
        return None
    return ProductoCatalogo(
        sku=fila["sku"],
        nombre=fila["nombre"],
        precio_centavos=fila["precio_centavos"],
        precio_actualizado_en=fila["precio_actualizado_en"],
        costo_centavos=fila["costo_centavos"],
        costo_actualizado_en=fila["costo_actualizado_en"],
        activo=bool(fila["activo"]),
    )


def validar_producto_para_venta(
    conexion: sqlite3.Connection,
    sku: str,
    *,
    ahora: datetime | None = None,
) -> ResultadoValidacionProducto:
    """Verifica existencia, estado activo y reglas TTL antes de vender un SKU.

    - Precio > 72h: bloquea la venta lanzando ``PrecioExpiradoError``.
    - Costo > 30d: permite la venta pero marca ``costo_requiere_revision=True``.
    """
    producto = obtener_producto(conexion, sku)
    if producto is None:
        raise ProductoNoEncontradoError(f"El SKU '{sku}' no existe en el catálogo local.")
    if not producto.activo:
        raise ProductoInactivoError(f"El SKU '{sku}' está inactivo.")

    referencia = _asegurar_zona_horaria(ahora)
    fecha_precio = datetime.fromisoformat(producto.precio_actualizado_en)
    if referencia - fecha_precio > TTL_PRECIO:
        raise PrecioExpiradoError(
            f"El precio del SKU '{sku}' superó el TTL de 72 horas."
        )

    fecha_costo = datetime.fromisoformat(producto.costo_actualizado_en)
    requiere_revision = (referencia - fecha_costo) > TTL_COSTO
    return ResultadoValidacionProducto(
        producto=producto,
        costo_requiere_revision=requiere_revision,
    )


def _upsert_producto(
    conexion: sqlite3.Connection,
    sku: str,
    nombre: str,
    precio_centavos: int,
    precio_actualizado_en: str,
    costo_centavos: int,
    costo_actualizado_en: str,
    activo: bool,
) -> None:
    conexion.execute(
        """
        INSERT INTO productos_catalogo (
            sku, nombre, precio_centavos, precio_actualizado_en,
            costo_centavos, costo_actualizado_en, activo
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (sku) DO UPDATE SET
            nombre = excluded.nombre,
            precio_centavos = excluded.precio_centavos,
            precio_actualizado_en = excluded.precio_actualizado_en,
            costo_centavos = excluded.costo_centavos,
            costo_actualizado_en = excluded.costo_actualizado_en,
            activo = excluded.activo
        """,
        (
            sku,
            nombre,
            precio_centavos,
            precio_actualizado_en,
            costo_centavos,
            costo_actualizado_en,
            int(activo),
        ),
    )


def _asegurar_zona_horaria(ahora: datetime | None) -> datetime:
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria.")
    return momento.astimezone(UTC)


def _normalizar_utc(ahora: datetime) -> str:
    return _asegurar_zona_horaria(ahora).isoformat(timespec="milliseconds")