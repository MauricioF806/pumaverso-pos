import { useCallback, useEffect, useState } from "react";
import { ErrorApi, SinServicioLocal, api } from "./api";
import { Cobro } from "./componentes/Cobro";
import { EstadoConexion } from "./componentes/EstadoConexion";
import { InicioSesion } from "./componentes/InicioSesion";
import { Inventario } from "./componentes/Inventario";
import { VentasDelTurno } from "./componentes/VentasDelTurno";
import { borrarToken, guardarToken, leerToken } from "./sesion";
import type { Usuario } from "./tipos";

type Vista = "cobro" | "ventas" | "inventario";

// Quién ve la pestaña Gerencia; la tienda vuelve a validar cada operación.
const ROLES_INVENTARIO = new Set(["gerente", "supervisor"]);
type Identidad = { sucursal_id: string; caja_id: string } | null;
type Estado =
  | { fase: "reanudando" }
  | { fase: "cerrada"; aviso: string | null }
  | { fase: "abierta"; token: string; usuario: Usuario };

export function App() {
  const [estado, setEstado] = useState<Estado>({ fase: "reanudando" });
  const [vista, setVista] = useState<Vista>("cobro");
  const [identidad, setIdentidad] = useState<Identidad>(null);

  // Qué caja y sucursal es este equipo; se muestra en la barra y al iniciar sesión.
  useEffect(() => {
    api
      .estadoSincronizacion()
      .then(({ sucursal_id, caja_id }) => setIdentidad({ sucursal_id, caja_id }))
      .catch(() => undefined);
  }, []);

  const nombreCaja = identidad
    ? `Caja ${identidad.caja_id} de la sucursal ${identidad.sucursal_id}`
    : null;

  // Al recargar la pantalla, se intenta reanudar la sesión guardada.
  useEffect(() => {
    const token = leerToken();
    if (!token) {
      setEstado({ fase: "cerrada", aviso: null });
      return;
    }
    api
      .quienSoy(token)
      .then((usuario) => setEstado({ fase: "abierta", token, usuario }))
      .catch((e) => {
        if (e instanceof SinServicioLocal) {
          setEstado({ fase: "cerrada", aviso: e.message });
          return;
        }
        borrarToken();
        setEstado({
          fase: "cerrada",
          aviso: e instanceof ErrorApi && e.estado === 403 ? e.detalle : null,
        });
      });
  }, []);

  const vencerSesion = useCallback((motivo: string) => {
    borrarToken();
    setEstado({ fase: "cerrada", aviso: `Se cerró la sesión: ${motivo}` });
  }, []);

  async function salir() {
    if (estado.fase !== "abierta") return;
    await api.cerrarSesion(estado.token).catch(() => undefined);
    borrarToken();
    setVista("cobro");
    setEstado({ fase: "cerrada", aviso: null });
  }

  if (estado.fase === "reanudando") {
    return <p className="cargando">Abriendo caja…</p>;
  }

  if (estado.fase === "cerrada") {
    return (
      <InicioSesion
        caja={nombreCaja}
        aviso={estado.aviso}
        alEntrar={(sesion) => {
          guardarToken(sesion.token);
          setEstado({ fase: "abierta", token: sesion.token, usuario: sesion });
        }}
      />
    );
  }

  return (
    <div className="caja">
      <header className="barra">
        <p className="barra__marca">
          Pumaverso
          {nombreCaja && <span className="barra__caja">{nombreCaja}</span>}
        </p>
        <nav className="barra__vistas" aria-label="Secciones de la caja">
          <button
            type="button"
            className="pestana"
            aria-current={vista === "cobro" ? "page" : undefined}
            onClick={() => setVista("cobro")}
          >
            Cobrar
          </button>
          <button
            type="button"
            className="pestana"
            aria-current={vista === "ventas" ? "page" : undefined}
            onClick={() => setVista("ventas")}
          >
            Ventas del turno
          </button>
          {ROLES_INVENTARIO.has(estado.usuario.rol) && (
            <button
              type="button"
              className="pestana"
              aria-current={vista === "inventario" ? "page" : undefined}
              onClick={() => setVista("inventario")}
            >
              Gerencia
            </button>
          )}
        </nav>
        <EstadoConexion />
        <div className="barra__usuario">
          <span>
            Atiende <strong>{estado.usuario.nombre}</strong>
          </span>
          <button type="button" className="boton boton--discreto" onClick={salir}>
            Cerrar sesión
          </button>
        </div>
      </header>

      <main className="contenido">
        {vista === "cobro" && <Cobro token={estado.token} alVencerSesion={vencerSesion} />}
        {vista === "ventas" && <VentasDelTurno token={estado.token} alVencerSesion={vencerSesion} />}
        {vista === "inventario" && (
          <Inventario
            token={estado.token}
            usuarioId={estado.usuario.usuario_id}
            alVencerSesion={vencerSesion}
          />
        )}
      </main>
    </div>
  );
}
