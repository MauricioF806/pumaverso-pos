"""Carga datos de demostración: usuarios y productos en central, y existencias en la tienda.

Uso (con central y la tienda levantados):

    python scripts/cargar_datos_demo.py

Opciones:

    --central URL      Servidor central (por defecto http://127.0.0.1:8001)
    --tienda URL       Nodo de la sucursal (por defecto http://127.0.0.1:8000)
    --existencias N    Deja N piezas de cada producto en la tienda (por defecto 24; 0 = no tocar)
    --sin-tienda       Solo carga central; la tienda recibirá los datos en su próxima sincronización

Llaves (decisión 0010): lee del ``.env`` la llave de administración
(``CENTRAL_LLAVE_ADMIN``) para hablar con central, y registra en central la llave
de la sucursal (``LLAVE_CENTRAL``) para que la tienda pueda sincronizar. Por eso
hay que correrlo desde la raíz del repo.

Se puede correr varias veces: actualiza usuarios y productos, y solo agrega las
piezas que falten para llegar a N.
"""

import argparse
import sys
from dataclasses import dataclass

import httpx
from pumaverso_tienda.usuarios import generar_hash_pin
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class UsuarioDemo:
    usuario_id: str
    nombre: str
    rol: str
    pin: str


@dataclass(frozen=True)
class ProductoDemo:
    sku: str
    nombre: str
    precio_centavos: int
    costo_centavos: int


USUARIOS = (
    UsuarioDemo("cajero1", "Ana López", "cajero", "1234"),
    UsuarioDemo("cajero2", "Luis Pérez", "cajero", "5678"),
    UsuarioDemo("gerente1", "Rosa Díaz", "gerente", "9999"),
    # Los ajustes los aprueba alguien distinto de quien los registra (decisión 0006).
    UsuarioDemo("supervisor1", "Carlos Ruiz", "supervisor", "4321"),
)

PRODUCTOS = (
    ProductoDemo("7501055300846", "Refresco de cola 600 ml", 2100, 1450),
    ProductoDemo("7501055310883", "Refresco de cola lata 355 ml", 1700, 1150),
    ProductoDemo("7501086801046", "Agua natural 1 L", 1400, 800),
    ProductoDemo("7501011101456", "Papas fritas sal 45 g", 2200, 1500),
    ProductoDemo("7501011115316", "Totopos sabor queso 62 g", 2400, 1650),
    ProductoDemo("7501000112548", "Pastelito relleno de fresa", 2000, 1300),
    ProductoDemo("7501000300013", "Pan blanco grande", 5600, 4100),
    ProductoDemo("7501020515343", "Leche entera 1 L", 3000, 2350),
    ProductoDemo("7502252480123", "Café americano 12 oz", 2500, 900),
    ProductoDemo("7501008042953", "Galletas de chocolate", 1900, 1250),
    ProductoDemo("7501058715883", "Chicles de menta", 1200, 700),
    ProductoDemo("7501035911208", "Bebida energética 473 ml", 3900, 2800),
)

PROVEEDOR_DEMO = "PROV-DEMO"


class ErrorDemo(RuntimeError):
    """Algo impidió cargar los datos; el mensaje explica qué revisar."""


class Llaves(BaseSettings):
    """Lo que el script necesita del ``.env``: las mismas variables que usan las apps."""

    central_llave_admin: str | None = None
    llave_central: str | None = None
    sucursal_id: str = "S001"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def cargar_central(
    central: httpx.Client, *, sucursal_id: str = "S001", llave_sucursal: str | None = None
) -> None:
    """Da de alta (o actualiza) los usuarios y productos de la demo en central.

    Con ``llave_sucursal``, también la registra para ``sucursal_id``. ``central``
    debe llevar la llave de administración.
    """
    for usuario in USUARIOS:
        _verificar(
            central.put(
                f"/usuarios/{usuario.usuario_id}",
                json={
                    "usuario_id": usuario.usuario_id,
                    "nombre": usuario.nombre,
                    "rol": usuario.rol,
                    "hash_pin": generar_hash_pin(usuario.pin),
                    "activo": True,
                },
            ),
            f"guardar el usuario {usuario.usuario_id} en central",
        )
    for producto in PRODUCTOS:
        _verificar(
            central.put(
                f"/catalogo/productos/{producto.sku}",
                json={
                    "sku": producto.sku,
                    "nombre": producto.nombre,
                    "precio_centavos": producto.precio_centavos,
                    "costo_centavos": producto.costo_centavos,
                    "activo": True,
                },
            ),
            f"guardar el producto {producto.sku} en central",
        )
    if llave_sucursal:
        _verificar(
            central.put(f"/sucursales/{sucursal_id}/llave", json={"llave": llave_sucursal}),
            f"registrar la llave de la sucursal {sucursal_id} en central",
        )


