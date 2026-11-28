"""Routers HTTP expuestos por el monolito de la sucursal."""

from fastapi import APIRouter

from pumaverso_tienda.api.routes.fantasma import router as fantasma_router
from pumaverso_tienda.api.routes.operaciones import router as operaciones_router
from pumaverso_tienda.api.routes.reportes import router as reportes_router
from pumaverso_tienda.api.routes.sincronizacion import router as sincronizacion_router

api_router = APIRouter()
api_router.include_router(operaciones_router)
api_router.include_router(sincronizacion_router)
api_router.include_router(fantasma_router)
api_router.include_router(reportes_router)

__all__ = ["api_router"]