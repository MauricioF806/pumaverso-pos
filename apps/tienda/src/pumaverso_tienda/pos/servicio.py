"""Procesamiento atómico de ventas y cancelaciones en el punto de venta."""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pumaverso_contratos import (
    ContenidoCancelacion,
    ContenidoVenta,
    LineaProducto,
    TipoEvento,
    serializar_contenido_evento,
)

from pumaverso_tienda.catalogo import validar_producto_para_venta
from pumaverso_tienda.eventos import Evento, obtener_evento, registrar_evento
from pumaverso_tienda.inventario import ajustar_existencia_local
from pumaverso_tienda.persistencia.transacciones import transaccion
from pumaverso_tienda.usuarios import (
    TTL_CREDENCIALES,
    CredencialesExpiradasError,
    CredencialesInvalidasError,
    UsuarioInactivoError,
    obtener_usuario,
)

VENTANA_ALERTA_NEGATIVO = timedelta(hours=24)
UMBRAL_EVENTOS_NEGATIVOS = 2


class RelojAtrasadoError(RuntimeError):
    """El reloj local es anterior a la última sincronización confirmada."""


class VentaNoCancelableError(ValueError):
    """El evento indicado no existe, no es una venta o ya fue cancelado."""


@dataclass(frozen=True, slots=True)
class ItemVentaSolicitado:
    """Producto y cantidad solicitados en el carrito de caja."""

    sku: str
    cantidad: int


@dataclass(frozen=True, slots=True)
class ResultadoVenta:
    """Resultado completo de registrar una venta local."""

    evento: Evento
    total_centavos: int
    inventario_negativo: bool
    alerta_inventario_negativo: bool
    skus_costo_por_revisar: tuple[str, ...]


def registrar_venta(
    conexion: sqlite3.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    usuario_id: str,
    items: Sequence[ItemVentaSolicitado],
    ahora: datetime | None = None,
) -> ResultadoVenta:
    """Valida reglas offline, descuenta inventario y registra la venta atómicamente."""
    if not items:
        raise ValueError("La venta debe incluir al menos un producto.")

    referencia = _asegurar_zona_horaria(ahora)
    _validar_reloj_local(conexion, sucursal_id, caja_id, referencia)
    _validar_usuario_operativo(conexion, usuario_id, referencia)

    partidas: list[LineaProducto] = []
    skus_revision: list[str] = []
    total_centavos = 0

    for item in items:
        if item.cantidad <= 0:
            raise ValueError("La cantidad vendida debe ser mayor que cero.")
        validacion = validar_producto_para_venta(conexion, item.sku, ahora=referencia)
        producto = validacion.producto
        if validacion.costo_requiere_revision:
            skus_revision.append(producto.sku)

        subtotal = producto.precio_centavos * item.cantidad
        total_centavos += subtotal
        partidas.append(
            LineaProducto(
                sku=producto.sku,
                cantidad=item.cantidad,
                precio_unitario_centavos=producto.precio_centavos,
                subtotal_centavos=subtotal,
            )
        )

    with transaccion(conexion):
        produjo_negativo = False
        for partida in partidas:
            existencia = ajustar_existencia_local(
                conexion, partida.sku, -partida.cantidad, ahora=referencia
            )
            if existencia.cantidad < 0:
                produjo_negativo = True

        contenido = ContenidoVenta(
            partidas=partidas,
            total_centavos=total_centavos,
            metodo_pago="efectivo",
            inventario_negativo=produjo_negativo,
        )
        evento = registrar_evento(
            conexion,
            sucursal_id=sucursal_id,
            caja_id=caja_id,
            tipo=TipoEvento.VENTA,
            contenido=serializar_contenido_evento(contenido),
            usuario_id=usuario_id,
            ahora=referencia,
        )

        alerta_generada = False
        if produjo_negativo:
            alerta_generada = _evaluar_alerta_inventario_negativo(
                conexion, sucursal_id, caja_id, evento.consecutivo, referencia
            )

    return ResultadoVenta(
        evento=evento,
        total_centavos=total_centavos,
        inventario_negativo=produjo_negativo,
        alerta_inventario_negativo=alerta_generada,
        skus_costo_por_revisar=tuple(skus_revision),
    )


