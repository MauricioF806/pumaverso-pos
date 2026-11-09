from fastapi import FastAPI

from pumaverso_tienda.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="API del monolito local de cada sucursal.",
    version=settings.app_version,
)


@app.get("/health", tags=["Salud"])
def health_check() -> dict[str, str]:
    """Verifica que el servicio local esté funcionando."""
    return {
        "status": "ok",
        "service": "pumaverso-tienda",
    }
