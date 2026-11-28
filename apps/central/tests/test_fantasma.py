"""Detección de inventario fantasma (PRD 5.3, decisión 0009)."""

import math
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pumaverso_contratos import (
    ContenidoAjuste,
    ContenidoCancelacion,
    ContenidoRecepcionProveedor,
    ContenidoVenta,
    EventoContrato,
    LineaProducto,
    LineaRecepcion,
    TipoEvento,
    calcular_hash_evento,
    formatear_registrado_en,
    serializar_contenido_evento,
)

from pumaverso_central.cola import procesar_eventos_pendientes_cola, recibir_lote_eventos
from pumaverso_central.fantasma import (
    ParametrosFantasma,
    detectar_inventario_fantasma,
    franjas_a_revisar,
    listar_sospechas,
    probabilidad_de_vender_tan_poco,
    umbral_de_sospecha,
)
from pumaverso_central.main import app

ADMIN = {"Authorization": f"Bearer {os.environ['CENTRAL_LLAVE_ADMIN']}"}

MX = ZoneInfo("America/Mexico_City")
HOY = datetime(2026, 11, 25, tzinfo=MX)
LECHE = "leche-1l"
REFRESCO = "refresco-600"
PRECIOS = {LECHE: 3000, REFRESCO: 2100}
PARAMETROS = ParametrosFantasma(dias_historia=14, dias_minimos=7)


def _a_las(dia: datetime, hora: int, minuto: int = 0) -> datetime:
    return dia.replace(hour=hora, minute=minuto)


class Sucursal:
    """Arma eventos válidos de una caja, con su hash real, como los mandaría la tienda."""

    def __init__(self, conn, sucursal_id: str = "S900") -> None:
        self.conn = conn
        self.sucursal_id = sucursal_id
        self.consecutivo = 0
        self.ventas: dict[datetime, int] = {}
        self._lote: list[EventoContrato] = []

    def _evento(self, tipo: TipoEvento, contenido, momento: datetime) -> int:
        self.consecutivo += 1
        datos = {
            "sucursal_id": self.sucursal_id,
            "caja_id": "C01",
            "consecutivo": self.consecutivo,
            "tipo": tipo,
            "version_esquema": 1,
            "contenido": serializar_contenido_evento(contenido),
            "usuario_id": "cajero1",
        }
        huella = calcular_hash_evento(**datos, registrado_en=formatear_registrado_en(momento))
        self._lote.append(EventoContrato(**datos, registrado_en=momento, hash=huella))
        return self.consecutivo

    def recibir(self, momento: datetime, piezas: dict[str, int]) -> None:
        partidas = [
            LineaRecepcion(sku=sku, cantidad=n, costo_unitario_centavos=100)
            for sku, n in piezas.items()
        ]
        contenido = ContenidoRecepcionProveedor(
            proveedor_id="PROV", folio_documento="F-1", partidas=partidas, total_centavos=0
        )
        self._evento(TipoEvento.RECEPCION_PROVEEDOR, contenido, momento)

    def vender(self, momento: datetime, sku: str, cantidad: int = 1) -> int:
        precio = PRECIOS[sku]
        contenido = ContenidoVenta(
            partidas=[
                LineaProducto(
                    sku=sku,
                    cantidad=cantidad,
                    precio_unitario_centavos=precio,
                    subtotal_centavos=precio * cantidad,
                )
            ],
            total_centavos=precio * cantidad,
        )
        return self._evento(TipoEvento.VENTA, contenido, momento)

    def cancelar(self, momento: datetime, consecutivo: int) -> None:
        contenido = ContenidoCancelacion(
            consecutivo_original=consecutivo, motivo="Devolución", aprobado_por="gerente1"
        )
        self._evento(TipoEvento.CANCELACION, contenido, momento)

    def ajustar(self, momento: datetime, sku: str, diferencia: int) -> None:
        contenido = ContenidoAjuste(
            sku=sku, diferencia_cantidad=diferencia, motivo="Conteo", aprobado_por="gerente1"
        )
        self._evento(TipoEvento.AJUSTE, contenido, momento)

    def dia_normal(self, dia: datetime, *, leche: int = 6, refresco: int = 6) -> list[int]:
        """Ventas de la franja de 12 a 16 h: una pieza cada pocos minutos."""
        consecutivos = []
        for i in range(leche):
            consecutivos.append(self.vender(_a_las(dia, 12, 10 + i * 5), LECHE))
        for i in range(refresco):
            consecutivos.append(self.vender(_a_las(dia, 13, 10 + i * 5), REFRESCO))
        return consecutivos

    def enviar(self) -> None:
        if self._lote:
            recibir_lote_eventos(
                self.conn, sucursal_id=self.sucursal_id, caja_id="C01", eventos=self._lote
            )
            self._lote = []
        while procesar_eventos_pendientes_cola(self.conn, limite=500).procesados:
            pass


