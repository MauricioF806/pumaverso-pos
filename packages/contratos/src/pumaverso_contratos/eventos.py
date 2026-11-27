"""Contrato compartido de eventos de Pumaverso POS (decisión 0003).

Define los tipos de evento, la versión de esquema, los modelos Pydantic de
contenido para cada operación y el sobre del evento intercambiado entre
``apps/tienda`` y ``apps/central``.

Todos los importes monetarios se expresan como enteros en centavos.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

VERSION_ESQUEMA_ACTUAL = 1
PATRON_IDENTIFICADOR = r"^[A-Za-z0-9_-]{1,32}$"


class TipoEvento(StrEnum):
    """Tipos de evento definidos en el PRD de Pumaverso POS."""

    VENTA = "venta"
    CANCELACION = "cancelacion"
    RECEPCION_PROVEEDOR = "recepcion_proveedor"
    MERMA = "merma"
    AJUSTE = "ajuste"
    TRASPASO = "traspaso"


class _ModeloEstricto(BaseModel):
    """Base inmutable que rechaza campos desconocidos en el contrato."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LineaProducto(_ModeloEstricto):
    """Partida individual dentro de una venta."""

    sku: str = Field(min_length=1, max_length=64)
    cantidad: int = Field(gt=0)
    precio_unitario_centavos: int = Field(ge=0)
    subtotal_centavos: int = Field(ge=0)


class ContenidoVenta(_ModeloEstricto):
    """Contenido de un evento de venta local."""

    partidas: list[LineaProducto] = Field(min_length=1)
    total_centavos: int = Field(ge=0)
    metodo_pago: Literal["efectivo"] = "efectivo"
    inventario_negativo: bool = False


class ContenidoCancelacion(_ModeloEstricto):
    """Contenido de un evento de cancelación (append-only, decisión 0005)."""

    consecutivo_original: int = Field(gt=0)
    motivo: str = Field(min_length=1, max_length=255)
    aprobado_por: str = Field(pattern=PATRON_IDENTIFICADOR)


class LineaRecepcion(_ModeloEstricto):
    """Partida recibida de un proveedor."""

    sku: str = Field(min_length=1, max_length=64)
    cantidad: int = Field(gt=0)
    costo_unitario_centavos: int = Field(ge=0)


class ContenidoRecepcionProveedor(_ModeloEstricto):
    """Contenido de un evento de recepción de mercancía."""

    proveedor_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    folio_documento: str = Field(min_length=1, max_length=64)
    partidas: list[LineaRecepcion] = Field(min_length=1)
    total_centavos: int = Field(ge=0)


class ContenidoMerma(_ModeloEstricto):
    """Contenido de un evento de merma de inventario."""

    sku: str = Field(min_length=1, max_length=64)
    cantidad: int = Field(gt=0)
    motivo: str = Field(min_length=1, max_length=255)


class ContenidoAjuste(_ModeloEstricto):
    """Contenido de un evento de ajuste de inventario."""

    sku: str = Field(min_length=1, max_length=64)
    diferencia_cantidad: int
    motivo: str = Field(min_length=1, max_length=255)
    aprobado_por: str = Field(pattern=PATRON_IDENTIFICADOR)

    @field_validator("diferencia_cantidad")
    @classmethod
    def _diferencia_no_cero(cls, valor: int) -> int:
        if valor == 0:
            raise ValueError("diferencia_cantidad no puede ser cero.")
        return valor


class LineaTraspaso(_ModeloEstricto):
    """Partida de mercancía transferida entre sucursales."""

    sku: str = Field(min_length=1, max_length=64)
    cantidad: int = Field(gt=0)


class ContenidoTraspaso(_ModeloEstricto):
    """Contenido de un evento de traspaso entre sucursales."""

    direccion: Literal["salida", "entrada"]
    sucursal_contraparte_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    partidas: list[LineaTraspaso] = Field(min_length=1)


MODELOS_CONTENIDO: dict[TipoEvento, type[BaseModel]] = {
    TipoEvento.VENTA: ContenidoVenta,
    TipoEvento.CANCELACION: ContenidoCancelacion,
    TipoEvento.RECEPCION_PROVEEDOR: ContenidoRecepcionProveedor,
    TipoEvento.MERMA: ContenidoMerma,
    TipoEvento.AJUSTE: ContenidoAjuste,
    TipoEvento.TRASPASO: ContenidoTraspaso,
}


class EventoContrato(_ModeloEstricto):
    """Representación validada de un evento intercambiado con central."""

    sucursal_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    caja_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    consecutivo: int = Field(gt=0)
    tipo: TipoEvento
    version_esquema: int = Field(default=VERSION_ESQUEMA_ACTUAL, gt=0)
    contenido: dict[str, Any]
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    registrado_en: datetime
    hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @field_validator("registrado_en")
    @classmethod
    def _requerir_zona_horaria(cls, valor: datetime) -> datetime:
        if valor.tzinfo is None:
            raise ValueError("registrado_en debe incluir zona horaria.")
        return valor

    def hash_esperado(self) -> str:
        """Recalcula el hash a partir de los datos del evento (lo usa central al recibir)."""
        return calcular_hash_evento(
            sucursal_id=self.sucursal_id,
            caja_id=self.caja_id,
            consecutivo=self.consecutivo,
            tipo=self.tipo,
            version_esquema=self.version_esquema,
            contenido=self.contenido,
            usuario_id=self.usuario_id,
            registrado_en=formatear_registrado_en(self.registrado_en),
        )


def serializar_contenido_evento(modelo: BaseModel) -> dict[str, Any]:
    """Convierte un modelo de contenido Pydantic a diccionario listo para persistir."""
    return modelo.model_dump(mode="json")


def validar_contenido_por_tipo(tipo: TipoEvento | str, contenido: dict[str, Any]) -> BaseModel:
    """Valida un diccionario contra el esquema Pydantic correspondiente a su tipo."""
    tipo_normalizado = TipoEvento(tipo)
    modelo = MODELOS_CONTENIDO[tipo_normalizado]
    return modelo.model_validate(contenido)


def formatear_registrado_en(momento: datetime) -> str:
    """Formato canónico de la fecha de un evento: UTC con milisegundos.

    Es el mismo texto que guarda la tienda, por eso entra en el hash.
    """
    if momento.tzinfo is None:
        raise ValueError("La fecha del evento debe incluir zona horaria.")
    return momento.astimezone(UTC).isoformat(timespec="milliseconds")


def calcular_hash_evento(
    *,
    sucursal_id: str,
    caja_id: str,
    consecutivo: int,
    tipo: TipoEvento | str,
    version_esquema: int,
    contenido: Mapping[str, Any],
    usuario_id: str,
    registrado_en: str,
) -> str:
    """Hash SHA-256 de un evento sobre JSON canónico (decisión 0005).

    Vive en el contrato para que la tienda y central lo calculen igual. No
    cambiar el algoritmo: los eventos ya guardados dejarían de coincidir.
    """
    documento = json.dumps(
        {
            "caja_id": caja_id,
            "consecutivo": consecutivo,
            "contenido": contenido,
            "registrado_en": registrado_en,
            "sucursal_id": sucursal_id,
            "tipo": str(TipoEvento(tipo)),
            "usuario_id": usuario_id,
            "version_esquema": version_esquema,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(documento.encode("utf-8")).hexdigest()
