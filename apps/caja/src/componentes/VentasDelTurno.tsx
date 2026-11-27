import { useCallback, useEffect, useState } from "react";
import { ErrorApi, SinServicioLocal, api } from "../api";
import { pesos } from "../dinero";
import type { Venta } from "../tipos";
import { DialogoCancelacion } from "./DialogoCancelacion";

interface Props {
  token: string;
  alVencerSesion: (motivo: string) => void;
}

const hora = new Intl.DateTimeFormat("es-MX", { hour: "2-digit", minute: "2-digit" });

function resumenProductos(venta: Venta): string {
  const [primera, ...resto] = venta.partidas;
  if (!primera) return "";
  const texto = `${primera.cantidad} × ${primera.nombre ?? primera.sku}`;
  return resto.length > 0 ? `${texto} y ${resto.length} más` : texto;
}

export function VentasDelTurno({ token, alVencerSesion }: Props) {
  const [ventas, setVentas] = useState<Venta[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [porCancelar, setPorCancelar] = useState<Venta | null>(null);

  const cargar = useCallback(async () => {
    try {
      setVentas(await api.ventasRecientes(token));
      setError(null);
    } catch (e) {
      if (e instanceof ErrorApi && (e.estado === 401 || e.estado === 403)) alVencerSesion(e.detalle);
      else if (e instanceof ErrorApi || e instanceof SinServicioLocal) setError(e.message);
      else setError("No se pudieron cargar las ventas.");
    }
  }, [token, alVencerSesion]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const cerrarDialogo = useCallback(() => setPorCancelar(null), []);

  async function alCancelar(consecutivo: number) {
    setPorCancelar(null);
    setAviso(`Venta ${consecutivo} cancelada.`);
    await cargar();
  }

  return (
    <section className="ventas" aria-labelledby="titulo-ventas">
      <div className="ventas__encabezado">
        <h2 id="titulo-ventas" className="ventas__titulo">
          Ventas recientes de esta caja
        </h2>
        <button type="button" className="boton boton--secundario" onClick={cargar}>
          Actualizar
        </button>
      </div>

      {aviso && (
        <p className="mensaje mensaje--exito" role="status">
          {aviso}
        </p>
      )}
      {error && (
        <p className="mensaje mensaje--error" role="alert">
          {error}
        </p>
      )}

      {ventas && ventas.length === 0 && (
        <p className="ventas__vacio">Todavía no hay ventas. Las que cobres aparecerán aquí.</p>
      )}

      {ventas && ventas.length > 0 && (
        <table className="tabla">
          <thead>
            <tr>
              <th scope="col">Hora</th>
              <th scope="col">Venta</th>
              <th scope="col">Productos</th>
              <th scope="col" className="tabla__numero">
                Total
              </th>
              <th scope="col">
                <span className="solo-lectores">Acciones</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {ventas.map((venta) => (
              <tr key={venta.consecutivo} className={venta.cancelada ? "tabla__fila--cancelada" : ""}>
                <td>{hora.format(new Date(venta.registrado_en))}</td>
                <td>{venta.consecutivo}</td>
                <td>{resumenProductos(venta)}</td>
                <td className="tabla__numero">{pesos(venta.total_centavos)}</td>
                <td className="tabla__accion">
                  {venta.cancelada ? (
                    <span className="etiqueta">Cancelada</span>
                  ) : (
                    <button
                      type="button"
                      className="boton boton--discreto"
                      onClick={() => {
                        setAviso(null);
                        setPorCancelar(venta);
                      }}
                    >
                      Cancelar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {porCancelar && (
        <DialogoCancelacion
          token={token}
          venta={porCancelar}
          alCerrar={cerrarDialogo}
          alCancelar={alCancelar}
          alVencerSesion={alVencerSesion}
        />
      )}
    </section>
  );
}