@pytest.fixture
def catalogo(conn_central):
    with conn_central.transaction():
        for sku, precio in PRECIOS.items():
            conn_central.execute(
                """
                INSERT INTO catalogo.productos (sku, nombre, precio_centavos, costo_centavos)
                VALUES (%s, %s, %s, 100)
                """,
                (sku, f"Producto {sku}", precio),
            )
    return conn_central


@pytest.fixture
def sucursal(catalogo):
    """Sucursal con dos semanas de ventas normales y mucho inventario registrado."""
    s = Sucursal(catalogo)
    s.recibir(HOY - timedelta(days=30), {LECHE: 500, REFRESCO: 500})
    for dias in range(14, 0, -1):
        s.dia_normal(HOY - timedelta(days=dias))
    return s


def _detectar(conn, ahora: datetime, parametros: ParametrosFantasma = PARAMETROS):
    return detectar_inventario_fantasma(conn, parametros=parametros, ahora=ahora)


# --- La matemática del umbral ---------------------------------------------------------


def test_probabilidad_de_no_vender_nada_con_un_ritmo_de_seis():
    assert probabilidad_de_vender_tan_poco(0, 6.0) == pytest.approx(math.exp(-6))


def test_el_umbral_depende_del_ritmo_de_cada_producto():
    assert umbral_de_sospecha(6.0, 0.01) == 0  # vender 0 es raro; vender 1 no tanto
    assert umbral_de_sospecha(20.0, 0.01) == 9  # con ritmo de 20, vender 9 o menos ya es raro
    assert umbral_de_sospecha(0.5, 0.01) is None  # se vende tan poco que no se distingue


def test_revisa_las_franjas_terminadas_de_las_ultimas_24_horas():
    franjas = franjas_a_revisar(_a_las(HOY, 16, 30), PARAMETROS)

    assert len(franjas) == 6
    assert franjas[0].inicio == _a_las(HOY - timedelta(days=1), 16)
    assert franjas[-1].inicio == _a_las(HOY, 12)
    assert franjas[-1].fin == _a_las(HOY, 16)


def test_las_horas_por_franja_deben_dividir_el_dia():
    with pytest.raises(ValueError, match="dividir 24"):
        ParametrosFantasma(horas_por_franja=5)


# --- Detección ------------------------------------------------------------------------


def test_detecta_el_producto_que_dejo_de_venderse_aunque_hay_existencias(sucursal, conn_central):
    sucursal.dia_normal(HOY, leche=0)  # hoy no se vendió leche; refresco normal
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)  # ya hay datos después de la franja
    sucursal.enviar()

    resultado = _detectar(conn_central, _a_las(HOY, 16, 30))

    assert [n["sku"] for n in resultado.nuevas] == [LECHE]
    sospecha = listar_sospechas(conn_central)[0]
    assert sospecha["estado"] == "pendiente_conteo"
    assert sospecha["unidades_esperadas"] == 6.0
    assert sospecha["unidades_vendidas"] == 0
    assert sospecha["umbral_unidades"] == 0
    assert sospecha["existencia_registrada"] == 500 - 14 * 6
    assert sospecha["venta_perdida_estimada_centavos"] == 6 * 3000
    assert sospecha["franja_inicio"] == _a_las(HOY, 12)


def test_una_venta_baja_pero_posible_no_es_sospecha(sucursal, conn_central):
    sucursal.dia_normal(HOY, leche=2)
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()

    assert _detectar(conn_central, _a_las(HOY, 16, 30)).nuevas == []


def test_no_es_fantasma_si_el_sistema_ya_sabe_que_no_hay(catalogo, conn_central):
    sucursal = Sucursal(catalogo)
    sucursal.recibir(HOY - timedelta(days=30), {LECHE: 14 * 6, REFRESCO: 500})
    for dias in range(14, 0, -1):
        sucursal.dia_normal(HOY - timedelta(days=dias))
    sucursal.dia_normal(HOY, leche=0)
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()

    assert _detectar(conn_central, _a_las(HOY, 16, 30)).nuevas == []


