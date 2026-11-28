"""Simula inventario fantasma en la sucursal S001 para la demo (decisión 0009).

Le da a la sucursal 4 semanas de historial de ventas. Ayer, a partir del
mediodía, la leche dejó de venderse aunque el sistema dice que hay piezas:
alguien la acomodó en la bodega y el anaquel quedó vacío. Después pide a
central que revise y muestra la sospecha, que el gerente ve en la pantalla de
la caja (pestaña Inventario).

Uso (con central levantado y después de ``cargar_datos_demo.py``):

    python scripts/simular_fantasma.py

Opciones:

    --central URL    Servidor central (por defecto http://127.0.0.1:8001)
    --sucursal ID    Sucursal (por defecto S001, la de la tienda de la demo)
    --caja ID        Caja donde se guarda el historial (por defecto H + mes y día, p. ej. H1127)
    --dias N         Días de historia (por defecto 28)

El historial va a una caja aparte, nunca a la caja real (C01): así no choca con
sus consecutivos. Solo se usa para la demo; en V1 una sucursal tiene una caja
(decisión 0004). La tienda no se entera de estas ventas: su inventario local no
cambia.

Usa la llave de administración de central (``CENTRAL_LLAVE_ADMIN`` del
``.env``), así que hay que correrlo desde la raíz del repo.

Se puede correr varias veces el mismo día: los eventos son idénticos y central
no los duplica. Cada día usa una caja de historial distinta.
"""

