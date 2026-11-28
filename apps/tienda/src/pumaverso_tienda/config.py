from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Identificadores cortos sin espacios: letras, números, guion y guion bajo.
PATRON_IDENTIFICADOR = r"^[A-Za-z0-9_-]{1,32}$"


class Settings(BaseSettings):
    """Configuración del nodo local de Pumaverso POS."""

    app_name: str = "Pumaverso POS - Nodo local"
    app_version: str = "0.1.0"
    environment: str = "development"

    database_path: Path = Path("data/pumaverso.db")

    # Identidad del nodo: forma parte de la identidad de cada evento.
    sucursal_id: str = Field(default="S001", pattern=PATRON_IDENTIFICADOR)
    # En V1 hay una sola caja por sucursal (decisión 0004).
    caja_id: str = Field(default="C01", pattern=PATRON_IDENTIFICADOR)

    # Parámetros de sincronización con el servidor central.
    central_url: str = "http://127.0.0.1:8001"
    # Llave de esta sucursal para hablar con central (decisión 0010). La entrega
    # el administrador de central; sin ella central rechaza la sincronización.
    llave_central: str | None = None
    sync_lote_maximo: int = Field(default=100, ge=1, le=1000)

    # Sincronización automática en segundo plano (decisión 0007).
    sync_automatica: bool = True
    sync_intervalo_segundos: float = Field(default=60.0, gt=0)
    # Tope de espera entre reintentos cuando central no responde.
    sync_espera_maxima_segundos: float = Field(default=300.0, gt=0)

    # Orígenes del navegador que pueden llamar a esta API (pantalla de caja,
    # decisión 0008). En .env se escribe como lista JSON.
    cors_origenes: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Obtiene una instancia reutilizable de la configuración."""
    return Settings()