def test_las_ventas_canceladas_no_cuentan(sucursal, conn_central):
    for consecutivo in sucursal.dia_normal(HOY, refresco=6)[:6]:  # las 6 leches
        sucursal.cancelar(_a_las(HOY, 15, 50), consecutivo)
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()

    assert [n["sku"] for n in _detectar(conn_central, _a_las(HOY, 16, 30)).nuevas] == [LECHE]


def test_sin_historia_suficiente_no_hay_sospechas(catalogo, conn_central):
    sucursal = Sucursal(catalogo)
    sucursal.recibir(HOY - timedelta(days=30), {LECHE: 500, REFRESCO: 500})
    for dias in range(3, 0, -1):
        sucursal.dia_normal(HOY - timedelta(days=dias))
    sucursal.dia_normal(HOY, leche=0)
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()

    assert _detectar(conn_central, _a_las(HOY, 16, 30)).nuevas == []


def test_espera_a_que_la_sucursal_envie_sus_ventas_antes_de_evaluar(sucursal, conn_central):
    """Una sucursal sin conexión parece no vender: no se evalúa hasta que lleguen sus datos."""
    sucursal.enviar()  # solo la historia: lo de hoy sigue en la tienda

    sin_datos = _detectar(conn_central, _a_las(HOY, 16, 30))

    assert sin_datos.nuevas == []
    assert _a_las(HOY, 12).isoformat() in [p["franja_inicio"] for p in sin_datos.franjas_pendientes]

    sucursal.dia_normal(HOY, leche=0)  # la tienda se reconecta y envía lo de hoy
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()

    assert [n["sku"] for n in _detectar(conn_central, _a_las(HOY, 16, 45)).nuevas] == [LECHE]


def test_correrla_varias_veces_no_duplica(sucursal, conn_central):
    sucursal.dia_normal(HOY, leche=0)
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()

    _detectar(conn_central, _a_las(HOY, 16, 30))
    segunda = _detectar(conn_central, _a_las(HOY, 16, 50))

    assert segunda.nuevas == []
    assert len(listar_sospechas(conn_central)) == 1


def test_mientras_siga_abierta_la_venta_perdida_crece(sucursal, conn_central):
    manana = HOY + timedelta(days=1)
    sucursal.dia_normal(HOY, leche=0)
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()
    _detectar(conn_central, _a_las(HOY, 16, 30))

    sucursal.dia_normal(manana, leche=0)
    sucursal.vender(_a_las(manana, 16, 5), REFRESCO)
    sucursal.enviar()
    resultado = _detectar(conn_central, _a_las(manana, 16, 30))

    sospecha = listar_sospechas(conn_central)[0]
    assert resultado.nuevas == []
    assert sospecha["franjas_afectadas"] == 2
    assert sospecha["venta_perdida_estimada_centavos"] > 6 * 3000


# --- Seguimiento: escalar, contar, ajustar ---------------------------------------------


@pytest.fixture
def con_sospecha(sucursal, conn_central):
    sucursal.dia_normal(HOY, leche=0)
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()
    _detectar(conn_central, _a_las(HOY, 16, 30))
    return listar_sospechas(conn_central)[0]


def test_si_nadie_cuenta_a_tiempo_se_escala(con_sospecha, conn_central):
    antes = _detectar(conn_central, _a_las(HOY, 16, 30) + timedelta(hours=23))
    despues = _detectar(conn_central, _a_las(HOY, 16, 30) + timedelta(hours=25))

    assert antes.escaladas == 0
    assert despues.escaladas == 1
    assert listar_sospechas(conn_central)[0]["estado"] == "escalada"


def test_el_reporte_dice_que_contar_que_pedir_y_cuanto_se_pierde(con_sospecha):
    reporte = TestClient(app, headers=ADMIN).get("/inventario/fantasma/sospechas").json()

    assert reporte["sugerencia_pedido"] == [LECHE]
    assert reporte["venta_perdida_estimada_centavos"] == 6 * 3000
    assert reporte["sospechas"][0]["nombre"] == f"Producto {LECHE}"


