"""Sincronización automática en segundo plano (decisión 0007)."""

import asyncio
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from pumaverso_tienda.api.dependencias import obtener_transporte_central
from pumaverso_tienda.catalogo import guardar_producto
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.main import app
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones
from pumaverso_tienda.pos import ItemVentaSolicitado, registrar_venta
from pumaverso_tienda.sincronizacion import (
    ActualizacionUsuario,
    ConflictoSincronizacionError,
    ErrorSincronizacion,
    PaqueteBajada,
    RespuestaSubida,
    TrabajadorSincronizacion,
    estado_sincronizacion_caja,
)
from pumaverso_tienda.usuarios import (
    UsuarioInactivoError,
    autenticar_usuario,
    generar_hash_pin,
    guardar_usuario_local,
)

INTERVALO = 60.0
ESPERA_MAXIMA = 300.0


class RngTope:
    """Generador que siempre devuelve el máximo permitido, para pruebas exactas."""

    def uniform(self, _minimo: float, maximo: float) -> float:
        return maximo


class CentralFalso:
    """Central de prueba: puede estar caído, en conflicto o respondiendo bien."""

    def __init__(self, *, modo: str = "ok", bajada: PaqueteBajada | None = None) -> None:
        self.modo = modo
        self.bajada = bajada or PaqueteBajada()
        self.lotes_recibidos: list[int] = []

    def descargar_actualizaciones(self, sucursal_id, desde):
        if self.modo == "caido":
            raise ErrorSincronizacion("Central sin conexión")
        return self.bajada

    def subir_eventos(self, sucursal_id, caja_id, eventos):
        if self.modo == "conflicto":
            raise ConflictoSincronizacionError("Conflicto de hash en S001/C01#1.")
        self.lotes_recibidos.append(len(eventos))
        return RespuestaSubida(ultimo_consecutivo_confirmado=eventos[-1].consecutivo)


@pytest.fixture
def tienda(ruta_bd):
    """Base local con un cajero vigente y un producto."""
    con = abrir_conexion(ruta_bd)
    aplicar_migraciones(con)
    ahora = datetime.now(UTC)
    guardar_usuario_local(
        con,
        usuario_id="cajero-1",
        nombre="Ana",
        rol="cajero",
        hash_pin=generar_hash_pin("1234"),
        actualizado_en=ahora,
    )
    guardar_producto(
        con,
        sku="coca-600",
        nombre="Coca",
        precio_centavos=1800,
        precio_actualizado_en=ahora,
        costo_centavos=1100,
        costo_actualizado_en=ahora,
    )
    yield con
    con.close()


def _trabajador(ruta_bd, central, *, limite_lote: int = 100) -> TrabajadorSincronizacion:
    return TrabajadorSincronizacion(
        abrir_conexion=lambda: abrir_conexion(ruta_bd),
        crear_transporte=lambda: central,
        sucursal_id="S001",
        caja_id="C01",
        limite_lote=limite_lote,
        intervalo_segundos=INTERVALO,
        espera_maxima_segundos=ESPERA_MAXIMA,
        rng=RngTope(),
    )


def _vender(con, veces: int = 1) -> None:
    for _ in range(veces):
        registrar_venta(
            con,
            sucursal_id="S001",
            caja_id="C01",
            usuario_id="cajero-1",
            items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        )


def test_con_central_disponible_espera_el_intervalo_normal(tienda, ruta_bd):
    _vender(tienda)
    trabajador = _trabajador(ruta_bd, CentralFalso())

    espera = trabajador.ejecutar_ciclo()

    assert espera == INTERVALO
    assert trabajador.estado.ultimo_exito_en is not None
    assert trabajador.estado.fallos_consecutivos == 0
    assert estado_sincronizacion_caja(tienda, "S001", "C01")["pendientes"] == 0


