import { type FormEvent, useState } from "react";
import { ErrorApi, SinServicioLocal, api } from "../api";
import type { RespuestaLogin } from "../tipos";

interface Props {
  caja?: string | null;
  aviso?: string | null;
  alEntrar: (sesion: RespuestaLogin) => void;
}

export function InicioSesion({ caja, aviso, alEntrar }: Props) {
  const [usuario, setUsuario] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function entrar(evento: FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setError(null);
    try {
      alEntrar(await api.iniciarSesion(usuario.trim(), pin));
    } catch (e) {
      setPin("");
      if (e instanceof ErrorApi && e.estado === 422) setError("Escribe tu usuario y tu PIN.");
      else if (e instanceof ErrorApi || e instanceof SinServicioLocal) setError(e.message);
      else setError("No se pudo iniciar sesión.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <main className="inicio">
      <form className="inicio__tarjeta" onSubmit={entrar} aria-labelledby="titulo-inicio">
        <h1 id="titulo-inicio" className="inicio__titulo">
          Abrir caja
        </h1>
        <p className="inicio__texto">Entra con tu usuario y tu PIN para empezar a cobrar.</p>
        {caja && <p className="inicio__caja">{caja}</p>}

        {aviso && !error && (
          <p className="mensaje mensaje--aviso" role="status">
            {aviso}
          </p>
        )}

        <label className="campo">
          <span className="campo__nombre">Usuario</span>
          <input
            className="campo__entrada"
            value={usuario}
            onChange={(e) => setUsuario(e.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            required
          />
        </label>

        <label className="campo">
          <span className="campo__nombre">PIN</span>
          <input
            className="campo__entrada campo__entrada--pin"
            type="password"
            inputMode="numeric"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            autoComplete="current-password"
            maxLength={32}
            required
          />
        </label>

        {error && (
          <p className="mensaje mensaje--error" role="alert">
            {error}
          </p>
        )}

        <button className="boton boton--principal" type="submit" disabled={enviando}>
          {enviando ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </main>
  );
}
