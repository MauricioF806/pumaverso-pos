"""Registro de eventos append-only de la sucursal y su estado de sincronización."""

from pumaverso_tienda.eventos.pendientes import (
    ConfirmacionInvalidaError,
    confirmar_hasta,
    eventos_pendientes,
    ultimo_consecutivo_confirmado,
)
from pumaverso_tienda.eventos.registro import (
    VERSION_ESQUEMA_ACTUAL,
    ContenidoInvalidoError,
    Evento,
    FueraDeTransaccionError,
    TipoEvento,
    calcular_hash,
    listar_eventos,
    obtener_evento,
    registrar_evento,
    serializar_contenido,
    ultimo_consecutivo,
    verificar_hash,
)

__all__ = [
    "VERSION_ESQUEMA_ACTUAL",
    "ConfirmacionInvalidaError",
    "ContenidoInvalidoError",
    "Evento",
    "FueraDeTransaccionError",
    "TipoEvento",
    "calcular_hash",
    "confirmar_hasta",
    "eventos_pendientes",
    "listar_eventos",
    "obtener_evento",
    "registrar_evento",
    "serializar_contenido",
    "ultimo_consecutivo",
    "ultimo_consecutivo_confirmado",
    "verificar_hash",
]
