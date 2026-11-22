from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pumaverso_tienda.config import get_settings
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Al arrancar, crea la base de datos local si no existe y aplica las migraciones."""
    conexion = abrir_conexion(get_settings().database_path)
    try:
        aplicar_migraciones(conexion)
    finally:
        conexion.close()
    yield


app = FastAPI(
    title=settings.app_name,
    description="API del monolito local de cada sucursal.",
    version=settings.app_version,
    lifespan=lifespan,
)


@app.get("/health", tags=["Salud"])
def health_check() -> dict[str, str]:
    """Verifica que el servicio local esté funcionando."""
    return {
        "status": "ok",
        "service": "pumaverso-tienda",
    }
