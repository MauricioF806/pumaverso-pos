from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pumaverso_contratos import (
    VERSION_ESQUEMA_ACTUAL,
    ContenidoAjuste,
    ContenidoCancelacion,
    ContenidoMerma,
    ContenidoRecepcionProveedor,
    ContenidoTraspaso,
    ContenidoVenta,
    EventoContrato,
    LineaProducto,
    LineaRecepcion,
    LineaTraspaso,
    TipoEvento,
    serializar_contenido_evento,
    validar_contenido_por_tipo,
)


def test_venta_valida_se_serializa_y_valida_por_tipo():
    venta = ContenidoVenta(
        partidas=[
            LineaProducto(
                sku="coca-600",
                cantidad=2,
                precio_unitario_centavos=1800,
                subtotal_centavos=3600,
            )
        ],
        total_centavos=3600,
        inventario_negativo=False,
    )
    datos = serializar_contenido_evento(venta)

    reconstruida = validar_contenido_por_tipo(TipoEvento.VENTA, datos)
    assert reconstruida == venta


def test_todos_los_tipos_tienen_modelo_registrado():
    ejemplos = {
        TipoEvento.VENTA: ContenidoVenta(
            partidas=[
                LineaProducto(
                    sku="coca-600",
                    cantidad=1,
                    precio_unitario_centavos=1800,
                    subtotal_centavos=1800,
                )
            ],
            total_centavos=1800,
        ),
        TipoEvento.CANCELACION: ContenidoCancelacion(
            consecutivo_original=1,
            motivo="Error de captura",
            aprobado_por="gerente-1",
        ),
        TipoEvento.RECEPCION_PROVEEDOR: ContenidoRecepcionProveedor(
            proveedor_id="PROV01",
            folio_documento="FAC-900",
            partidas=[
                LineaRecepcion(sku="coca-600", cantidad=24, costo_unitario_centavos=1100)
            ],
            total_centavos=26400,
        ),
        TipoEvento.MERMA: ContenidoMerma(
            sku="leche-1l",
            cantidad=1,
            motivo="Envase dañado",
        ),
        TipoEvento.AJUSTE: ContenidoAjuste(
            sku="coca-600",
            diferencia_cantidad=-2,
            motivo="Conteo físico",
            aprobado_por="gerente-1",
        ),
        TipoEvento.TRASPASO: ContenidoTraspaso(
            direccion="salida",
            sucursal_contraparte_id="S002",
            partidas=[LineaTraspaso(sku="coca-600", cantidad=6)],
        ),
    }

    for tipo, modelo in ejemplos.items():
        validado = validar_contenido_por_tipo(tipo, serializar_contenido_evento(modelo))
        assert validado == modelo


def test_ajuste_no_permite_diferencia_cero():
    with pytest.raises(ValidationError):
        ContenidoAjuste(
            sku="coca-600",
            diferencia_cantidad=0,
            motivo="Sin cambio",
            aprobado_por="gerente-1",
        )


def test_evento_contrato_valida_identidad_zona_horaria_y_hash():
    sobre = EventoContrato(
        sucursal_id="S001",
        caja_id="C01",
        consecutivo=1,
        tipo=TipoEvento.VENTA,
        version_esquema=VERSION_ESQUEMA_ACTUAL,
        contenido={"total_centavos": 1800},
        usuario_id="cajero-1",
        registrado_en=datetime(2026, 11, 21, 18, 30, tzinfo=UTC),
        hash="a" * 64,
    )
    assert sobre.version_esquema == 1

    with pytest.raises(ValidationError):
        EventoContrato(
            sucursal_id="S001",
            caja_id="C01",
            consecutivo=1,
            tipo=TipoEvento.VENTA,
            contenido={},
            usuario_id="cajero-1",
            registrado_en=datetime(2026, 11, 21, 18, 30),  # sin zona horaria
            hash="a" * 64,
        )