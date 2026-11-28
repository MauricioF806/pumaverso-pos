import { useState } from "react";
import { type AjustePropuesto, Ajuste } from "./inventario/Ajuste";
import { Fantasma } from "./inventario/Fantasma";
import { Merma } from "./inventario/Merma";
import { Recepcion } from "./inventario/Recepcion";

interface Props {
  token: string;
  usuarioId: string;
  alVencerSesion: (motivo: string) => void;
}

type Seccion = "recepcion" | "merma" | "ajuste" | "fantasma";

const SECCIONES: { id: Seccion; nombre: string; detalle: string }[] = [
  { id: "recepcion", nombre: "Recibir mercancía", detalle: "Llegó el proveedor" },
  { id: "merma", nombre: "Merma", detalle: "Caducado o dañado" },
  { id: "ajuste", nombre: "Ajuste", detalle: "El conteo no cuadra" },
  { id: "fantasma", nombre: "Inventario fantasma", detalle: "Lo que dejó de venderse" },
];

/** Pantallas del gerente y el supervisor (decisión 0011). */
export function Inventario({ token, usuarioId, alVencerSesion }: Props) {
  const [seccion, setSeccion] = useState<Seccion>("recepcion");
  const [propuesto, setPropuesto] = useState<AjustePropuesto | null>(null);

  function irA(destino: Seccion) {
    setSeccion(destino);
    if (destino !== "ajuste") setPropuesto(null);
  }

  return (
    <div className="inventario">
      <nav className="inventario__secciones" aria-label="Operaciones de inventario">
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
