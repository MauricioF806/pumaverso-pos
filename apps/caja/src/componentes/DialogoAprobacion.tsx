import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";

interface Props {
  titulo: string;
  children: ReactNode;
  textoConfirmar: string;
  /** Lanza un Error con el mensaje a mostrar si la aprobación no procede. */
  alConfirmar: (aprobador: string, pin: string) => Promise<void>;
  alCerrar: () => void;
}

/**
 * Pide usuario y PIN de un gerente o supervisor distinto de quien opera
 * (decisión 0006). Los errores se muestran dentro del diálogo.
 */
export function DialogoAprobacion({ titulo, children, textoConfirmar, alConfirmar, alCerrar }: Props) {
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
      await alConfirmar(aprobador.trim(), pin);
    } catch (e) {
      setPin("");
      setError(e instanceof Error ? e.message : "No se pudo aprobar.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="velo" onMouseDown={(e) => e.target === e.currentTarget && alCerrar()}>
      <form
        className="dialogo dialogo--neutral"
        role="dialog"
        aria-modal="true"
        aria-labelledby="titulo-aprobacion"
        onSubmit={confirmar}
      >
        <h2 id="titulo-aprobacion" className="dialogo__titulo">
          {titulo}
        </h2>
        <div className="dialogo__texto">{children}</div>
        <div className="dialogo__fila">
          <label className="campo">
            <span className="campo__nombre">Usuario de quien autoriza</span>
            <input
              ref={primerCampo}
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
            <span className="campo__nombre">Su PIN</span>
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
          <button type="submit" className="boton boton--principal" disabled={enviando}>
            {enviando ? "Registrando…" : textoConfirmar}
          </button>
        </div>
      </form>
    </div>
  );
}