def cargar_existencias(tienda: httpx.Client, objetivo: int) -> int:
    """Sincroniza la tienda y registra una recepción para llegar a ``objetivo`` piezas.

    Devuelve cuántas piezas se recibieron en total (0 si ya había suficientes).
    """
    sync = tienda.post("/sincronizacion/ejecutar")
    if sync.status_code not in (
        200,
        409,
    ):  # 409: hubo conflicto al subir, pero la bajada sí se aplicó
        _verificar(sync, "sincronizar la tienda con central")

    gerente = next(u for u in USUARIOS if u.rol == "gerente")
    login = tienda.post("/auth/login", json={"usuario_id": gerente.usuario_id, "pin": gerente.pin})
    _verificar(login, f"iniciar sesión como {gerente.usuario_id} en la tienda")
    sesion = {"Authorization": f"Bearer {login.json()['token']}"}

    catalogo = tienda.get("/catalogo", params={"limite": 100})
    _verificar(catalogo, "consultar el catálogo de la tienda")
    existencias = {p["sku"]: p["existencia"] for p in catalogo.json()}

    partidas = []
    for producto in PRODUCTOS:
        if producto.sku not in existencias:
            raise ErrorDemo(
                f"La tienda no tiene el producto {producto.sku}: la sincronización con central "
                "no trajo el catálogo. Revisa CENTRAL_URL en el .env de la tienda."
            )
        faltan = objetivo - existencias[producto.sku]
        if faltan > 0:
            partidas.append(
                {
                    "sku": producto.sku,
                    "cantidad": faltan,
                    "costo_unitario_centavos": producto.costo_centavos,
                }
            )

    if partidas:
        _verificar(
            tienda.post(
                "/inventario/recepciones",
                headers=sesion,
                json={
                    "proveedor_id": PROVEEDOR_DEMO,
                    "folio_documento": "DEMO-INICIAL",
                    "partidas": partidas,
                },
            ),
            "registrar la recepción de mercancía en la tienda",
        )
    tienda.post("/auth/logout", headers=sesion)
    return sum(p["cantidad"] for p in partidas)


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga datos de demostración de Pumaverso POS.")
    parser.add_argument("--central", default="http://127.0.0.1:8001")
    parser.add_argument("--tienda", default="http://127.0.0.1:8000")
    parser.add_argument("--existencias", type=int, default=24)
    parser.add_argument("--sin-tienda", action="store_true")
    args = parser.parse_args(argumentos)
    llaves = Llaves()
    encabezados = (
        {"Authorization": f"Bearer {llaves.central_llave_admin}"}
        if llaves.central_llave_admin
        else {}
    )

    try:
        with httpx.Client(base_url=args.central, timeout=10, headers=encabezados) as central:
            cargar_central(
                central, sucursal_id=llaves.sucursal_id, llave_sucursal=llaves.llave_central
            )
        print(f"Central ({args.central}): {len(USUARIOS)} usuarios y {len(PRODUCTOS)} productos.")
        if llaves.llave_central:
            print(f"Llave de la sucursal {llaves.sucursal_id} registrada en central.")
        else:
            print(
                "Aviso: no hay LLAVE_CENTRAL en el .env; la tienda no podrá sincronizar.",
                file=sys.stderr,
            )

        if not args.sin_tienda and args.existencias > 0:
            with httpx.Client(base_url=args.tienda, timeout=30) as tienda:
                recibidas = cargar_existencias(tienda, args.existencias)
            print(
                f"Tienda ({args.tienda}): {args.existencias} piezas de cada producto "
                f"({recibidas} recibidas ahora)."
            )
    except httpx.ConnectError as error:
        print(
            f"No se pudo conectar: {error}. ¿Están levantados central y la tienda?", file=sys.stderr
        )
        return 1
    except ErrorDemo as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print("\nUsuarios para la demo:")
    print(f"  {'Usuario':<10} {'PIN':<6} {'Rol':<8} Nombre")
    for usuario in USUARIOS:
        print(f"  {usuario.usuario_id:<10} {usuario.pin:<6} {usuario.rol:<8} {usuario.nombre}")
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
                " Revisa que CENTRAL_LLAVE_ADMIN esté en el .env y que corras el script "
                "desde la raíz del repo."
            )
        raise ErrorDemo(f"No se pudo {que} (HTTP {respuesta.status_code}): {detalle}{pista}")


if __name__ == "__main__":
    sys.exit(main())
