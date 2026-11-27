// Formas de las respuestas de la API de la tienda (apps/tienda/api/routes).

export interface Usuario {
  usuario_id: string;
  nombre: string;
  rol: string;
}

export interface RespuestaLogin extends Usuario {
  token: string;
  expira_en: string;
}

export interface Producto {
  sku: string;
  nombre: string;
  precio_centavos: number;
  existencia: number;
}

export interface ProductoListado extends Producto {
  precio_vigente: boolean;
}

export interface ProductoConsultado extends Producto {
  costo_requiere_revision: boolean;
}

export interface VentaCreada {
  sucursal_id: string;
  caja_id: string;
  consecutivo: number;
  total_centavos: number;
  hash: string;
  inventario_negativo: boolean;
  alerta_inventario_negativo: boolean;
  skus_costo_por_revisar: string[];
}

export interface PartidaVenta {
  sku: string;
  nombre: string | null;
  cantidad: number;
  precio_unitario_centavos: number;
  subtotal_centavos: number;
}

export interface Venta {
  consecutivo: number;
  registrado_en: string;
  usuario_id: string;
  total_centavos: number;
  inventario_negativo: boolean;
  cancelada: boolean;
  consecutivo_cancelacion: number | null;
  hash: string;
  partidas: PartidaVenta[];
}

export interface EstadoTrabajador {
  ultimo_intento_en: string | null;
  ultimo_exito_en: string | null;
  fallos_consecutivos: number;
  en_conflicto: boolean;
  ultimo_error: string | null;
  proximo_intento_en: string | null;
}

export interface EstadoSincronizacion {
  sucursal_id: string;
  caja_id: string;
  ultimo_consecutivo: number;
  ultimo_consecutivo_confirmado: number;
  pendientes: number;
  sincronizacion_automatica: boolean;
  trabajador: EstadoTrabajador | null;
}
