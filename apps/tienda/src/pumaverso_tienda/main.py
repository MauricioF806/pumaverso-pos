import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pumaverso_tienda.api.dependencias import obtener_transporte_central
from pumaverso_tienda.api.routes import api_router
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones
from pumaverso_tienda.sincronizacion import TrabajadorSincronizacion, TransporteCentral

settings = get_settings()


def _crear_transporte() -> TransporteCentral:
    # Respeta las sustituciones de app.dependency_overrides, igual que los endpoints.
    fabrica = app.dependency_overrides.get(obtener_transporte_central, obtener_transporte_central)
    return fabrica()


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    """Prepara la base de datos local y arranca la sincronización automática."""
    configuracion = get_settings()
    conexion = abrir_conexion(configuracion.database_path)
    try:
        aplicar_migraciones(conexion)
    finally:
        conexion.close()

    app_.state.trabajador_sync = None
    tarea: asyncio.Task[None] | None = None
    detener = asyncio.Event()
    if configuracion.sync_automatica:
        trabajador = TrabajadorSincronizacion(
            abrir_conexion=lambda: abrir_conexion(configuracion.database_path),
            crear_transporte=_crear_transporte,
            sucursal_id=configuracion.sucursal_id,
            caja_id=configuracion.caja_id,
            limite_lote=configuracion.sync_lote_maximo,
            intervalo_segundos=configuracion.sync_intervalo_segundos,
            espera_maxima_segundos=configuracion.sync_espera_maxima_segundos,
        )
        app_.state.trabajador_sync = trabajador
        tarea = asyncio.create_task(trabajador.ejecutar_siempre(detener))

    yield

    if tarea is not None:
        detener.set()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea


app = FastAPI(
    title=settings.app_name,
    description="API del monolito local de cada sucursal.",
    version=settings.app_version,
    lifespan=lifespan,
)

app.include_router(api_router)


@app.get("/health", tags=["Salud"])
def health_check() -> dict[str, str]:
    """Verifica que el servicio local esté funcionando."""
    return {
        "status": "ok",
        "service": "pumaverso-tienda",
    }
