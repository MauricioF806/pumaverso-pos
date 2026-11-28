import { useCallback, useEffect, useState } from "react";
import { api, explicarError } from "../../api";
import { pesos } from "../../dinero";
import type { ReporteVentas } from "../../tipos";

interface Props {
  token: string;
  alVencerSesion: (motivo: string) => void;
}

/** "2026-11-27" en la hora local de la caja. */
function hoyLocal(): string {
  const ahora = new Date();
  const mes = String(ahora.getMonth() + 1).padStart(2, "0");
  const dia = String(ahora.getDate()).padStart(2, "0");
  return `${ahora.getFullYear()}-${mes}-${dia}`;
}

/** De la medianoche local de ese día a la siguiente. */
function rangoDelDia(fecha: string): [Date, Date] {
  const [anio, mes, dia] = fecha.split("-").map(Number);
  return [new Date(anio, mes - 1, dia), new Date(anio, mes - 1, dia + 1)];
}

const fechaLarga = new Intl.DateTimeFormat("es-MX", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

/** Corte del día: lo que vendió la caja, por cajero y por producto (decisión 0012). */
export function VentasDelDia({ token, alVencerSesion }: Props) {
  const [fecha, setFecha] = useState(hoyLocal);
  const [reporte, setReporte] = useState<ReporteVentas | null>(null);
  const [error, setError] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    if (!fecha) return;
    const [desde, hasta] = rangoDelDia(fecha);
    try {
      setReporte(await api.reporteVentas(token, desde, hasta));
      setError(null);
    } catch (e) {
      setError(explicarError(e, alVencerSesion));
    }
  }, [fecha, token, alVencerSesion]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const [inicioDia] = fecha ? rangoDelDia(fecha) : [new Date()];

  return (
    <section className="formulario corte" aria-labelledby="titulo-ventas-dia">
      <div className="formulario__encabezado formulario__encabezado--con-accion">
        <div>
          <h2 id="titulo-ventas-dia" className="formulario__titulo">
            Ventas del día
          </h2>
          <p className="formulario__texto">
            Sale de lo que registró esta caja, así que funciona sin conexión. Las ventas canceladas
            no suman.
          </p>
        </div>
        <label className="campo">
          <span className="campo__nombre">Día</span>
          <input
            className="campo__entrada"
            type="date"
            value={fecha}
            max={hoyLocal()}
            onChange={(e) => setFecha(e.target.value)}
          />
        </label>
      </div>

      {error && (
        <p className="mensaje mensaje--error" role="alert">
          {error}
        </p>
      )}

      {reporte && (
        <div className="corte__cuerpo">
          <div className="ticket corte__ticket" aria-label="Corte del día">
            <p className="corte__fecha">Corte del {fechaLarga.format(inicioDia)}</p>
            <dl className="corte__lineas">
              <div>
                <dt>Ventas</dt>
                <dd>{reporte.ventas}</dd>
              </div>
              <div>
                <dt>Piezas</dt>
                <dd>{reporte.piezas}</dd>
              </div>
              <div>
                <dt>Ticket promedio</dt>
                <dd>{pesos(reporte.ticket_promedio_centavos)}</dd>
              </div>
              <div>
                <dt>Canceladas</dt>
                <dd>
                  {reporte.canceladas}
                  {reporte.canceladas > 0 && ` (${pesos(reporte.canceladas_centavos)})`}
                </dd>
              </div>
              <div>
                <dt>Sin existencias registradas</dt>
                <dd>{reporte.con_inventario_negativo}</dd>
              </div>
            </dl>
            <p className="ticket__total">
              Vendido <strong>{pesos(reporte.total_centavos)}</strong>
            </p>
          </div>

          <div className="corte__detalle">
            {reporte.ventas === 0 ? (
              <p className="formulario__texto">Sin ventas este día.</p>
            ) : (
              <>
                <table className="tabla">
                  <caption className="tabla__titulo">Por cajero</caption>
                  <thead>
                    <tr>
                      <th scope="col">Cajero</th>
                      <th scope="col" className="tabla__numero">
                        Ventas
                      </th>
                      <th scope="col" className="tabla__numero">
                        Total
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {reporte.por_cajero.map((c) => (
                      <tr key={c.usuario_id}>
                        <td>{c.nombre}</td>
                        <td className="tabla__numero">{c.ventas}</td>
                        <td className="tabla__numero">{pesos(c.total_centavos)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <table className="tabla">
                  <caption className="tabla__titulo">Lo más vendido</caption>
                  <thead>
                    <tr>
                      <th scope="col">Producto</th>
                      <th scope="col" className="tabla__numero">
                        Piezas
                      </th>
                      <th scope="col" className="tabla__numero">
                        Importe
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {reporte.productos_mas_vendidos.map((p) => (
                      <tr key={p.sku}>
                        <td>{p.nombre}</td>
                        <td className="tabla__numero">{p.piezas}</td>
                        <td className="tabla__numero">{pesos(p.total_centavos)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
