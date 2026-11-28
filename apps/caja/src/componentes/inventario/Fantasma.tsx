import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ErrorApi, api, explicarError } from "../../api";
import { conSigno, pesos } from "../../dinero";
import type { ConteoRegistrado, ReporteFantasma, Sospecha } from "../../tipos";
import type { AjustePropuesto } from "./Ajuste";

interface Props {
  token: string;
  alVencerSesion: (motivo: string) => void;
  alProponerAjuste: (ajuste: AjustePropuesto) => void;
}

const hora = new Intl.DateTimeFormat("es-MX", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const dia = new Intl.DateTimeFormat("es-MX", { weekday: "long", day: "numeric", month: "long" });

function franja(s: Sospecha): string {
  const inicio = new Date(s.franja_inicio);
  return `entre ${hora.format(inicio)} y ${hora.format(new Date(s.franja_fin))} del ${dia.format(inicio)}`;
}

function piezas(n: number): string {
  const redondeado = Math.round(n * 10) / 10;
  return `${redondeado.toLocaleString("es-MX")} ${redondeado === 1 ? "pieza" : "piezas"}`;
}

/**
 * Productos que dejaron de venderse aunque el sistema dice que hay: probablemente
 * no están en el anaquel (decisión 0009). El reporte vive en central.
 */
export function Fantasma({ token, alVencerSesion, alProponerAjuste }: Props) {
  const [reporte, setReporte] = useState<ReporteFantasma | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [contados, setContados] = useState<Record<number, ConteoRegistrado>>({});

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      setReporte(await api.inventarioFantasma(token));
      setError(null);
    } catch (e) {
      setError(explicarError(e, alVencerSesion));
    } finally {
      setCargando(false);
    }
  }, [token, alVencerSesion]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const abiertas = reporte?.sospechas.filter((s) => !contados[s.id]) ?? [];

  return (
    <section className="formulario" aria-labelledby="titulo-fantasma">
      <div className="formulario__encabezado formulario__encabezado--con-accion">
        <div>
          <h2 id="titulo-fantasma" className="formulario__titulo">
            Inventario fantasma
          </h2>
          <p className="formulario__texto">
            Productos que dejaron de venderse aunque el sistema dice que hay. Suele ser que están en
            bodega y no en el anaquel. Central revisa cada 15 minutos.
          </p>
        </div>
        <button type="button" className="boton boton--secundario" onClick={cargar} disabled={cargando}>
          Actualizar
        </button>
      </div>

      {error && (
        <p className="mensaje mensaje--error" role="alert">
          {error}
        </p>
      )}

      {reporte && reporte.venta_perdida_estimada_centavos > 0 && abiertas.length > 0 && (
        <p className="perdida">
          Venta perdida estimada mientras no se resuelva:{" "}
          <strong>{pesos(reporte.venta_perdida_estimada_centavos)}</strong>
        </p>
      )}

      {Object.values(contados).map((c) => {
        const sospecha = reporte?.sospechas.find((s) => s.id === c.sospecha_id);
        return (
          <div key={c.sospecha_id} className="mensaje mensaje--exito resuelta" role="status">
            <p>
              Conteo registrado: {sospecha?.nombre ?? c.sku}, {c.cantidad_contada} piezas. El sistema
              tenía {c.existencia_local}.
            </p>
            {c.diferencia !== 0 ? (
              <button
                type="button"
                className="boton boton--principal"
                onClick={() =>
                  alProponerAjuste({
                    sku: c.sku,
                    contadas: c.cantidad_contada,
                    motivo: "Conteo físico por sospecha de inventario fantasma",
                  })
                }
              >
                Registrar ajuste de {conSigno(c.diferencia)}
              </button>
            ) : (
              <p>Coincide con el sistema: no hace falta ajustar.</p>
            )}
          </div>
        );
      })}

      {reporte && abiertas.length === 0 && !cargando && (
        <p className="formulario__texto">No hay sospechas abiertas en esta sucursal.</p>
      )}

      <ul className="sospechas">
        {abiertas.map((s) => (
          <li key={s.id}>
            <TarjetaSospecha
              sospecha={s}
              token={token}
              alContar={(conteo) => setContados((actuales) => ({ ...actuales, [s.id]: conteo }))}
              alVencerSesion={alVencerSesion}
              alDesaparecer={cargar}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

interface PropsTarjeta {
  sospecha: Sospecha;
  token: string;
  alContar: (conteo: ConteoRegistrado) => void;
  alVencerSesion: (motivo: string) => void;
  alDesaparecer: () => void;
}

function TarjetaSospecha({ sospecha: s, token, alContar, alVencerSesion, alDesaparecer }: PropsTarjeta) {
  const [contadas, setContadas] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const escalada = s.estado === "escalada";

  async function contar(evento: FormEvent) {
    evento.preventDefault();
    const n = Number.parseInt(contadas, 10);
    if (!Number.isInteger(n) || n < 0) {
      setError("Escribe cuántas piezas hay en anaquel y bodega (0 o más).");
      return;
    }
    setEnviando(true);
    setError(null);
    try {
      alContar(await api.registrarConteo(token, s.id, n));
    } catch (e) {
      if (e instanceof ErrorApi && e.estado === 404) alDesaparecer();
      setError(explicarError(e, alVencerSesion));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <article className={`sospecha ${escalada ? "sospecha--escalada" : ""}`} aria-label={s.nombre}>
      <div className="sospecha__cabeza">
        <h3 className="sospecha__nombre">{s.nombre}</h3>
        <span className="sospecha__estado">{escalada ? "Escalada al regional" : "Falta contar"}</span>
      </div>
      <p>
        Se esperaban unas {piezas(s.unidades_esperadas)} {franja(s)} y se vendieron{" "}
        {s.unidades_vendidas}.
      </p>
      <dl className="sospecha__datos">
        <div>
          <dt>En tienda según el sistema</dt>
          <dd>{s.existencia_local}</dd>
        </div>
        <div>
          <dt>Venta perdida estimada</dt>
          <dd>{pesos(s.venta_perdida_estimada_centavos)}</dd>
        </div>
      </dl>
      <form className="sospecha__conteo" onSubmit={contar} noValidate>
        <label className="campo">
          <span className="campo__nombre">Piezas contadas (anaquel y bodega)</span>
          <input
            className="campo__entrada"
            inputMode="numeric"
            value={contadas}
            onChange={(e) => setContadas(e.target.value)}
          />
        </label>
        <button type="submit" className="boton boton--principal" disabled={enviando}>
          {enviando ? "Registrando…" : "Registrar conteo"}
        </button>
      </form>
      {error && (
        <p className="mensaje mensaje--error" role="alert">
          {error}
        </p>
      )}
    </article>
  );
}
