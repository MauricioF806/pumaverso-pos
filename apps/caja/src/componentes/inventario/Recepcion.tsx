import { useState } from "react";
import { api, explicarError } from "../../api";
import { aCentavos, pesos } from "../../dinero";
import type { Producto } from "../../tipos";
import { BuscadorProducto } from "./BuscadorProducto";

interface Props {
  token: string;
  alVencerSesion: (motivo: string) => void;
}

interface Linea {
  sku: string;
  nombre: string;
  cantidad: string;
  costo: string;
}

const PATRON_PROVEEDOR = /^[A-Za-z0-9_-]{1,32}$/;

/** Llega el proveedor: se capturan las piezas y el costo de su factura. */
export function Recepcion({ token, alVencerSesion }: Props) {
  const [proveedor, setProveedor] = useState("");
  const [folio, setFolio] = useState("");
  const [lineas, setLineas] = useState<Linea[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [exito, setExito] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  function agregar(producto: Producto) {
    setExito(null);
    setLineas((actuales) =>
      actuales.some((l) => l.sku === producto.sku)
        ? actuales
        : [...actuales, { sku: producto.sku, nombre: producto.nombre, cantidad: "", costo: "" }],
    );
  }

  function cambiar(sku: string, campo: "cantidad" | "costo", valor: string) {
    setLineas((actuales) => actuales.map((l) => (l.sku === sku ? { ...l, [campo]: valor } : l)));
  }

  const partidas = lineas.map((l) => ({
    sku: l.sku,
    cantidad: Number.parseInt(l.cantidad, 10),
    costo_unitario_centavos: aCentavos(l.costo),
  }));
  const completas = partidas.every(
    (p) => Number.isInteger(p.cantidad) && p.cantidad > 0 && p.costo_unitario_centavos !== null,
  );
  const total = completas
    ? partidas.reduce((suma, p) => suma + p.cantidad * (p.costo_unitario_centavos ?? 0), 0)
    : null;
  const piezas = completas ? partidas.reduce((suma, p) => suma + p.cantidad, 0) : 0;

  // No es un <form>: adentro va el buscador, que tiene su propio formulario.
  async function registrar() {
    setError(null);
    if (!PATRON_PROVEEDOR.test(proveedor.trim())) {
      setError("El proveedor va sin espacios: letras, números y guiones, por ejemplo PROV-BIMBO.");
      return;
    }
    if (!folio.trim()) {
      setError("Escribe el folio de la factura.");
      return;
    }
    if (lineas.length === 0) {
      setError("Agrega al menos un producto de la factura.");
      return;
    }
    if (!completas) {
      setError("Cada producto necesita piezas (más de 0) y su costo unitario.");
      return;
    }
    setEnviando(true);
    try {
      const evento = await api.recibirMercancia(token, {
        proveedor_id: proveedor.trim(),
        folio_documento: folio.trim(),
        partidas: partidas.map((p) => ({ ...p, costo_unitario_centavos: p.costo_unitario_centavos ?? 0 })),
      });
      setExito(
        `Recepción registrada (operación ${evento.consecutivo}): ${piezas} piezas de ${lineas.length} ${
          lineas.length === 1 ? "producto" : "productos"
        }. Ya están en el inventario de la tienda.`,
      );
      setLineas([]);
      setFolio("");
    } catch (e) {
      setError(explicarError(e, alVencerSesion));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section className="formulario" aria-labelledby="titulo-recepcion">
      <div className="formulario__encabezado">
        <h2 id="titulo-recepcion" className="formulario__titulo">
          Recibir mercancía
        </h2>
        <p className="formulario__texto">
          Captura lo que dice la factura del proveedor. Funciona sin conexión; central lo recibe al
          sincronizar.
        </p>
      </div>

      <div className="formulario__fila">
        <label className="campo">
          <span className="campo__nombre">Proveedor</span>
          <input
            className="campo__entrada"
            value={proveedor}
            onChange={(e) => setProveedor(e.target.value)}
            placeholder="PROV-BIMBO"
            autoComplete="off"
            spellCheck={false}
            required
          />
        </label>
        <label className="campo">
          <span className="campo__nombre">Folio de la factura</span>
          <input
            className="campo__entrada"
            value={folio}
            onChange={(e) => setFolio(e.target.value)}
            maxLength={64}
            autoComplete="off"
            required
          />
        </label>
      </div>

      <BuscadorProducto etiqueta="Agregar producto (Enter)" alElegir={agregar} />

      {lineas.length > 0 && (
        <table className="tabla tabla--captura">
          <thead>
            <tr>
              <th scope="col">Producto</th>
              <th scope="col">Piezas</th>
              <th scope="col">Costo unitario</th>
              <th scope="col">
                <span className="solo-lectores">Quitar</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {lineas.map((l) => (
              <tr key={l.sku}>
                <td>{l.nombre}</td>
                <td>
                  <input
                    className="campo__entrada campo__entrada--corta"
                    inputMode="numeric"
                    value={l.cantidad}
                    onChange={(e) => cambiar(l.sku, "cantidad", e.target.value)}
                    aria-label={`Piezas de ${l.nombre}`}
                  />
                </td>
                <td>
                  <input
                    className="campo__entrada campo__entrada--corta"
                    inputMode="decimal"
                    value={l.costo}
                    onChange={(e) => cambiar(l.sku, "costo", e.target.value)}
                    placeholder="0.00"
                    aria-label={`Costo unitario de ${l.nombre}`}
                  />
                </td>
                <td className="tabla__accion">
                  <button
                    type="button"
                    className="boton boton--discreto"
                    onClick={() => setLineas((a) => a.filter((x) => x.sku !== l.sku))}
                  >
                    Quitar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {error && (
        <p className="mensaje mensaje--error" role="alert">
          {error}
        </p>
      )}
      {exito && (
        <p className="mensaje mensaje--exito" role="status">
          {exito}
        </p>
      )}

      <div className="formulario__acciones">
        {total !== null && lineas.length > 0 && (
          <p className="formulario__resumen">
            {piezas} piezas, total de la factura <strong>{pesos(total)}</strong>
          </p>
        )}
        <button type="button" className="boton boton--principal" onClick={registrar} disabled={enviando}>
          {enviando ? "Registrando…" : "Registrar recepción"}
        </button>
      </div>
    </section>
  );
}
