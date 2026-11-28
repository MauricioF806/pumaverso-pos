"""Reporte de ventas para el gerente (PRD, alcance V1; decisión 0012).

Sale del registro de eventos de la tienda, así que funciona sin conexión. El
rango lo da quien pregunta (la pantalla calcula "hoy" en la hora local de la
sucursal), y se compara contra la fecha en UTC con la que se guarda cada evento.
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pumaverso_contratos import formatear_registrado_en

DIAS_MAXIMOS = 31
PRODUCTOS_EN_REPORTE = 5


@dataclass(frozen=True, slots=True)
class VentasDeCajero:
    usuario_id: str
    nombre: str
    ventas: int
    total_centavos: int


@dataclass(frozen=True, slots=True)
class ProductoVendido:
    sku: str
    nombre: str
    piezas: int
    total_centavos: int


@dataclass(slots=True)
class ReporteVentas:
    desde: datetime
    hasta: datetime
    ventas: int = 0
    total_centavos: int = 0
    piezas: int = 0
    ticket_promedio_centavos: int = 0
    canceladas: int = 0
    canceladas_centavos: int = 0
    con_inventario_negativo: int = 0
    por_cajero: list[VentasDeCajero] = field(default_factory=list)
    productos_mas_vendidos: list[ProductoVendido] = field(default_factory=list)


def reporte_de_ventas(
    conexion: sqlite3.Connection,
    sucursal_id: str,
    caja_id: str,
    *,
    desde: datetime,
    hasta: datetime,
) -> ReporteVentas:
    """Ventas de ``desde`` (incluido) a ``hasta`` (excluido).

    Las ventas canceladas no suman al total; se cuentan aparte.
    """
    if desde.tzinfo is None or hasta.tzinfo is None:
        raise ValueError("desde y hasta deben incluir zona horaria.")
    if hasta <= desde:
        raise ValueError("hasta debe ser posterior a desde.")
    if hasta - desde > timedelta(days=DIAS_MAXIMOS):
        raise ValueError(f"El rango no puede pasar de {DIAS_MAXIMOS} días.")

    filas = conexion.execute(
        """
        SELECT e.usuario_id, e.contenido, vc.consecutivo_evento AS cancelacion
        FROM eventos AS e
        LEFT JOIN ventas_canceladas AS vc
               ON vc.sucursal_id = e.sucursal_id
              AND vc.caja_id = e.caja_id
              AND vc.consecutivo_original = e.consecutivo
        WHERE e.sucursal_id = ? AND e.caja_id = ? AND e.tipo = 'venta'
          AND e.registrado_en >= ? AND e.registrado_en < ?
        """,
        (sucursal_id, caja_id, formatear_registrado_en(desde), formatear_registrado_en(hasta)),
    ).fetchall()

    reporte = ReporteVentas(desde=desde, hasta=hasta)
    por_cajero: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    por_producto: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for fila in filas:
        contenido = json.loads(fila["contenido"])
        total = contenido["total_centavos"]
        if fila["cancelacion"] is not None:
            reporte.canceladas += 1
            reporte.canceladas_centavos += total
            continue
        reporte.ventas += 1
        reporte.total_centavos += total
        reporte.con_inventario_negativo += bool(contenido.get("inventario_negativo"))
        por_cajero[fila["usuario_id"]][0] += 1
        por_cajero[fila["usuario_id"]][1] += total
        for partida in contenido["partidas"]:
            reporte.piezas += partida["cantidad"]
            por_producto[partida["sku"]][0] += partida["cantidad"]
            por_producto[partida["sku"]][1] += partida["subtotal_centavos"]

    if reporte.ventas:
        reporte.ticket_promedio_centavos = round(reporte.total_centavos / reporte.ventas)
    nombres_usuarios = _nombres(conexion, "usuarios_locales", "usuario_id", por_cajero)
    nombres_productos = _nombres(conexion, "productos_catalogo", "sku", por_producto)
    reporte.por_cajero = sorted(
        (
            VentasDeCajero(u, nombres_usuarios.get(u, u), ventas, total)
            for u, (ventas, total) in por_cajero.items()
        ),
        key=lambda c: (-c.total_centavos, c.usuario_id),
    )
    reporte.productos_mas_vendidos = sorted(
        (
            ProductoVendido(sku, nombres_productos.get(sku, sku), piezas, total)
            for sku, (piezas, total) in por_producto.items()
        ),
        key=lambda p: (-p.piezas, -p.total_centavos, p.sku),
    )[:PRODUCTOS_EN_REPORTE]
    return reporte


def _nombres(conexion: sqlite3.Connection, tabla: str, llave: str, ids) -> dict[str, str]:
    ids = list(ids)
    if not ids:
        return {}
    marcadores = ", ".join("?" for _ in ids)
    filas = conexion.execute(
        f"SELECT {llave} AS id, nombre FROM {tabla} WHERE {llave} IN ({marcadores})",
        ids,
    ).fetchall()
    return {fila["id"]: fila["nombre"] for fila in filas}
