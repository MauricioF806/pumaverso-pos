from fastapi import FastAPI

app = FastAPI(
    title="Pumaverso POS - Nodo local",
    description="API del monolito local de cada sucursal.",
    version="0.1.0",
)


@app.get("/health", tags=["Salud"])
def health_check() -> dict[str, str]:
    """Verifica que el servicio local esté funcionando."""
    return {
        "status": "ok",
        "service": "pumaverso-tienda",
    }
