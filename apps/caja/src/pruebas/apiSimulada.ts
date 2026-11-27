import { vi } from "vitest";
import type { EstadoSincronizacion } from "../tipos";

type Respuesta = { estado?: number; cuerpo?: unknown };
type Manejador = Respuesta | ((cuerpo: unknown) => Respuesta);

export interface Llamada {
  metodo: string;
  ruta: string;
  cuerpo: unknown;
  token: string | null;
}

export const ESTADO_EN_LINEA: EstadoSincronizacion = {
  sucursal_id: "S001",
  caja_id: "C01",
  ultimo_consecutivo: 0,
  ultimo_consecutivo_confirmado: 0,
  pendientes: 0,
  sincronizacion_automatica: true,
  trabajador: {
    ultimo_intento_en: "2026-11-27T18:00:00Z",
    ultimo_exito_en: "2026-11-27T18:00:00Z",
    fallos_consecutivos: 0,
    en_conflicto: false,
    ultimo_error: null,
    proximo_intento_en: null,
  },
};

/**
 * Sustituye `fetch` por una API de la tienda simulada.
 * Las rutas se escriben como "POST /pos/ventas" o "GET /catalogo/123".
 */
export function simularApi(rutas: Record<string, Manejador>) {
  const llamadas: Llamada[] = [];
  const fetchFalso = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname, search } = new URL(url);
    const metodo = (init.method ?? "GET").toUpperCase();
    const cuerpo = init.body ? JSON.parse(String(init.body)) : undefined;
    const encabezados = new Headers(init.headers);
    const token = encabezados.get("Authorization")?.replace("Bearer ", "") ?? null;
    llamadas.push({ metodo, ruta: pathname + search, cuerpo, token });

    const clave = `${metodo} ${pathname}`;
    const manejador =
      rutas[`${metodo} ${pathname}${search}`] ??
      rutas[clave] ??
      (clave === "GET /sincronizacion/estado" ? { cuerpo: ESTADO_EN_LINEA } : undefined);
    if (!manejador) return new Response(JSON.stringify({ detail: `Sin simular: ${clave}` }), { status: 500 });

    const { estado = 200, cuerpo: respuesta } =
      typeof manejador === "function" ? manejador(cuerpo) : manejador;
    return new Response(estado === 204 ? null : JSON.stringify(respuesta ?? {}), { status: estado });
  });
  vi.stubGlobal("fetch", fetchFalso);
  return llamadas;
}

export const ANA = { usuario_id: "cajero1", nombre: "Ana López", rol: "cajero" };

export const REFRESCO = {
  sku: "7501055300846",
  nombre: "Refresco de cola 600 ml",
  precio_centavos: 2100,
  existencia: 24,
  costo_requiere_revision: false,
};

export const LECHE = {
  sku: "7501020515343",
  nombre: "Leche entera 1 L",
  precio_centavos: 3000,
  existencia: 12,
  costo_requiere_revision: false,
};
