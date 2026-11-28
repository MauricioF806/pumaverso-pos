import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ErrorApi, SinServicioLocal, api } from "../../api";
import { conSigno } from "../../dinero";
import type { Producto } from "../../tipos";
import { DialogoAprobacion } from "../DialogoAprobacion";
import { BuscadorProducto } from "./BuscadorProducto";

export interface AjustePropuesto {
  sku: string;
  contadas: number;
  motivo: string;
}

interface Props {
  token: string;
  usuarioId: string;
  alVencerSesion: (motivo: string) => void;
  /** Viene de un conteo de inventario fantasma: producto y piezas ya capturados. */
  propuesto?: AjustePropuesto | null;
}

/** Se contó el anaquel y no coincide con el sistema: se corrige con un evento nuevo. */
export function Ajuste({ token, usuarioId, alVencerSesion, propuesto = null }: Props) {
  const [producto, setProducto] = useState<Producto | null>(null);
  const [contadas, setContadas] = useState("");
  const [motivo, setMotivo] = useState("Conteo físico");
  const [error, setError] = useState<string | null>(null);
  const [exito, setExito] = useState<string | null>(null);
  const [aprobando, setAprobando] = useState(false);

  useEffect(() => {
    if (!propuesto) return;
    api
      .consultarProducto(propuesto.sku)
      .then((p) => {
        setProducto(p);
        setContadas(String(propuesto.contadas));
        setMotivo(propuesto.motivo);
      })
      .catch(() => setError("No se encontró el producto del conteo en la tienda."));
  }, [propuesto]);

  const piezas = Number.parseInt(contadas, 10);
  const diferencia = producto && Number.isInteger(piezas) && piezas >= 0 ? piezas - producto.existencia : null;

  function pedirAprobacion(evento: FormEvent) {
    evento.preventDefault();
    setError(null);
    if (diferencia === null) {
      setError("Escribe cuántas piezas contaste (0 o más).");
      return;
    }
    if (diferencia === 0) {
      setError("Lo contado coincide con el sistema: no hace falta ajustar.");
      return;
    }
    if (!motivo.trim()) {
      setError("Escribe el motivo.");
      return;
    }
    setAprobando(true);
  }

  const cerrar = useCallback(() => setAprobando(false), []);

  async function confirmar(aprobador: string, pin: string) {
    if (!producto || diferencia === null) return;
    if (aprobador === usuarioId) {
      throw new Error("Debe autorizar otra persona: un gerente o supervisor distinto de ti.");
    }
    try {
      const evento = await api.registrarAjuste(token, {
        sku: producto.sku,
        diferencia_cantidad: diferencia,
        motivo: motivo.trim(),
        aprobador_id: aprobador,
        aprobador_pin: pin,
      });
      setAprobando(false);
      setExito(
        `Ajuste registrado (operación ${evento.consecutivo}): ${producto.nombre} ${conSigno(diferencia)}. ` +
          `Ahora hay ${piezas} en tienda.`,
      );
      setProducto(null);
      setContadas("");
      setMotivo("Conteo físico");
    } catch (e) {
      if (e instanceof ErrorApi && e.estado === 401) {
        alVencerSesion(e.detalle);
        return;
      }
      if (e instanceof ErrorApi && e.estado === 422) throw new Error("Completa el usuario y el PIN.");
      if (e instanceof ErrorApi || e instanceof SinServicioLocal) throw new Error(e.message);
      throw e;
    }
  }

  return (
    <section className="formulario" aria-labelledby="titulo-ajuste">
      <div className="formulario__encabezado">
        <h2 id="titulo-ajuste" className="formulario__titulo">
          Ajustar inventario
        </h2>
        <p className="formulario__texto">
          Escribe lo que contaste en anaquel y bodega. El historial no se borra: el ajuste queda como
          una operación nueva, autorizada por otro gerente o supervisor.
        </p>
      </div>

      {exito && (
        <p className="mensaje mensaje--exito" role="status">
          {exito}
        </p>
      )}

      {!producto ? (
        <BuscadorProducto etiqueta="Producto contado (Enter)" alElegir={setProducto} autoFocus={!propuesto} />
      ) : (
        <form className="formulario__cuerpo" onSubmit={pedirAprobacion} noValidate>
          <div className="elegido">
            <p className="elegido__nombre">{producto.nombre}</p>
            <p className="elegido__dato">El sistema dice: {producto.existencia} en tienda</p>
            <button type="button" className="boton boton--discreto" onClick={() => setProducto(null)}>
              Cambiar producto
            </button>
          </div>

          <div className="formulario__fila">
            <label className="campo">
              <span className="campo__nombre">Piezas contadas</span>
              <input
                className="campo__entrada"
                inputMode="numeric"
                value={contadas}
                onChange={(e) => setContadas(e.target.value)}
                autoFocus
              />
            </label>
            <label className="campo">
              <span className="campo__nombre">Motivo</span>
              <input
                className="campo__entrada"
                value={motivo}
                onChange={(e) => setMotivo(e.target.value)}
                maxLength={255}
              />
            </label>
          </div>

          {diferencia !== null && diferencia !== 0 && (
            <p className={`diferencia ${diferencia < 0 ? "diferencia--falta" : "diferencia--sobra"}`}>
              Diferencia contra el sistema: <strong>{conSigno(diferencia)}</strong>
              {diferencia < 0 ? " (faltan piezas)" : " (sobran piezas)"}
            </p>
          )}

          {error && (
            <p className="mensaje mensaje--error" role="alert">
              {error}
            </p>
          )}
          <div className="formulario__acciones">
            <button type="submit" className="boton boton--principal">
              Registrar ajuste
            </button>
          </div>
        </form>
      )}

      {aprobando && producto && diferencia !== null && (
        <DialogoAprobacion
          titulo={`Ajustar ${producto.nombre}`}
          textoConfirmar={`Ajustar ${conSigno(diferencia)}`}
          alConfirmar={confirmar}
          alCerrar={cerrar}
        >
          De {producto.existencia} a {piezas} piezas. Lo autoriza un gerente o supervisor distinto de
          quien registra.
        </DialogoAprobacion>
      )}
    </section>
  );
}
