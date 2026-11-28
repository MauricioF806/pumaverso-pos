"""Configuración del servidor central de Pumaverso POS."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pumaverso_central.fantasma import ParametrosFantasma


class CentralSettings(BaseSettings):
    """Parámetros de conexión y servicio para apps/central."""

    app_name: str = "Pumaverso POS - Servidor central"
    app_version: str = "0.1.0"
    database_url: str = "postgresql://pumaverso:pumaverso@127.0.0.1:5432/pumaverso_central"

    # Llave de administración (decisión 0010). Sin ella nadie puede administrar
    # central: dar de alta usuarios y productos, ver la cola ni registrar llaves.
    llave_admin: str | None = Field(default=None, min_length=32)

    # Límite de tasa por sucursal (decisión 0012). 0 = sin límite.
    limite_por_minuto: int = Field(default=180, ge=0)
    limite_rafaga: int = Field(default=60, ge=1)

    # Procesamiento automático de la cola (decisión 0007).
    cola_automatica: bool = True
    cola_intervalo_segundos: float = Field(default=5.0, gt=0)
    cola_lote: int = Field(default=50, ge=1, le=1000)

    # Detección de inventario fantasma (decisión 0009).
    fantasma_automatico: bool = True
    fantasma_intervalo_segundos: float = Field(default=900.0, gt=0)
    fantasma_zona_horaria: str = "America/Mexico_City"
    fantasma_horas_por_franja: int = Field(default=4, ge=1, le=24)
    fantasma_dias_historia: int = Field(default=28, ge=1, le=365)
    fantasma_dias_minimos: int = Field(default=7, ge=1)
    fantasma_significancia: float = Field(default=0.01, gt=0, lt=1)
    fantasma_horas_para_escalar: float = Field(default=24.0, gt=0)

    def parametros_fantasma(self) -> ParametrosFantasma:
        return ParametrosFantasma(
            zona_horaria=self.fantasma_zona_horaria,
            horas_por_franja=self.fantasma_horas_por_franja,
            dias_historia=self.fantasma_dias_historia,
            dias_minimos=self.fantasma_dias_minimos,
            significancia=self.fantasma_significancia,
            horas_para_escalar=self.fantasma_horas_para_escalar,
        )

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
