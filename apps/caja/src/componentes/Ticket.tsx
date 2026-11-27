import { pesos } from "../dinero";

export interface LineaTicket {
  sku: string;
  nombre: string;
  precio_centavos: number;
  cantidad: number;
}

interface Props {
  lineas: LineaTicket[];
  ultimaAgregada: string | null;
  alCambiarCantidad: (sku: string, cantidad: number) => void;
  alQuitar: (sku: string) => void;
}

export function totalDe(lineas: LineaTicket[]): number {
  return lineas.reduce((suma, l) => suma + l.precio_centavos * l.cantidad, 0);
}

/** El carrito se muestra como el ticket térmico que se va imprimiendo. */
export function Ticket({ lineas, ultimaAgregada, alCambiarCantidad, alQuitar }: Props) {
  const piezas = lineas.reduce((suma, l) => suma + l.cantidad, 0);

  return (
    <section className="ticket" aria-label="Ticket de la venta en curso">
      {lineas.length === 0 ? (
        <p className="ticket__vacio">
          Escanea un producto o búscalo por nombre para empezar la venta.
        </p>
      ) : (
        <ol className="ticket__lineas">
          {lineas.map((linea) => (
            <li
              key={linea.sku}
              className={`ticket__linea${linea.sku === ultimaAgregada ? " ticket__linea--nueva" : ""}`}
            >
              <span className="ticket__nombre">{linea.nombre}</span>
              <span className="ticket__importe">{pesos(linea.precio_centavos * linea.cantidad)}</span>
              <span className="ticket__cantidad">
                <button
                  type="button"
                  className="ticket__ajuste"
                  onClick={() => alCambiarCantidad(linea.sku, linea.cantidad - 1)}
                  aria-label={`Quitar una pieza de ${linea.nombre}`}
                >
                  −
                </button>
                <span aria-label={`${linea.cantidad} piezas`}>
                  {linea.cantidad} × {pesos(linea.precio_centavos)}
                </span>
                <button
                  type="button"
                  className="ticket__ajuste"
                  onClick={() => alCambiarCantidad(linea.sku, linea.cantidad + 1)}
                  aria-label={`Agregar una pieza de ${linea.nombre}`}
                >
                  +
                </button>
              </span>
              <button
                type="button"
                className="ticket__quitar"
                onClick={() => alQuitar(linea.sku)}
                aria-label={`Quitar ${linea.nombre} del ticket`}
              >
                Quitar
              </button>
            </li>
          ))}
        </ol>
      )}

      <div className="ticket__total">
        <span>
          Total{" "}
          <span className="ticket__piezas">
            ({piezas} {piezas === 1 ? "pieza" : "piezas"})
          </span>
        </span>
        <strong data-testid="total">{pesos(totalDe(lineas))}</strong>
      </div>
    </section>
  );
}
