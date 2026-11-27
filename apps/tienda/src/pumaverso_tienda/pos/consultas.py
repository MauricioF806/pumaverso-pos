"""Consultas de ventas para la pantalla de caja: ventas recientes y detalle del ticket.

Solo leen. Las ventas salen del registro de eventos y su estado de cancelación
de la tabla ``ventas_canceladas``.
"""

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PartidaVenta:
    """Un producto dentro de una venta, con su nombre para mostrarlo en el ticket."""

    sku: str
    nombre: str | None
    cantidad: int
    precio_unitario_centavos: int
    subtotal_centavos: int


@dataclass(frozen=True, slots=True)
class VentaRegistrada:
    """Venta tal como quedó registrada, con su estado de cancelación."""

    consecutivo: int
    registrado_en: str
    usuario_id: str
    total_centavos: int
    inventario_negativo: bool
    partidas: tuple[PartidaVenta, ...]
    cancelada: bool
    consecutivo_cancelacion: int | None
    hash: str


_CONSULTA_VENTAS = """
    SELECT e.consecutivo, e.registrado_en, e.usuario_id, e.contenido, e.hash,
           vc.consecutivo_evento AS consecutivo_cancelacion
    FROM eventos AS e
    LEFT JOIN ventas_canceladas AS vc
           ON vc.sucursal_id = e.sucursal_id
          AND vc.caja_id = e.caja_id
          AND vc.consecutivo_original = e.consecutivo
    WHERE e.sucursal_id = ? AND e.caja_id = ? AND e.tipo = 'venta'
"""


def listar_ventas_recientes(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str, *, limite: int = 20
) -> list[VentaRegistrada]:
    """Últimas ventas de la caja, de la más reciente a la más antigua."""
    if limite < 1:
        raise ValueError("limite debe ser mayor que cero")
    filas = conexion.execute(
        _CONSULTA_VENTAS + " ORDER BY e.consecutivo DESC LIMIT ?",
        (sucursal_id, caja_id, limite),
    ).fetchall()
    return _filas_a_ventas(conexion, filas)


def obtener_venta(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str, consecutivo: int
) -> VentaRegistrada | None:
    """Detalle de una venta para mostrar o reimprimir su ticket. ``None`` si no es una venta."""
    filas = conexion.execute(
        _CONSULTA_VENTAS + " AND e.consecutivo = ?",
        (sucursal_id, caja_id, consecutivo),
    ).fetchall()
    ventas = _filas_a_ventas(conexion, filas)
    return ventas[0] if ventas else None


def _filas_a_ventas(
    conexion: sqlite3.Connection, filas: list[sqlite3.Row]
) -> list[VentaRegistrada]:
    contenidos = [json.loads(fila["contenido"]) for fila in filas]
    skus = {p["sku"] for contenido in contenidos for p in contenido["partidas"]}
    nombres = _nombres_de_productos(conexion, skus)

    ventas = []
    for fila, contenido in zip(filas, contenidos, strict=True):
        partidas = tuple(
            PartidaVenta(
                sku=p["sku"],
                nombre=nombres.get(p["sku"]),
                cantidad=p["cantidad"],
                precio_unitario_centavos=p["precio_unitario_centavos"],
                subtotal_centavos=p["subtotal_centavos"],
            )
            for p in contenido["partidas"]
        )
        ventas.append(
            VentaRegistrada(
                consecutivo=fila["consecutivo"],
                registrado_en=fila["registrado_en"],
                usuario_id=fila["usuario_id"],
                total_centavos=contenido["total_centavos"],
                inventario_negativo=bool(contenido.get("inventario_negativo", False)),
                partidas=partidas,
                cancelada=fila["consecutivo_cancelacion"] is not None,
                consecutivo_cancelacion=fila["consecutivo_cancelacion"],
                hash=fila["hash"],
            )
        )
    return ventas


def _nombres_de_productos(conexion: sqlite3.Connection, skus: set[str]) -> dict[str, str]:
    if not skus:
        return {}
    marcadores = ", ".join("?" for _ in skus)
    filas = conexion.execute(
        f"SELECT sku, nombre FROM productos_catalogo WHERE sku IN ({marcadores})",
        tuple(skus),
    ).fetchall()
    return {fila["sku"]: fila["nombre"] for fila in filas}
