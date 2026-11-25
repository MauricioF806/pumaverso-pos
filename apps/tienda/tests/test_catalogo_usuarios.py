from datetime import UTC, datetime, timedelta

import pytest

from pumaverso_tienda.catalogo import (
    PrecioExpiradoError,
    ProductoInactivoError,
    ProductoNoEncontradoError,
    guardar_producto,
    validar_producto_para_venta,
)
from pumaverso_tienda.usuarios import (
    CredencialesExpiradasError,
    CredencialesInvalidasError,
    UsuarioInactivoError,
    autenticar_usuario,
    generar_hash_pin,
    guardar_usuario_local,
)

BASE = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def test_autenticar_usuario_vigente_funciona(conexion):
    guardar_usuario_local(
        conexion,
        usuario_id="cajero-1",
        nombre="Ana López",
        rol="cajero",
        hash_pin=generar_hash_pin("1234"),
        actualizado_en=BASE,
    )

    usuario = autenticar_usuario(
        conexion, "cajero-1", "1234", ahora=BASE + timedelta(hours=23)
    )
    assert usuario.nombre == "Ana López"


def test_autenticar_rechaza_pin_incorrecto_baja_y_ttl_24h(conexion):
    guardar_usuario_local(
        conexion,
        usuario_id="cajero-1",
        nombre="Ana López",
        rol="cajero",
        hash_pin=generar_hash_pin("1234"),
        actualizado_en=BASE,
    )

    with pytest.raises(CredencialesInvalidasError):
        autenticar_usuario(conexion, "cajero-1", "0000", ahora=BASE)

    with pytest.raises(CredencialesExpiradasError):
        autenticar_usuario(
            conexion, "cajero-1", "1234", ahora=BASE + timedelta(hours=24, seconds=1)
        )

    guardar_usuario_local(
        conexion,
        usuario_id="cajero-1",
        nombre="Ana López",
        rol="cajero",
        hash_pin=generar_hash_pin("1234"),
        activo=False,
        actualizado_en=BASE,
    )
    with pytest.raises(UsuarioInactivoError):
        autenticar_usuario(conexion, "cajero-1", "1234", ahora=BASE)


def test_catalogo_valida_ttl_precio_72h_y_marca_costo_30d(conexion):
    guardar_producto(
        conexion,
        sku="coca-600",
        nombre="Refresco Cola 600ml",
        precio_centavos=1800,
        precio_actualizado_en=BASE,
        costo_centavos=1100,
        costo_actualizado_en=BASE - timedelta(days=31),
    )

    # Precio vigente (<72h), pero costo > 30 días: permite venta y marca revisión.
    resultado = validar_producto_para_venta(
        conexion, "coca-600", ahora=BASE + timedelta(hours=71)
    )
    assert resultado.producto.precio_centavos == 1800
    assert resultado.costo_requiere_revision is True

    # Precio caducado (>72h): bloquea la venta.
    with pytest.raises(PrecioExpiradoError):
        validar_producto_para_venta(
            conexion, "coca-600", ahora=BASE + timedelta(hours=72, seconds=1)
        )


def test_catalogo_rechaza_sku_inexistente_o_inactivo(conexion):
    with pytest.raises(ProductoNoEncontradoError):
        validar_producto_para_venta(conexion, "no-existe", ahora=BASE)

    guardar_producto(
        conexion,
        sku="pan-dulce",
        nombre="Concha Vainilla",
        precio_centavos=1500,
        precio_actualizado_en=BASE,
        costo_centavos=800,
        costo_actualizado_en=BASE,
        activo=False,
    )
    with pytest.raises(ProductoInactivoError):
        validar_producto_para_venta(conexion, "pan-dulce", ahora=BASE)