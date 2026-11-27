import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ErrorApi, SinServicioLocal, api } from "../api";
import { pesos } from "../dinero";
import type { Producto, ProductoListado, VentaCreada } from "../tipos";
import { type LineaTicket, Ticket, totalDe } from "./Ticket";

interface Props {
  token: string;
  alVencerSesion: (motivo: string) => void;
}

type Mensaje = { tipo: "error" | "aviso" | "exito"; texto: string } | null;

const ESPERA_BUSQUEDA_MS = 250;

export function Cobro({ token, alVencerSesion }: Props) {
  const [lineas, setLineas] = useState<LineaTicket[]>([]);
  const [ultimaAgregada, setUltimaAgregada] = useState<string | null>(null);
  const [codigo, setCodigo] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [resultados, setResultados] = useState<ProductoListado[]>([]);
  const [recibido, setRecibido] = useState("");
  const [mensaje, setMensaje] = useState<Mensaje>(null);
  const [cobrando, setCobrando] = useState(false);
  const [cobrada, setCobrada] = useState<{ venta: VentaCreada; cambio: number | null } | null>(
    null,
  );
  const escaner = useRef<HTMLInputElement>(null);

  const total = totalDe(lineas);
  const recibidoCentavos = Math.round(Number.parseFloat(recibido.replace(",", ".")) * 100);
  const cambio = Number.isFinite(recibidoCentavos) ? recibidoCentavos - total : null;

  function agregar(producto: Producto) {
    setCobrada(null);
    setLineas((actuales) => {
      const existente = actuales.find((l) => l.sku === producto.sku);
      if (existente) {
        return actuales.map((l) => (l.sku === producto.sku ? { ...l, cantidad: l.cantidad + 1 } : l));
      }
      return [
        ...actuales,
        { sku: producto.sku, nombre: producto.nombre, precio_centavos: producto.precio_centavos, cantidad: 1 },
      ];
    });
    setUltimaAgregada(producto.sku);
    setMensaje(null);
  }

  function cambiarCantidad(sku: string, cantidad: number) {
    if (cantidad < 1) return quitar(sku);
    setLineas((actuales) => actuales.map((l) => (l.sku === sku ? { ...l, cantidad } : l)));
  }

  function quitar(sku: string) {
    setLineas((actuales) => actuales.filter((l) => l.sku !== sku));
    escaner.current?.focus();
  }

  function mostrarError(e: unknown, contexto?: string) {
    if (e instanceof ErrorApi && (e.estado === 401 || e.estado === 403)) {
      alVencerSesion(e.detalle);
    } else if (e instanceof ErrorApi && e.estado === 404 && contexto) {
      setMensaje({ tipo: "error", texto: `No hay ningún producto con el código ${contexto}.` });
    } else if (e instanceof ErrorApi || e instanceof SinServicioLocal) {
      setMensaje({ tipo: "error", texto: e.message });
    } else {
      setMensaje({ tipo: "error", texto: "Ocurrió un error inesperado." });
    }
  }

  async function escanear(evento: FormEvent) {
    evento.preventDefault();
    const sku = codigo.trim();
    if (!sku) return;
    setCodigo("");
    try {
      agregar(await api.consultarProducto(sku));
    } catch (e) {
      mostrarError(e, sku);
    }
  }

  const cobrar = useCallback(async () => {
    if (lineas.length === 0 || cobrando) return;
    if (cambio !== null && recibido && cambio < 0) {
      setMensaje({ tipo: "error", texto: `Faltan ${pesos(-cambio)} para cubrir el total.` });
      return;
    }
    setCobrando(true);
    try {
      const venta = await api.cobrar(
        token,
        lineas.map((l) => ({ sku: l.sku, cantidad: l.cantidad })),
      );
      // El cambio se sigue mostrando después de cobrar: es cuando el cajero lo necesita.
      setCobrada({ venta, cambio: recibido && cambio !== null ? cambio : null });
      setLineas([]);
      setRecibido("");
      setUltimaAgregada(null);
      if (venta.alerta_inventario_negativo) {
        setMensaje({
          tipo: "aviso",
          texto:
            "Venta cobrada. Es la tercera venta de hoy sin existencias registradas: avisa al gerente para revisar el inventario.",
        });
      } else if (venta.inventario_negativo) {
        setMensaje({
          tipo: "aviso",
          texto: "Venta cobrada. El sistema no tenía existencias de algún producto; el gerente lo revisará.",
        });
      } else {
        setMensaje(null);
      }
    } catch (e) {
      mostrarError(e);
    } finally {
      setCobrando(false);
      escaner.current?.focus();
    }
    // mostrarError solo usa setters estables y la prop alVencerSesion.
  }, [lineas, cobrando, cambio, recibido, token]);

  // F12 cobra desde cualquier parte de la pantalla, como en las cajas reales.
  useEffect(() => {
    function alPresionar(evento: KeyboardEvent) {
      if (evento.key === "F12") {
        evento.preventDefault();
        cobrar();
      }
    }
    window.addEventListener("keydown", alPresionar);
    return () => window.removeEventListener("keydown", alPresionar);
  }, [cobrar]);

  // Búsqueda por nombre, con una pequeña espera para no consultar en cada tecla.
  useEffect(() => {
    const texto = busqueda.trim();
    if (!texto) {
      setResultados([]);
      return;
    }
    let vigente = true;
    const espera = window.setTimeout(async () => {
      try {
        const encontrados = await api.buscarProductos(texto);
        if (vigente) setResultados(encontrados);
      } catch (e) {
        if (vigente) mostrarError(e);
      }
    }, ESPERA_BUSQUEDA_MS);
    return () => {
      vigente = false;
      window.clearTimeout(espera);
    };
  }, [busqueda]);

  function elegir(producto: ProductoListado) {
    agregar(producto);
    setBusqueda("");
    escaner.current?.focus();
  }

  return (
    <div className="cobro">
      <section className="captura" aria-label="Captura de productos">
        <form onSubmit={escanear}>
          <label className="campo">
            <span className="campo__nombre">Código del producto (Enter para agregar)</span>
            <input
              ref={escaner}
              className="campo__entrada campo__entrada--escaner"
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
              placeholder="Escanea o escribe el código"
              autoComplete="off"
              spellCheck={false}
              autoFocus
            />
          </label>
        </form>

        <label className="campo">
          <span className="campo__nombre">Buscar por nombre</span>
          <input
            className="campo__entrada"
            type="search"
            value={busqueda}
            onChange={(e) => setBusqueda(e.target.value)}
            placeholder="Por ejemplo: refresco, leche, pan"
            autoComplete="off"
          />
        </label>

        {mensaje && (
          <p className={`mensaje mensaje--${mensaje.tipo}`} role={mensaje.tipo === "error" ? "alert" : "status"}>
            {mensaje.texto}
          </p>
        )}

        {busqueda.trim() && (
          <ul className="resultados" aria-label="Productos encontrados">
            {resultados.length === 0 && <li className="resultados__vacio">Sin coincidencias.</li>}
            {resultados.map((p) => (
              <li key={p.sku}>
                <button
                  type="button"
                  className="resultado"
                  onClick={() => elegir(p)}
                  disabled={!p.precio_vigente}
                >
                  <span className="resultado__nombre">{p.nombre}</span>
                  <span className="resultado__precio">{pesos(p.precio_centavos)}</span>
                  <span className="resultado__extra">
                    {p.precio_vigente
                      ? `${p.existencia} en tienda`
                      : "Precio vencido: hace falta sincronizar con central"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {cobrada && (
          <div className="cobrada" role="status">
            <p>
              Venta {cobrada.venta.consecutivo} cobrada por {pesos(cobrada.venta.total_centavos)}.
            </p>
            {cobrada.cambio !== null && (
              <p className="cobrada__cambio">Cambio: {pesos(cobrada.cambio)}</p>
            )}
          </div>
        )}
      </section>

      <aside className="mostrador">
        <Ticket
          lineas={lineas}
          ultimaAgregada={ultimaAgregada}
          alCambiarCantidad={cambiarCantidad}
          alQuitar={quitar}
        />

        <div className="pago">
          <label className="campo campo--en-linea">
            <span className="campo__nombre">Paga con</span>
            <input
              className="campo__entrada"
              inputMode="decimal"
              value={recibido}
              onChange={(e) => setRecibido(e.target.value)}
              placeholder="0.00"
              disabled={lineas.length === 0}
            />
          </label>
          {recibido && cambio !== null && cambio >= 0 && (
            <p className="pago__cambio">
              Cambio <strong>{pesos(cambio)}</strong>
            </p>
          )}
        </div>

        <button
          type="button"
          className="boton boton--cobrar"
          onClick={cobrar}
          disabled={lineas.length === 0 || cobrando}
        >
          {cobrando ? "Cobrando…" : `Cobrar ${pesos(total)}`}
          <kbd className="boton__tecla">F12</kbd>
        </button>
      </aside>
    </div>
  );
}
