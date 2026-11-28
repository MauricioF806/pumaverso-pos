import { type FormEvent, useState } from "react";
import { api, explicarError } from "../../api";
import type { Producto } from "../../tipos";
import { BuscadorProducto } from "./BuscadorProducto";

interface Props {
  token: string;
  alVencerSesion: (motivo: string) => void;
}

const MOTIVOS = ["Caducado", "Dañado o roto", "Otro"] as const;

/** Producto que ya no se puede vender: sale del inventario con su motivo. */
export function Merma({ token, alVencerSesion }: Props) {
  const [producto, setProducto] = useState<Producto | null>(null);
  const [cantidad, setCantidad] = useState("");
  const [motivo, setMotivo] = useState<(typeof MOTIVOS)[number]>("Caducado");
  const [otroMotivo, setOtroMotivo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [exito, setExito] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  function elegir(p: Producto) {
    setProducto(p);
    setExito(null);
    setError(null);
  }

  async function registrar(evento: FormEvent) {
    evento.preventDefault();
    if (!producto) return;
    const piezas = Number.parseInt(cantidad, 10);
    const texto = motivo === "Otro" ? otroMotivo.trim() : motivo;
    if (!Number.isInteger(piezas) || piezas <= 0) {
      setError("Escribe cuántas piezas salen (más de 0).");
      return;
    }
    if (!texto) {
      setError("Escribe el motivo.");
      return;
    }
    setEnviando(true);
    setError(null);
    try {
      const evento = await api.registrarMerma(token, { sku: producto.sku, cantidad: piezas, motivo: texto });
      const actual = await api.consultarProducto(producto.sku).catch(() => null);
      setExito(
        `Merma registrada (operación ${evento.consecutivo}): ${piezas} × ${producto.nombre}.` +
          (actual ? ` Quedan ${actual.existencia} en tienda.` : ""),
      );
      setProducto(null);
      setCantidad("");
      setOtroMotivo("");
    } catch (e) {
      setError(explicarError(e, alVencerSesion));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section className="formulario" aria-labelledby="titulo-merma">
      <div className="formulario__encabezado">
        <h2 id="titulo-merma" className="formulario__titulo">
          Registrar merma
        </h2>
        <p className="formulario__texto">
          Producto caducado o dañado que se retira del anaquel. Sale del inventario y queda registrado
          quién lo retiró y por qué.
        </p>
      </div>

      {exito && (
        <p className="mensaje mensaje--exito" role="status">
          {exito}
        </p>
      )}

      {!producto ? (
        <BuscadorProducto etiqueta="Producto (Enter)" alElegir={elegir} autoFocus />
      ) : (
        <form className="formulario__cuerpo" onSubmit={registrar} noValidate>
          <div className="elegido">
            <p className="elegido__nombre">{producto.nombre}</p>
            <p className="elegido__dato">{producto.existencia} en tienda según el sistema</p>
            <button type="button" className="boton boton--discreto" onClick={() => setProducto(null)}>
              Cambiar producto
            </button>
          </div>

          <div className="formulario__fila">
            <label className="campo">
              <span className="campo__nombre">Piezas que salen</span>
              <input
                className="campo__entrada"
                inputMode="numeric"
                value={cantidad}
                onChange={(e) => setCantidad(e.target.value)}
                autoFocus
              />
            </label>
            <fieldset className="opciones">
              <legend className="campo__nombre">Motivo</legend>
              {MOTIVOS.map((m) => (
                <label key={m} className="opcion">
                  <input
                    type="radio"
                    name="motivo-merma"
                    checked={motivo === m}
                    onChange={() => setMotivo(m)}
                  />
                  {m}
                </label>
              ))}
            </fieldset>
          </div>
          {motivo === "Otro" && (
            <label className="campo">
              <span className="campo__nombre">¿Cuál?</span>
              <input
                className="campo__entrada"
                value={otroMotivo}
                onChange={(e) => setOtroMotivo(e.target.value)}
                maxLength={255}
              />
            </label>
          )}

          {error && (
            <p className="mensaje mensaje--error" role="alert">
              {error}
            </p>
          )}
          <div className="formulario__acciones">
            <button type="submit" className="boton boton--principal" disabled={enviando}>
              {enviando ? "Registrando…" : "Registrar merma"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
