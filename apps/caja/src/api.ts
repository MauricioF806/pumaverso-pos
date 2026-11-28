import type {
  ConteoRegistrado,
  EstadoSincronizacion,
  EventoCreado,
  PartidaRecepcion,
  ProductoConsultado,
  ProductoListado,
  ReporteFantasma,
  RespuestaLogin,
  Usuario,
  Venta,
  VentaCreada,
} from "./tipos";

const URL_BASE = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

/** La tienda respondió con un error. `detalle` es el mensaje que explica qué pasó. */
export class ErrorApi extends Error {
  constructor(
    readonly estado: number,
    readonly detalle: string,
  ) {
    super(detalle);
    this.name = "ErrorApi";
  }
}

/** No hubo respuesta: el servicio local de la tienda no está corriendo. */
export class SinServicioLocal extends Error {
  constructor() {
    super(
      "La caja no puede comunicarse con el servicio de la tienda. Revisa que esté encendido.",
    );
    this.name = "SinServicioLocal";
  }
}

async function pedir<T>(ruta: string, opciones: RequestInit & { token?: string } = {}): Promise<T> {
  const { token, headers, ...resto } = opciones;
  let respuesta: Response;
  try {
    respuesta = await fetch(`${URL_BASE}${ruta}`, {
      ...resto,
      headers: {
        ...(resto.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
    });
  } catch {
    throw new SinServicioLocal();
  }

  if (respuesta.status === 204) return undefined as T;
  const datos = await respuesta.json().catch(() => null);
  if (!respuesta.ok) {
    throw new ErrorApi(respuesta.status, leerDetalle(datos, respuesta.status));
  }
  return datos as T;
}

function leerDetalle(datos: unknown, estado: number): string {
  const detalle = (datos as { detail?: unknown } | null)?.detail;
  if (typeof detalle === "string") return detalle;
  if (Array.isArray(detalle)) return "Los datos enviados no son válidos.";
  return `La tienda respondió con un error (${estado}).`;
}

const json = (cuerpo: unknown) => JSON.stringify(cuerpo);

export const api = {
  iniciarSesion: (usuario_id: string, pin: string) =>
    pedir<RespuestaLogin>("/auth/login", { method: "POST", body: json({ usuario_id, pin }) }),

  cerrarSesion: (token: string) => pedir<void>("/auth/logout", { method: "POST", token }),

  quienSoy: (token: string) => pedir<Usuario>("/auth/yo", { token }),

  consultarProducto: (sku: string) =>
    pedir<ProductoConsultado>(`/catalogo/${encodeURIComponent(sku)}`),

  buscarProductos: (texto: string, limite = 12) =>
    pedir<ProductoListado[]>(
      `/catalogo?${new URLSearchParams({ buscar: texto, limite: String(limite) })}`,
    ),

  cobrar: (token: string, items: { sku: string; cantidad: number }[]) =>
    pedir<VentaCreada>("/pos/ventas", { method: "POST", token, body: json({ items }) }),

  ventasRecientes: (token: string, limite = 30) =>
    pedir<Venta[]>(`/pos/ventas?limite=${limite}`, { token }),

  cancelarVenta: (
    token: string,
    consecutivo: number,
    datos: { motivo: string; aprobador_id: string; aprobador_pin: string },
  ) =>
    pedir<{ consecutivo: number }>(`/pos/ventas/${consecutivo}/cancelar`, {
      method: "POST",
      token,
      body: json(datos),
    }),

  estadoSincronizacion: () => pedir<EstadoSincronizacion>("/sincronizacion/estado"),

  // --- Inventario ---

  recibirMercancia: (
    token: string,
    datos: { proveedor_id: string; folio_documento: string; partidas: PartidaRecepcion[] },
  ) => pedir<EventoCreado>("/inventario/recepciones", { method: "POST", token, body: json(datos) }),

  registrarMerma: (token: string, datos: { sku: string; cantidad: number; motivo: string }) =>
    pedir<EventoCreado>("/inventario/mermas", { method: "POST", token, body: json(datos) }),

  registrarAjuste: (
    token: string,
    datos: {
      sku: string;
      diferencia_cantidad: number;
      motivo: string;
      aprobador_id: string;
      aprobador_pin: string;
    },
  ) => pedir<EventoCreado>("/inventario/ajustes", { method: "POST", token, body: json(datos) }),

  inventarioFantasma: (token: string) => pedir<ReporteFantasma>("/inventario/fantasma", { token }),

  registrarConteo: (token: string, sospechaId: number, cantidad_contada: number) =>
    pedir<ConteoRegistrado>(`/inventario/fantasma/${sospechaId}/conteo`, {
      method: "POST",
      token,
      body: json({ cantidad_contada }),
    }),
};

/** Mensaje para el usuario. Si la sesión ya no sirve (401), avisa y devuelve null. */
export function explicarError(e: unknown, alVencerSesion: (motivo: string) => void): string | null {
  if (e instanceof ErrorApi && e.estado === 401) {
    alVencerSesion(e.detalle);
    return null;
  }
  if (e instanceof ErrorApi && e.estado === 422) return "Revisa los datos: falta algo o no es válido.";
  if (e instanceof ErrorApi || e instanceof SinServicioLocal) return e.message;
  return "Ocurrió un error inesperado.";
}
