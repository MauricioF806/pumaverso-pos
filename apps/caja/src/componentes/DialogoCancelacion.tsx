import { type FormEvent, useEffect, useRef, useState } from "react";
import { ErrorApi, SinServicioLocal, api } from "../api";
import { pesos } from "../dinero";
import type { Venta } from "../tipos";

interface Props {
  token: string;
  venta: Venta;
  alCerrar: () => void;
  alCancelar: (consecutivo: number) => void;
  alVencerSesion: (motivo: string) => void;
}

/** Cancelar requiere a un gerente o supervisor con su propio PIN (decisión 0006). */
export function DialogoCancelacion({ token, venta, alCerrar, alCancelar, alVencerSesion }: Props) {
  const [motivo, setMotivo] = useState("Devolución del cliente");
  const [aprobador, setAprobador] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const primerCampo = useRef<HTMLInputElement>(null);

  useEffect(() => {
    primerCampo.current?.focus();
    function alPresionar(evento: KeyboardEvent) {
      if (evento.key === "Escape") alCerrar();
    }
    window.addEventListener("keydown", alPresionar);
    return () => window.removeEventListener("keydown", alPresionar);
  }, [alCerrar]);

  async function confirmar(evento: FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setError(null);
    try {
      await api.cancelarVenta(token, venta.consecutivo, {
        motivo: motivo.trim(),
        aprobador_id: aprobador.trim(),
        aprobador_pin: pin,
      });
      alCancelar(venta.consecutivo);
    } catch (e) {
      setPin("");
      if (e instanceof ErrorApi && e.estado === 401) alVencerSesion(e.detalle);
      else if (e instanceof ErrorApi && e.estado === 422) setError("Completa el motivo, el usuario y el PIN.");
      else if (e instanceof ErrorApi || e instanceof SinServicioLocal) setError(e.message);
      else setError("No se pudo cancelar la venta.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="velo" onMouseDown={(e) => e.target === e.currentTarget && alCerrar()}>
      <form
        className="dialogo"
        role="dialog"
        aria-modal="true"
        aria-labelledby="titulo-cancelar"
        onSubmit={confirmar}
      >
        <h2 id="titulo-cancelar" className="dialogo__titulo">
          Cancelar la venta {venta.consecutivo}
        </h2>
        <p className="dialogo__texto">
          Se devolverán {pesos(venta.total_centavos)} y las piezas regresan al inventario. Un gerente
          o supervisor debe autorizarlo con su PIN.
        </p>

        <label className="campo">
          <span className="campo__nombre">Motivo</span>
          <input
            ref={primerCampo}
            className="campo__entrada"
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
            maxLength={255}
            required
          />
        </label>
        <div className="dialogo__fila">
          <label className="campo">
            <span className="campo__nombre">Usuario del gerente</span>
            <input
              className="campo__entrada"
              value={aprobador}
              onChange={(e) => setAprobador(e.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              required
            />
          </label>
          <label className="campo">
            <span className="campo__nombre">PIN del gerente</span>
            <input
              className="campo__entrada campo__entrada--pin"
              type="password"
              inputMode="numeric"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              autoComplete="off"
              required
            />
          </label>
        </div>

        {error && (
          <p className="mensaje mensaje--error" role="alert">
            {error}
          </p>
        )}

        <div className="dialogo__acciones">
          <button type="button" className="boton boton--secundario" onClick={alCerrar}>
            Volver
          </button>
          <button type="submit" className="boton boton--peligro" disabled={enviando}>
            {enviando ? "Cancelando…" : "Cancelar venta"}
          </button>
        </div>
      </form>
    </div>
  );
}
