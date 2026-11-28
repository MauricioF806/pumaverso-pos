import { useState } from "react";
import { type AjustePropuesto, Ajuste } from "./inventario/Ajuste";
import { Fantasma } from "./inventario/Fantasma";
import { Merma } from "./inventario/Merma";
import { Recepcion } from "./inventario/Recepcion";
import { VentasDelDia } from "./inventario/VentasDelDia";

interface Props {
  token: string;
  usuarioId: string;
  alVencerSesion: (motivo: string) => void;
}

type Seccion = "ventas" | "recepcion" | "merma" | "ajuste" | "fantasma";

const SECCIONES: { id: Seccion; nombre: string; detalle: string }[] = [
  { id: "ventas", nombre: "Ventas del día", detalle: "Corte por cajero y producto" },
  { id: "recepcion", nombre: "Recibir mercancía", detalle: "Llegó el proveedor" },
  { id: "merma", nombre: "Merma", detalle: "Caducado o dañado" },
  { id: "ajuste", nombre: "Ajuste", detalle: "El conteo no cuadra" },
  { id: "fantasma", nombre: "Inventario fantasma", detalle: "Lo que dejó de venderse" },
];

/** Pestaña Gerencia: pantallas del gerente y el supervisor (decisiones 0011 y 0012). */
export function Inventario({ token, usuarioId, alVencerSesion }: Props) {
  const [seccion, setSeccion] = useState<Seccion>("ventas");
  const [propuesto, setPropuesto] = useState<AjustePropuesto | null>(null);

  function irA(destino: Seccion) {
    setSeccion(destino);
    if (destino !== "ajuste") setPropuesto(null);
  }

  return (
    <div className="inventario">
      <nav className="inventario__secciones" aria-label="Secciones de gerencia">
        {SECCIONES.map((s) => (
          <button
            key={s.id}
            type="button"
            className="seccion"
            aria-current={seccion === s.id ? "page" : undefined}
            onClick={() => irA(s.id)}
          >
            <span className="seccion__nombre">{s.nombre}</span>
            <span className="seccion__detalle">{s.detalle}</span>
          </button>
        ))}
      </nav>

      <div className="inventario__panel">
        {seccion === "ventas" && <VentasDelDia token={token} alVencerSesion={alVencerSesion} />}
        {seccion === "recepcion" && <Recepcion token={token} alVencerSesion={alVencerSesion} />}
        {seccion === "merma" && <Merma token={token} alVencerSesion={alVencerSesion} />}
        {seccion === "ajuste" && (
          <Ajuste
            token={token}
            usuarioId={usuarioId}
            alVencerSesion={alVencerSesion}
            propuesto={propuesto}
          />
        )}
        {seccion === "fantasma" && (
          <Fantasma
            token={token}
            alVencerSesion={alVencerSesion}
            alProponerAjuste={(ajuste) => {
              setPropuesto(ajuste);
              setSeccion("ajuste");
            }}
          />
        )}
      </div>
    </div>
  );
}