def test_el_conteo_cierra_la_sospecha_y_dice_cuanto_ajustar(con_sospecha, conn_central):
    cliente = TestClient(app, headers=ADMIN)
    ruta = f"/inventario/fantasma/sospechas/{con_sospecha['id']}/conteo"

    conteo = cliente.post(ruta, json={"cantidad_contada": 0, "usuario_id": "gerente1"})
    otra_vez = cliente.post(ruta, json={"cantidad_contada": 0, "usuario_id": "gerente1"})

    assert conteo.status_code == 200
    assert conteo.json()["diferencia"] == -(500 - 14 * 6)
    assert otra_vez.status_code == 404
    assert listar_sospechas(conn_central) == []
    cerrada = listar_sospechas(conn_central, solo_abiertas=False)[0]
    assert (cerrada["resolucion"], cerrada["cantidad_contada"]) == ("conteo", 0)


def test_un_ajuste_de_la_tienda_cierra_la_sospecha(con_sospecha, sucursal, conn_central):
    sucursal.ajustar(_a_las(HOY, 17), LECHE, -(500 - 14 * 6))
    sucursal.enviar()

    cerrada = listar_sospechas(conn_central, solo_abiertas=False)[0]
    assert cerrada["estado"] == "resuelta"
    assert cerrada["resolucion"] == "ajuste"


def test_la_api_permite_revisar_como_si_fuera_otra_hora(sucursal):
    sucursal.dia_normal(HOY, leche=0)
    sucursal.vender(_a_las(HOY, 16, 5), REFRESCO)
    sucursal.enviar()
    cliente = TestClient(app, headers=ADMIN)

    sin_zona = cliente.post(
        "/inventario/fantasma/detectar", params={"momento": "2026-11-25T16:30:00"}
    )
    respuesta = cliente.post(
        "/inventario/fantasma/detectar",
        params={"momento": _a_las(HOY, 16, 30).isoformat()},
    )

    assert sin_zona.status_code == 422
    assert [n["sku"] for n in respuesta.json()["sospechas_nuevas"]] == [LECHE]


# --- Script de la demo ------------------------------------------------------------------


def test_el_script_de_la_demo_produce_una_sola_sospecha_de_leche(conn_central):
    pytest.importorskip("pumaverso_tienda", reason="El script de datos usa el hash de PIN.")
    import importlib.util
    from pathlib import Path

    def cargar(nombre):
        ruta = Path(__file__).resolve().parents[3] / "scripts" / f"{nombre}.py"
        especificacion = importlib.util.spec_from_file_location(nombre, ruta)
        modulo = importlib.util.module_from_spec(especificacion)
        especificacion.loader.exec_module(modulo)
        return modulo

    datos_demo, simulacion = cargar("cargar_datos_demo"), cargar("simular_fantasma")
    central = TestClient(app, headers=ADMIN)
    datos_demo.cargar_central(central)
    ayer = datetime(2026, 11, 26, tzinfo=simulacion.ZONA)

    catalogo = simulacion.leer_catalogo(central, "DEMO")
    caja = simulacion.simular("DEMO", 10, ayer, catalogo)
    simulacion.enviar(central, caja)
    simulacion.enviar(central, simulacion.simular("DEMO", 10, ayer, catalogo))  # sin duplicar
    respuesta = central.post(
        "/inventario/fantasma/detectar",
        params={"momento": ayer.replace(hour=20, minute=30).isoformat()},
    )

    reporte = central.get("/inventario/fantasma/sospechas").json()
    assert respuesta.status_code == 200
    assert reporte["sugerencia_pedido"] == [simulacion.LECHE]
    assert reporte["sospechas"][0]["franjas_afectadas"] == 2  # de 12 a 16 y de 16 a 20


def test_central_revisa_sola_al_arrancar(catalogo, conn_central, monkeypatch):
    import time
    from datetime import UTC

    from pumaverso_central.config import get_central_settings

    sucursal = Sucursal(catalogo)
    sucursal.vender(datetime.now(UTC), REFRESCO)  # datos completos de las últimas 24 h
    sucursal.enviar()
    monkeypatch.setenv("CENTRAL_FANTASMA_AUTOMATICO", "true")
    get_central_settings.cache_clear()
    try:
        with TestClient(app, headers=ADMIN):
            limite = time.monotonic() + 5
            evaluadas = 0
            while evaluadas < 6 and time.monotonic() < limite:
                time.sleep(0.05)
                evaluadas = conn_central.execute(
                    "SELECT COUNT(*) AS n FROM inventario.franjas_evaluadas"
                ).fetchone()["n"]
    finally:
        get_central_settings.cache_clear()

    assert evaluadas == 6  # las 6 franjas terminadas de las últimas 24 horas
