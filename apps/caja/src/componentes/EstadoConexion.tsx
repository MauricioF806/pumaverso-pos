import { useEffect, useState } from "react";
import { SinServicioLocal, api } from "../api";
import type { EstadoSincronizacion } from "../tipos";

const CADA_MS = 5000;

type Tono = "bien" | "espera" | "problema";

interface Resumen {
  tono: Tono;
  titulo: string;
  detalle?: string;
}

/** Traduce el estado de la sincronización a lo que necesita saber el cajero. */
export function resumirEstado(estado: EstadoSincronizacion | null, sinServicio: boolean): Resumen {
  if (sinServicio) {
    return { tono: "problema", titulo: "Sin servicio local", detalle: "No se puede cobrar" };
  }
  if (!estado) return { tono: "espera", titulo: "Revisando conexión" };

  const porEnviar =
    estado.pendientes === 1 ? "1 operación por enviar" : `${estado.pendientes} operaciones por enviar`;
  const trabajador = estado.trabajador;

  if (trabajador?.en_conflicto) {
    return { tono: "problema", titulo: "Central rechazó las ventas", detalle: "Avisa al gerente" };
  }
  if (trabajador && trabajador.fallos_consecutivos > 0) {
    return {
      tono: "espera",
      titulo: "Sin conexión con central",
      detalle: estado.pendientes > 0 ? porEnviar : undefined,
    };
  }
  if (trabajador && !trabajador.ultimo_exito_en) {
    return { tono: "espera", titulo: "Conectando con central" };
  }
  if (!trabajador) {
    return {
      tono: "bien",
      titulo: "Sincronización manual",
      detalle: estado.pendientes > 0 ? porEnviar : undefined,
    };
  }
  // Con conexión, lo pendiente se envía solo en segundos: no hace falta mostrarlo.
  return { tono: "bien", titulo: "En línea" };
}

export function EstadoConexion() {
  const [estado, setEstado] = useState<EstadoSincronizacion | null>(null);
  const [sinServicio, setSinServicio] = useState(false);

  useEffect(() => {
    let vigente = true;
    async function consultar() {
      try {
        const nuevo = await api.estadoSincronizacion();
        if (vigente) {
          setEstado(nuevo);
          setSinServicio(false);
        }
      } catch (e) {
        if (vigente && e instanceof SinServicioLocal) setSinServicio(true);
      }
    }
    consultar();
    const intervalo = window.setInterval(consultar, CADA_MS);
    return () => {
      vigente = false;
      window.clearInterval(intervalo);
    };
  }, []);

  const resumen = resumirEstado(estado, sinServicio);
  return (
    <div className={`conexion conexion--${resumen.tono}`} role="status" aria-live="polite">
      <span className="conexion__punto" aria-hidden="true" />
      <span className="conexion__titulo">{resumen.titulo}</span>
      {resumen.detalle && <span className="conexion__detalle">{resumen.detalle}</span>}
    </div>
  );
}