def test_sin_central_la_espera_crece_hasta_el_tope(tienda, ruta_bd):
    trabajador = _trabajador(ruta_bd, CentralFalso(modo="caido"))

    esperas = [trabajador.ejecutar_ciclo() for _ in range(11)]

    assert esperas[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert esperas[-1] == ESPERA_MAXIMA
    assert trabajador.estado.fallos_consecutivos == 11
    assert "Central sin conexión" in trabajador.estado.ultimo_error


def test_la_espera_tiene_margen_aleatorio(tienda, ruta_bd):
    """Con el generador real, las esperas varían: las tiendas no reintentan a la vez."""
    trabajador = _trabajador(ruta_bd, CentralFalso(modo="caido"))
    trabajador._rng = None  # generador real
    for _ in range(5):
        trabajador.ejecutar_ciclo()

    esperas = {trabajador.ejecutar_ciclo() for _ in range(5)}

    assert len(esperas) > 1
    assert all(0 <= e <= ESPERA_MAXIMA for e in esperas)


def test_al_volver_central_se_recupera_y_envia_lo_acumulado(tienda, ruta_bd):
    central = CentralFalso(modo="caido")
    trabajador = _trabajador(ruta_bd, central)
    _vender(tienda, veces=3)
    trabajador.ejecutar_ciclo()
    trabajador.ejecutar_ciclo()

    central.modo = "ok"
    espera = trabajador.ejecutar_ciclo()

    assert espera == INTERVALO
    assert trabajador.estado.fallos_consecutivos == 0
    assert trabajador.estado.ultimo_error is None
    assert central.lotes_recibidos == [3]
    assert estado_sincronizacion_caja(tienda, "S001", "C01")["pendientes"] == 0


def test_si_quedan_pendientes_sigue_casi_de_inmediato(tienda, ruta_bd):
    _vender(tienda, veces=5)
    central = CentralFalso()
    trabajador = _trabajador(ruta_bd, central, limite_lote=2)

    esperas = [trabajador.ejecutar_ciclo() for _ in range(3)]

    assert esperas == [1.0, 1.0, INTERVALO]
    assert central.lotes_recibidos == [2, 2, 1]


def test_un_conflicto_no_se_reintenta_rapido_pero_sigue_recibiendo_bajas(tienda, ruta_bd):
    _vender(tienda)
    baja = PaqueteBajada(
        usuarios=(
            ActualizacionUsuario(
                usuario_id="cajero-1",
                nombre="Ana",
                rol="cajero",
                hash_pin=generar_hash_pin("1234"),
                activo=False,
                actualizado_en=datetime.now(UTC),
            ),
        )
    )
    trabajador = _trabajador(ruta_bd, CentralFalso(modo="conflicto", bajada=baja))

    espera = trabajador.ejecutar_ciclo()

    assert espera == INTERVALO
    assert trabajador.estado.en_conflicto is True
    assert trabajador.estado.fallos_consecutivos == 0
    with pytest.raises(UsuarioInactivoError):
        autenticar_usuario(tienda, "cajero-1", "1234")


def test_un_error_inesperado_no_detiene_al_trabajador(tienda, ruta_bd):
    def transporte_roto():
        raise RuntimeError("configuración inválida")

    trabajador = _trabajador(ruta_bd, CentralFalso())
    trabajador._crear_transporte = transporte_roto

    espera = trabajador.ejecutar_ciclo()

    assert espera == 1.0
    assert "configuración inválida" in trabajador.estado.ultimo_error


def test_el_ciclo_continuo_se_detiene_al_pedirlo(tienda, ruta_bd):
    central = CentralFalso()
    trabajador = _trabajador(ruta_bd, central)
    trabajador._intervalo = 0.01
    _vender(tienda)

    async def correr() -> None:
        detener = asyncio.Event()
        tarea = asyncio.create_task(trabajador.ejecutar_siempre(detener))
        await asyncio.sleep(0.2)
        detener.set()
        await asyncio.wait_for(tarea, timeout=2)

    asyncio.run(correr())

    assert central.lotes_recibidos == [1]
    assert trabajador.estado.ultimo_exito_en is not None


def test_la_app_arranca_el_trabajador_y_muestra_su_estado(tmp_path, monkeypatch):
    ruta = tmp_path / "datos" / "pumaverso.db"
    monkeypatch.setenv("DATABASE_PATH", str(ruta))
    monkeypatch.setenv("SYNC_AUTOMATICA", "true")
    monkeypatch.setenv("SYNC_INTERVALO_SEGUNDOS", "0.05")
    get_settings.cache_clear()
    central = CentralFalso()
    app.dependency_overrides[obtener_transporte_central] = lambda: central
    try:
        with TestClient(app) as cliente:
            limite = time.monotonic() + 5
            estado = cliente.get("/sincronizacion/estado").json()
            while estado["trabajador"]["ultimo_exito_en"] is None and time.monotonic() < limite:
                time.sleep(0.05)
                estado = cliente.get("/sincronizacion/estado").json()

        assert estado["sincronizacion_automatica"] is True
        assert estado["trabajador"]["ultimo_exito_en"] is not None
        assert estado["trabajador"]["en_conflicto"] is False
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_sin_sincronizacion_automatica_el_estado_lo_indica(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "datos" / "pumaverso.db"))
    get_settings.cache_clear()
    try:
        with TestClient(app) as cliente:
            estado = cliente.get("/sincronizacion/estado").json()
        assert estado["sincronizacion_automatica"] is False
        assert estado["trabajador"] is None
    finally:
        get_settings.cache_clear()
