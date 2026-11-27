// El token se guarda en el navegador para no pedir PIN otra vez si se recarga
// la pantalla. Si el almacenamiento no está disponible, la sesión dura lo que
// dure la pestaña.
const CLAVE = "pumaverso.caja.token";

export function leerToken(): string | null {
  try {
    return window.localStorage.getItem(CLAVE);
  } catch {
    return null;
  }
}

export function guardarToken(token: string): void {
  try {
    window.localStorage.setItem(CLAVE, token);
  } catch {
    // Sin almacenamiento: la sesión vive solo en memoria.
  }
}

export function borrarToken(): void {
  try {
    window.localStorage.removeItem(CLAVE);
  } catch {
    // Nada que borrar.
  }
}
