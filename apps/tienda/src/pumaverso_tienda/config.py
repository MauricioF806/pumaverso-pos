from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del nodo local de Pumaverso POS."""

    app_name: str = "Pumaverso POS - Nodo local"
    app_version: str = "0.1.0"
    environment: str = "development"

    database_path: Path = Path("data/pumaverso.db")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Obtiene una instancia reutilizable de la configuración."""
    return Settings()