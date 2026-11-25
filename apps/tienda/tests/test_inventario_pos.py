from datetime import UTC, datetime, timedelta

import pytest
from pumaverso_contratos import LineaRecepcion, TipoEvento

from pumaverso_tienda.catalogo import PrecioExpiradoError, guardar_producto
from pumaverso_tienda.eventos import confirmar_hasta, obtener_evento
from pumaverso_tienda.inventario import (
    obtener_existencia,
    registrar_ajuste_inventario,
    registrar_merma,
    registrar_recepcion_proveedor,
)
from pumaverso_tienda.pos import (
    ItemVentaSolicitado,
    RelojAtrasadoError,
    VentaNoCancelableError,
    cancelar_venta,
    registrar_venta,
)
from pumaverso_tienda.usuarios import generar_hash_pin, guardar_usuario_local

SUCURSAL = "S001"
CAJA = "C01"
BASE = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)


@pytest.fixture
def entorno_tienda(conexion):
    guardar_usuario_local(
        conexion,
        usuario_id="cajero-1",
        nombre="Ana López",
        rol="cajero",
        hash_pin=generar_hash_pin("1234"),
        actualizado_en=BASE,
    )
    guardar_producto(
        conexion,
        sku="coca-600",
        nombre="Refresco Cola 600ml",
        precio_centavos=1800,
        precio_actualizado_en=BASE,
        costo_centavos=1100,
        costo_actualizado_en=BASE,
    )
    return conexion


def test_recepcion_venta_y_cancelacion_append_only(entorno_tienda):
    registrar_recepcion_proveedor(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        proveedor_id="PROV01",
        folio_documento="FAC-100",
        partidas=[LineaRecepcion(sku="coca-600", cantidad=10, costo_unitario_centavos=1100)],
        ahora=BASE,
    )
    assert obtener_existencia(entorno_tienda, "coca-600") == 10

    venta = registrar_venta(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=3)],
        ahora=BASE + timedelta(minutes=5),
    )
    assert venta.total_centavos == 5400
    assert venta.inventario_negativo is False
    assert obtener_existencia(entorno_tienda, "coca-600") == 7

    evento_cancelacion = cancelar_venta(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        consecutivo_original=venta.evento.consecutivo,
        motivo="Cliente devolvió producto",
        aprobado_por="gerente-1",
        ahora=BASE + timedelta(minutes=10),
    )
    assert evento_cancelacion.tipo is TipoEvento.CANCELACION
    assert obtener_existencia(entorno_tienda, "coca-600") == 10
    assert obtener_evento(entorno_tienda, SUCURSAL, CAJA, venta.evento.consecutivo) is not None

    with pytest.raises(VentaNoCancelableError):
        cancelar_venta(
            entorno_tienda,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            usuario_id="cajero-1",
            consecutivo_original=venta.evento.consecutivo,
            motivo="Doble cancelación",
            aprobado_por="gerente-1",
            ahora=BASE + timedelta(minutes=12),
        )


def test_venta_permite_inventario_negativo_y_alerta_al_tercero_en_24h(entorno_tienda):
    # Tres ventas sin existencias previas dentro de 24 horas.
    r1 = registrar_venta(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        ahora=BASE + timedelta(hours=1),
    )
    r2 = registrar_venta(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        ahora=BASE + timedelta(hours=2),
    )
    r3 = registrar_venta(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        ahora=BASE + timedelta(hours=3),
    )

    assert r1.inventario_negativo is True and r1.alerta_inventario_negativo is False
    assert r2.inventario_negativo is True and r2.alerta_inventario_negativo is False
    assert r3.inventario_negativo is True and r3.alerta_inventario_negativo is True
    assert obtener_existencia(entorno_tienda, "coca-600") == -3

    alertas = entorno_tienda.execute("SELECT COUNT(*) FROM alertas_inventario").fetchone()[0]
    assert alertas == 1


def test_venta_bloquea_precio_expirado_y_reloj_atrasado(entorno_tienda):
    # Registramos una venta válida y confirmamos sincronización a las 11:00 UTC.
    v = registrar_venta(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        ahora=BASE + timedelta(hours=1),
    )
    confirmar_hasta(
        entorno_tienda, SUCURSAL, CAJA, v.evento.consecutivo, ahora=BASE + timedelta(hours=1)
    )

    # Si el reloj retrocede antes de las 11:00 UTC, se bloquean las ventas.
    with pytest.raises(RelojAtrasadoError):
        registrar_venta(
            entorno_tienda,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            usuario_id="cajero-1",
            items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
            ahora=BASE + timedelta(minutes=30),
        )

    # Si pasan 73 horas y el usuario renovó sesión, el precio caducado bloquea la venta.
    guardar_usuario_local(
        entorno_tienda,
        usuario_id="cajero-1",
        nombre="Ana López",
        rol="cajero",
        hash_pin=generar_hash_pin("1234"),
        actualizado_en=BASE + timedelta(hours=72),
    )
    with pytest.raises(PrecioExpiradoError):
        registrar_venta(
            entorno_tienda,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            usuario_id="cajero-1",
            items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
            ahora=BASE + timedelta(hours=73),
        )

    # Registramos una venta válida y confirmamos sincronización a las 11:00 UTC.
    v = registrar_venta(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        ahora=BASE + timedelta(hours=1),
    )
    confirmar_hasta(
        entorno_tienda, SUCURSAL, CAJA, v.evento.consecutivo, ahora=BASE + timedelta(hours=1)
    )

    # Si el reloj retrocede antes de las 11:00 UTC, se bloquean las ventas.
    with pytest.raises(RelojAtrasadoError):
        registrar_venta(
            entorno_tienda,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            usuario_id="cajero-1",
            items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
            ahora=BASE + timedelta(minutes=30),
        )


def test_merma_y_ajuste_actualizan_existencias_y_eventos(entorno_tienda):
    registrar_recepcion_proveedor(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        proveedor_id="PROV01",
        folio_documento="FAC-200",
        partidas=[LineaRecepcion(sku="coca-600", cantidad=20, costo_unitario_centavos=1100)],
        ahora=BASE,
    )
    registrar_merma(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        sku="coca-600",
        cantidad=2,
        motivo="Lata golpeada",
        ahora=BASE + timedelta(minutes=15),
    )
    registrar_ajuste_inventario(
        entorno_tienda,
        sucursal_id=SUCURSAL,
        caja_id=CAJA,
        usuario_id="cajero-1",
        sku="coca-600",
        diferencia_cantidad=-3,
        motivo="Conteo cíclico",
        aprobado_por="gerente-1",
        ahora=BASE + timedelta(minutes=20),
    )
    assert obtener_existencia(entorno_tienda, "coca-600") == 15