import { type FormEvent, useEffect, useRef, useState } from "react";
import { ErrorApi, SinServicioLocal, api } from "../../api";
import type { Producto, ProductoListado } from "../../tipos";

interface Props {
  /** Texto de la etiqueta del campo de código. */
  etiqueta: string;
  alElegir: (producto: Producto) => void;
  autoFocus?: boolean;
}

const ESPERA_BUSQUEDA_MS = 250;

/** Escanear, escribir el código o buscar por nombre. Igual que en Cobrar. */
export function BuscadorProducto({ etiqueta, alElegir, autoFocus = false }: Props) {
  const [codigo, setCodigo] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [resultados, setResultados] = useState<ProductoListado[]>([]);
  const [error, setError] = useState<string | null>(null);
  const escaner = useRef<HTMLInputElement>(null);

  async function buscarCodigo(evento: FormEvent) {
    evento.preventDefault();
    const sku = codigo.trim();
    if (!sku) return;
    setCodigo("");
    try {
      alElegir(await api.consultarProducto(sku));
      setError(null);
    } catch (e) {
      if (e instanceof ErrorApi && e.estado === 404) setError(`No hay ningún producto con el código ${sku}.`);
      else if (e instanceof ErrorApi || e instanceof SinServicioLocal) setError(e.message);
      else setError("No se pudo consultar el producto.");
    }
  }

  useEffect(() => {
    const texto = busqueda.trim();
    if (!texto) {
      setResultados([]);
      return;
    }
    let vigente = true;
    const espera = window.setTimeout(async () => {
      try {
        const encontrados = await api.buscarProductos(texto, 8);
        if (vigente) setResultados(encontrados);
      } catch (e) {
        if (vigente) setError(e instanceof Error ? e.message : "No se pudo buscar.");
      }
    }, ESPERA_BUSQUEDA_MS);
    return () => {
      vigente = false;
      window.clearTimeout(espera);
    };
  }, [busqueda]);

  function elegir(producto: Producto) {
    alElegir(producto);
    setBusqueda("");
    setError(null);
    escaner.current?.focus();
  }

  return (
    <div className="buscador">
      <form onSubmit={buscarCodigo}>
        <label className="campo">
          <span className="campo__nombre">{etiqueta}</span>
          <input
            ref={escaner}
            className="campo__entrada"
            value={codigo}
            onChange={(e) => setCodigo(e.target.value)}
            placeholder="Escanea o escribe el código"
            autoComplete="off"
            spellCheck={false}
            autoFocus={autoFocus}
          />
        </label>
      </form>
      <label className="campo">
        <span className="campo__nombre">O búscalo por nombre</span>
        <input
          className="campo__entrada"
          type="search"
          value={busqueda}
          onChange={(e) => setBusqueda(e.target.value)}
          placeholder="Por ejemplo: leche"
          autoComplete="off"
        />
      </label>
      {error && (
        <p className="mensaje mensaje--error" role="alert">
          {error}
        </p>
      )}
      {busqueda.trim() && (
        <ul className="resultados" aria-label="Productos encontrados">
          {resultados.length === 0 && <li className="resultados__vacio">Sin coincidencias.</li>}
          {resultados.map((p) => (
            <li key={p.sku}>
              <button type="button" className="resultado" onClick={() => elegir(p)}>
                <span className="resultado__nombre">{p.nombre}</span>
                <span className="resultado__precio">{p.existencia} en tienda</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
