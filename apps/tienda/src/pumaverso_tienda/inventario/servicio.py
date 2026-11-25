"""Control de existencias locales y movimientos de inventario."""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pumaverso_contratos import (
    ContenidoAjuste,
    ContenidoMerma,
    ContenidoRecepcionProveedor,
    LineaRecepcion,
    TipoEvento,
    serializar_contenido_evento,
)

from pumaverso_tienda.catalogo import ProductoNoEncontradoError, obtener_producto
from pumaverso_tienda.eventos import Evento, registrar_evento
from pumaverso_tienda.persistencia.transacciones import transaccion


@dataclass(frozen=True, slots=True)
class ExistenciaProducto:
    """Cantidad disponible en la sucursal para un SKU."""

    sku: str
    cantidad: int
    actualizado_en: str


def obtener_existencia(conexion: sqlite3.Connection, sku: str) -> int:
    """Devuelve las existencias actuales de un SKU en la sucursal (0 si no hay registro)."""
    fila = conexion.execute(
        "SELECT cantidad FROM existencias_locales WHERE sku = ?",
        (sku,),
    ).fetchone()
    return fila["cantidad"] if fila is not None else 0


def ajustar_existencia_local(
    conexion: sqlite3.Connection,
    sku: str,
    delta: int,
    *,
    ahora: datetime | None = None,
) -> ExistenciaProducto:
    """Suma o resta ``delta`` a las existencias de un SKU existente en catálogo."""
    if obtener_producto(conexion, sku) is None:
        raise ProductoNoEncontradoError(f"El SKU '{sku}' no existe en el catálogo local.")
    momento = _normalizar_utc(ahora)
    nueva_cantidad = obtener_existencia(conexion, sku) + delta
    conexion.execute(
        """
        INSERT INTO existencias_locales (sku, cantidad, actualizado_en)
        VALUES (?, ?, ?)
        ON CONFLICT (sku) DO UPDATE SET
            cantidad = excluded.cantidad,
            actualizado_en = excluded.actualizado_en
        """,
        (sku, nueva_cantidad, momento),
    )
    return ExistenciaProducto(sku=sku, cantidad=nueva_cantidad, actualizado_en=momento)


def registrar_recepcion_proveedor(
    conexion: sqlite3.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    usuario_id: str,
    proveedor_id: str,
    folio_documento: str,
    partidas: Sequence[LineaRecepcion],
    ahora: datetime | None = None,
) -> Evento:
    """Registra entrada de mercancía de proveedor y su evento de forma atómica."""
    total_centavos = sum(p.cantidad * p.costo_unitario_centavos for p in partidas)
    contenido = ContenidoRecepcionProveedor(
        proveedor_id=proveedor_id,
        folio_documento=folio_documento,
        partidas=list(partidas),
        total_centavos=total_centavos,
    )
    with transaccion(conexion):
        for partida in contenido.partidas:
            ajustar_existencia_local(conexion, partida.sku, partida.cantidad, ahora=ahora)
        return registrar_evento(
            conexion,
            sucursal_id=sucursal_id,
            caja_id=caja_id,
            tipo=TipoEvento.RECEPCION_PROVEEDOR,
            contenido=serializar_contenido_evento(contenido),
            usuario_id=usuario_id,
            ahora=ahora,
        )


def registrar_merma(
    conexion: sqlite3.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    usuario_id: str,
    sku: str,
    cantidad: int,
    motivo: str,
    ahora: datetime | None = None,
) -> Evento:
    """Descuenta mercancía mermada y registra el evento de forma atómica."""
    contenido = ContenidoMerma(sku=sku, cantidad=cantidad, motivo=motivo)
    with transaccion(conexion):
        ajustar_existencia_local(conexion, contenido.sku, -contenido.cantidad, ahora=ahora)
        return registrar_evento(
            conexion,
            sucursal_id=sucursal_id,
            caja_id=caja_id,
            tipo=TipoEvento.MERMA,
            contenido=serializar_contenido_evento(contenido),
            usuario_id=usuario_id,
            ahora=ahora,
        )


def registrar_ajuste_inventario(
    conexion: sqlite3.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    usuario_id: str,
    sku: str,
    diferencia_cantidad: int,
    motivo: str,
    aprobado_por: str,
    ahora: datetime | None = None,
) -> Evento:
    """Aplica un ajuste aprobado de inventario y registra el evento de forma atómica."""
    contenido = ContenidoAjuste(
        sku=sku,
        diferencia_cantidad=diferencia_cantidad,
        motivo=motivo,
        aprobado_por=aprobado_por,
    )
    with transaccion(conexion):
        ajustar_existencia_local(
            conexion, contenido.sku, contenido.diferencia_cantidad, ahora=ahora
        )
        return registrar_evento(
            conexion,
            sucursal_id=sucursal_id,
            caja_id=caja_id,
            tipo=TipoEvento.AJUSTE,
            contenido=serializar_contenido_evento(contenido),
            usuario_id=usuario_id,
            ahora=ahora,
        )


def _normalizar_utc(ahora: datetime | None) -> str:
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria.")
    return momento.astimezone(UTC).isoformat(timespec="milliseconds")