def cancelar_venta(
    conexion: sqlite3.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    usuario_id: str,
    consecutivo_original: int,
    motivo: str,
    aprobado_por: str,
    ahora: datetime | None = None,
) -> Evento:
    """Registra una cancelación append-only y reintegra las existencias vendidas."""
    referencia = _asegurar_zona_horaria(ahora)
    _validar_usuario_operativo(conexion, usuario_id, referencia)

    original = obtener_evento(conexion, sucursal_id, caja_id, consecutivo_original)
    if original is None or original.tipo is not TipoEvento.VENTA:
        raise VentaNoCancelableError(
            f"El evento {consecutivo_original} no corresponde a una venta existente."
        )

    ya_cancelada = conexion.execute(
        """
        SELECT 1 FROM ventas_canceladas
        WHERE sucursal_id = ? AND caja_id = ? AND consecutivo_original = ?
        """,
        (sucursal_id, caja_id, consecutivo_original),
    ).fetchone()
    if ya_cancelada is not None:
        raise VentaNoCancelableError(
            f"La venta con consecutivo {consecutivo_original} ya fue cancelada."
        )

    venta_original = ContenidoVenta.model_validate(original.contenido)
    contenido_cancelacion = ContenidoCancelacion(
        consecutivo_original=consecutivo_original,
        motivo=motivo,
        aprobado_por=aprobado_por,
    )

    with transaccion(conexion):
        for partida in venta_original.partidas:
            ajustar_existencia_local(
                conexion, partida.sku, partida.cantidad, ahora=referencia
            )

        evento = registrar_evento(
            conexion,
            sucursal_id=sucursal_id,
            caja_id=caja_id,
            tipo=TipoEvento.CANCELACION,
            contenido=serializar_contenido_evento(contenido_cancelacion),
            usuario_id=usuario_id,
            ahora=referencia,
        )
        conexion.execute(
            """
            INSERT INTO ventas_canceladas (
                sucursal_id, caja_id, consecutivo_original, consecutivo_evento, cancelada_en
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                sucursal_id,
                caja_id,
                consecutivo_original,
                evento.consecutivo,
                referencia.isoformat(timespec="milliseconds"),
            ),
        )
        return evento


def _validar_reloj_local(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str, referencia: datetime
) -> None:
    fila = conexion.execute(
        """
        SELECT actualizado_en FROM estado_sincronizacion
        WHERE sucursal_id = ? AND caja_id = ?
        """,
        (sucursal_id, caja_id),
    ).fetchone()
    if fila is not None:
        ultima_sync = datetime.fromisoformat(fila["actualizado_en"])
        if referencia < ultima_sync:
            raise RelojAtrasadoError(
                "El reloj local es anterior a la última sincronización registrada."
            )


def _validar_usuario_operativo(
    conexion: sqlite3.Connection, usuario_id: str, referencia: datetime
) -> None:
    usuario = obtener_usuario(conexion, usuario_id)
    if usuario is None:
        raise CredencialesInvalidasError(f"El usuario '{usuario_id}' no existe localmente.")
    if not usuario.activo:
        raise UsuarioInactivoError(f"El usuario '{usuario_id}' está dado de baja.")
    if referencia - datetime.fromisoformat(usuario.actualizado_en) > TTL_CREDENCIALES:
        raise CredencialesExpiradasError(
            "Las credenciales del usuario superaron el TTL de 24 horas."
        )


def _evaluar_alerta_inventario_negativo(
    conexion: sqlite3.Connection,
    sucursal_id: str,
    caja_id: str,
    consecutivo: int,
    referencia: datetime,
) -> bool:
    limite_tiempo = (referencia - VENTANA_ALERTA_NEGATIVO).isoformat(timespec="milliseconds")
    total_negativos = conexion.execute(
        """
        SELECT COUNT(*) FROM eventos
        WHERE sucursal_id = ?
          AND caja_id = ?
          AND tipo = 'venta'
          AND json_extract(contenido, '$.inventario_negativo') = 1
          AND registrado_en >= ?
        """,
        (sucursal_id, caja_id, limite_tiempo),
    ).fetchone()[0]

    if total_negativos > UMBRAL_EVENTOS_NEGATIVOS:
        conexion.execute(
            """
            INSERT INTO alertas_inventario (
                sucursal_id, caja_id, consecutivo, tipo_alerta, detalle, creada_en
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                sucursal_id,
                caja_id,
                consecutivo,
                "inventario_negativo_recurrente",
                f"Se registraron {total_negativos} ventas con inventario negativo en 24h.",
                referencia.isoformat(timespec="milliseconds"),
            ),
        )
        return True
    return False


def _asegurar_zona_horaria(ahora: datetime | None) -> datetime:
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria.")
    return momento.astimezone(UTC)