import argparse
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from pumaverso_contratos import (
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
from pydantic_settings import BaseSettings, SettingsConfigDict

ZONA = ZoneInfo("America/Mexico_City")
LECHE = "7501020515343"
SEMILLA = 2026
EVENTOS_POR_LOTE = 500


@dataclass(frozen=True)
class Ritmo:
    sku: str
    piezas_por_franja: float


# Piezas que se venden normalmente en cada franja de 4 horas (8-12, 12-16, 16-20).
RITMOS = (
    Ritmo("7501055300846", 8),  # Refresco de cola 600 ml
    Ritmo("7501086801046", 5),  # Agua natural 1 L
    Ritmo("7501011101456", 4),  # Papas fritas sal 45 g
    Ritmo(LECHE, 6),  # Leche entera 1 L
    Ritmo("7501000300013", 3),  # Pan blanco grande
    Ritmo("7502252480123", 5),  # Café americano 12 oz
)
FRANJAS = (8, 12, 16)
ESTADOS = {
    "pendiente_conteo": "el gerente debe contar el anaquel",
    "escalada": "nadie contó a tiempo; pasó al responsable regional",
    "resuelta": "resuelta",
}
HORAS_POR_FRANJA = 4


class ErrorSimulacion(RuntimeError):
    """Algo impidió simular; el mensaje explica qué revisar."""


class Llaves(BaseSettings):
    central_llave_admin: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class CajaSimulada:
    """Arma eventos válidos, con el mismo hash que calcularía una tienda real."""

    def __init__(self, sucursal_id: str, caja_id: str) -> None:
        self.sucursal_id = sucursal_id
        self.caja_id = caja_id
        self.eventos: list[EventoContrato] = []

    def _agregar(self, tipo: TipoEvento, contenido, momento: datetime) -> None:
        datos = {
            "sucursal_id": self.sucursal_id,
            "caja_id": self.caja_id,
            "consecutivo": len(self.eventos) + 1,
            "tipo": tipo,
            "version_esquema": 1,
            "contenido": serializar_contenido_evento(contenido),
            "usuario_id": "cajero1",
        }
        huella = calcular_hash_evento(**datos, registrado_en=formatear_registrado_en(momento))
        self.eventos.append(EventoContrato(**datos, registrado_en=momento, hash=huella))

    def recibir(self, momento: datetime, piezas: int, costos: dict[str, int]) -> None:
        partidas = [
            LineaRecepcion(sku=r.sku, cantidad=piezas, costo_unitario_centavos=costos[r.sku])
            for r in RITMOS
        ]
        total = sum(p.cantidad * p.costo_unitario_centavos for p in partidas)
        contenido = ContenidoRecepcionProveedor(
            proveedor_id="PROV-DEMO",
            folio_documento="DEMO-FANTASMA",
            partidas=partidas,
            total_centavos=total,
        )
        self._agregar(TipoEvento.RECEPCION_PROVEEDOR, contenido, momento)

    def vender(self, momento: datetime, sku: str, precio: int) -> None:
        contenido = ContenidoVenta(
            partidas=[
                LineaProducto(
                    sku=sku,
                    cantidad=1,
                    precio_unitario_centavos=precio,
                    subtotal_centavos=precio,
                )
            ],
            total_centavos=precio,
        )
        self._agregar(TipoEvento.VENTA, contenido, momento)


def poisson(rng: random.Random, promedio: float) -> int:
    """Cantidad aleatoria con ese promedio, como las ventas reales de una franja."""
    limite, k, producto = math.exp(-promedio), 0, rng.random()
    while producto > limite:
        k += 1
        producto *= rng.random()
    return k


def simular(
    sucursal_id: str, dias: int, ayer: datetime, catalogo: dict, caja_id: str = "H01"
) -> CajaSimulada:
    """Historia normal de ``dias`` días y, ayer, la leche sin venderse desde el mediodía."""
    rng = random.Random(SEMILLA)
    caja = CajaSimulada(sucursal_id, caja_id)
    costos = {sku: datos["costo_centavos"] for sku, datos in catalogo.items()}
    caja.recibir((ayer - timedelta(days=dias + 1)).replace(hour=7), 2000, costos)

    for atras in range(dias, -1, -1):
        dia = ayer - timedelta(days=atras)
        for hora in FRANJAS:
            ventas: list[tuple[datetime, str]] = []
            for ritmo in RITMOS:
                if atras == 0:
                    piezas = round(ritmo.piezas_por_franja)  # ayer: un día común...
                    if ritmo.sku == LECHE and hora >= 12:
                        piezas = 0  # ...salvo la leche, que ya no estaba en el anaquel
                else:
                    piezas = poisson(rng, ritmo.piezas_por_franja)
                for _ in range(piezas):
                    minuto = rng.randrange(HORAS_POR_FRANJA * 60)
                    ventas.append((dia.replace(hour=hora) + timedelta(minutes=minuto), ritmo.sku))
            for momento, sku in sorted(ventas):
                caja.vender(momento, sku, catalogo[sku]["precio_centavos"])

    # Un último cliente después de las 20 h: así central sabe que ya tiene todo lo de ayer.
    refresco = RITMOS[0].sku
    caja.vender(ayer.replace(hour=20, minute=5), refresco, catalogo[refresco]["precio_centavos"])
    return caja


def leer_catalogo(central: httpx.Client, sucursal_id: str) -> dict:
    respuesta = central.get("/sincronizacion/actualizaciones", params={"sucursal_id": sucursal_id})
    _verificar(respuesta, "leer el catálogo de central")
    catalogo = {p["sku"]: p for p in respuesta.json()["productos"]}
    faltan = [r.sku for r in RITMOS if r.sku not in catalogo]
    if faltan:
        raise ErrorSimulacion(
            "Central no tiene los productos de la demo. Corre primero "
            "`python scripts/cargar_datos_demo.py`."
        )
    return catalogo


def enviar(central: httpx.Client, caja: CajaSimulada) -> None:
    for inicio in range(0, len(caja.eventos), EVENTOS_POR_LOTE):
        lote = caja.eventos[inicio : inicio + EVENTOS_POR_LOTE]
        respuesta = central.post(
            "/sincronizacion/eventos",
            json={
                "sucursal_id": caja.sucursal_id,
                "caja_id": caja.caja_id,
                "eventos": [e.model_dump(mode="json") for e in lote],
            },
        )
        if respuesta.status_code == 409:
            raise ErrorSimulacion(
                f"Central ya tiene otro historial en la caja {caja.caja_id} de "
                f"{caja.sucursal_id}. Usa otra caja, por ejemplo --caja H2."
            )
        _verificar(respuesta, "enviar las ventas simuladas a central")
    while True:
        respuesta = central.post("/sincronizacion/procesar-cola", params={"limite": 1000})
        _verificar(respuesta, "procesar la cola de central")
        if respuesta.json()["procesados"] == 0:
            break


def pesos(centavos: int) -> str:
    return f"${centavos / 100:,.2f}"


def main(argumentos: list[str] | None = None) -> int:
    hoy = datetime.now(ZONA).replace(hour=0, minute=0, second=0, microsecond=0)
    ayer = hoy - timedelta(days=1)

    parser = argparse.ArgumentParser(description="Simula inventario fantasma para la demo.")
    parser.add_argument("--central", default="http://127.0.0.1:8001")
    parser.add_argument("--sucursal", default="S001")
    parser.add_argument("--caja", default=f"H{hoy:%m%d}")
    parser.add_argument("--dias", type=int, default=28)
    args = parser.parse_args(argumentos)

    revision = ayer.replace(hour=20, minute=30)
    try:
        llave = Llaves().central_llave_admin
        encabezados = {"Authorization": f"Bearer {llave}"} if llave else {}
        with httpx.Client(base_url=args.central, timeout=60, headers=encabezados) as central:
            catalogo = leer_catalogo(central, args.sucursal)
            caja = simular(args.sucursal, args.dias, ayer, catalogo, args.caja)
            enviar(central, caja)
            detectar = central.post(
                "/inventario/fantasma/detectar",
                params={"momento": revision.isoformat()},
            )
            _verificar(detectar, "pedir a central que revise el inventario fantasma")
            reporte = central.get(
                "/inventario/fantasma/sospechas", params={"sucursal_id": args.sucursal}
            )
            _verificar(reporte, "leer el reporte de sospechas")
    except httpx.ConnectError as error:
        print(f"No se pudo conectar: {error}. ¿Está levantado central?", file=sys.stderr)
        return 1
    except ErrorSimulacion as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    datos = reporte.json()
    print(
        f"Historial simulado de {args.sucursal} (caja {args.caja}): {args.dias} días, "
        f"{len(caja.eventos)} operaciones enviadas a central."
    )
    print(f"Revisión de ayer ({ayer:%d/%m}) a las {revision:%H:%M}:\n")
    if not datos["sospechas"]:
        print("  Sin sospechas abiertas. Si hoy ya registraste el conteo, la sospecha quedó")
        print("  cerrada; la simulación se puede repetir mañana.")
    for s in datos["sospechas"]:
        inicio = datetime.fromisoformat(s["franja_inicio"]).astimezone(ZONA)
        fin = datetime.fromisoformat(s["franja_fin"]).astimezone(ZONA)
        print(f"  Sospecha de inventario fantasma: {s['nombre']}")
        print(
            f"    Entre {inicio:%H:%M} y {fin:%H:%M} se esperaban unas "
            f"{s['unidades_esperadas']:.1f} piezas y se vendieron {s['unidades_vendidas']}."
        )
        print(f"    Probabilidad de que sea casualidad: {s['probabilidad'] * 100:.2f} %")
        print(f"    Estado: {ESTADOS[s['estado']]}")
    nombres = {s["sku"]: s["nombre"] for s in datos["sospechas"]}
    pedido = [nombres.get(sku, sku) for sku in datos["sugerencia_pedido"]]
    print(f"\n  Agregar al pedido: {', '.join(pedido) or 'nada'}")
    print(f"  Venta perdida estimada: {pesos(datos['venta_perdida_estimada_centavos'])}")
    print(
        "\nEl gerente lo ve en la pantalla de caja: pestaña Inventario, "
        "sección Inventario fantasma."
    )
    return 0


def _verificar(respuesta: httpx.Response, que: str) -> None:
    if respuesta.is_error:
        try:
            detalle = respuesta.json().get("detail", respuesta.text)
        except ValueError:
            detalle = respuesta.text
        pista = ""
        if respuesta.status_code in (401, 403):
            pista = (
                " Revisa CENTRAL_LLAVE_ADMIN en el .env y corre el script desde la raíz del repo."
            )
        raise ErrorSimulacion(f"No se pudo {que} (HTTP {respuesta.status_code}): {detalle}{pista}")


if __name__ == "__main__":
    sys.exit(main())
