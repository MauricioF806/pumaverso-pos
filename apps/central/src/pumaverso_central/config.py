"""Configuración del servidor central de Pumaverso POS."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CentralSettings(BaseSettings):
    """Parámetros de conexión y servicio para apps/central."""

    app_name: str = "Pumaverso POS - Servidor central"
    app_version: str = "0.1.0"
    database_url: str = "postgresql://pumaverso:pumaverso@127.0.0.1:5432/pumaverso_central"

    # Procesamiento automático de la cola (decisión 0007).
    cola_automatica: bool = True
    cola_intervalo_segundos: float = Field(default=5.0, gt=0)
    cola_lote: int = Field(default=50, ge=1, le=1000)

    model_config = SettingsConfigDict(
        env_prefix="CENTRAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_central_settings() -> CentralSettings:
    """Devuelve una instancia reutilizable de la configuración central."""
    return CentralSettings()
