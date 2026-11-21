# Guía de contribución — Pumaverso POS

Este documento establece las reglas de colaboración para los integrantes del equipo Pumaverso Distribuido.

Su objetivo es mantener un flujo de trabajo organizado, evitar conflictos entre cambios y garantizar que el código integrado en la rama principal sea funcional.

## 1. Organización del repositorio

El repositorio utiliza Git y GitHub para administrar el código fuente y coordinar el trabajo del equipo.

La rama principal es:

- `main`: contiene la versión estable e integrada del proyecto.

El desarrollo de nuevas funcionalidades, correcciones y documentación debe realizarse en ramas independientes.

**No se deben realizar cambios directamente sobre `main`.**

## 2. Estrategia de ramas

Se utilizará una estrategia sencilla basada en ramas de funcionalidades (*feature branches*).

Cada integrante crea una rama para trabajar en una tarea específica.

### 2.1. Convención de nombres

Los nombres deben ser descriptivos y utilizar minúsculas, separando las palabras con guiones.

| Prefijo | Uso | Ejemplo |
|---|---|---|
| `feature/` | Nueva funcionalidad | `feature/pos-ventas` |
| `fix/` | Corrección de errores | `fix/calculo-total` |
| `test/` | Incorporación de pruebas | `test/inventario` |
| `docs/` | Documentación | `docs/arquitectura` |
| `chore/` | Configuración y mantenimiento | `chore/dependencias` |

Cada rama debe tener un propósito concreto. Se recomienda no mezclar funcionalidades independientes en una misma rama.

### 2.2. Crear una rama

Antes de comenzar una tarea, actualizar la información del repositorio y crear una rama a partir de `main`:

```bash
git fetch origin
git switch main
git pull origin main
git switch -c feature/nombre-funcionalidad
```

Sustituir `nombre-funcionalidad` por el nombre correspondiente a la tarea.

### 2.3. Trabajar en una rama

Durante el desarrollo se recomienda realizar commits pequeños y relacionados con un cambio específico.

Antes de confirmar cambios:

```bash
git status
git diff
```

Después:

```bash
git add .
git commit -m "feat: descripcion del cambio"
```

Cuando se necesite actualizar la rama con los últimos cambios de `main`, se debe coordinar la integración para evitar sobrescribir trabajo de otros integrantes.

### 2.4. Subir la rama

Para publicar la rama en GitHub:

```bash
git push -u origin feature/nombre-funcionalidad
```

Los siguientes cambios de esa misma rama pueden subirse con:

```bash
git push
```

## 3. Convención de commits

Se utilizarán mensajes de commit basados en Conventional Commits.

Formato:

```text
tipo: descripción breve del cambio
```

Tipos principales:

| Tipo | Uso |
|---|---|
| `feat` | Nueva funcionalidad |
| `fix` | Corrección de errores |
| `docs` | Cambios en documentación |
| `test` | Incorporación o modificación de pruebas |
| `refactor` | Reorganización de código sin cambiar su comportamiento |
| `chore` | Configuración y mantenimiento |

Ejemplos:

```bash
git commit -m "feat: registrar ventas locales"
git commit -m "fix: corregir calculo del total"
git commit -m "test: agregar pruebas de inventario"
git commit -m "docs: documentar arquitectura local"
git commit -m "chore: configurar dependencias"
```

Los mensajes deben describir el cambio realizado, no solamente indicar que se modificaron archivos.

## 4. Pull Requests e integración

Una vez terminada una tarea, el integrante debe abrir un Pull Request (PR) en GitHub.

El PR debe:

1. Explicar qué funcionalidad o corrección incorpora.
2. Describir los archivos principales modificados.
3. Indicar las pruebas realizadas.
4. Mencionar posibles cambios que puedan afectar otros módulos.
5. Relacionarse con la tarea correspondiente, cuando exista.

### 4.1. Revisión de código

Cada Pull Request debe ser revisado por al menos otro integrante antes de integrarse.

El revisor debe verificar:

- Que los cambios respeten el contexto y la arquitectura.
- Que no introduzcan dependencias innecesarias.
- Que no rompan funcionalidades existentes.
- Que las pruebas sean satisfactorias.
- Que los cambios no sobrescriban trabajo de otros integrantes.

El autor debe atender las observaciones antes de solicitar la integración definitiva.

### 4.2. Integración

Una vez aprobado el PR, se integra a `main`.

Después de la integración, la rama de trabajo puede eliminarse.

Se recomienda utilizar la opción **Squash and merge** para mantener un historial de integración más limpio, salvo que el equipo acuerde otra estrategia.

## 5. Reglas para evitar conflictos

- No trabajar directamente en `main`.
- Actualizar `main` antes de crear una rama nueva.
- Evitar modificar simultáneamente los mismos archivos sin coordinación.
- Avisar al equipo cuando se necesite modificar una interfaz compartida.
- No sobrescribir cambios de otros integrantes.
- No subir archivos temporales, entornos virtuales, bases de datos locales ni secretos.
- No integrar cambios que no hayan sido revisados.
- Resolver los conflictos de Git comprendiendo los cambios de ambas partes, sin eliminar código arbitrariamente.

## 6. Entorno de desarrollo

El monolito local utiliza Python 3.13.

Se recomienda crear un entorno virtual por cada instalación local del repositorio.

En Windows (Git Bash):

```bash
py -3.13 -m venv .venv
source .venv/Scripts/activate
```

En macOS o Linux:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

Instalar el proyecto y sus dependencias de desarrollo desde la raíz:

```bash
python -m pip install -e "apps/tienda[dev]"
```

Verificar la instalación:

```bash
python --version
python -m pip list
```

El directorio `.venv/` no debe subirse al repositorio.

Cada integrante debe mantener su entorno virtual independiente.

## 7. Pruebas y calidad del código

Antes de abrir un Pull Request, el integrante debe ejecutar las pruebas relacionadas con sus cambios.

Comando general para ejecutar las pruebas:

```bash
python -m pytest
```

Para revisar el estilo y posibles problemas del código:

```bash
python -m ruff check apps/tienda
```

Los cambios deben incluir pruebas cuando incorporen o modifiquen comportamientos importantes.

No se debe considerar terminada una funcionalidad únicamente porque el código se ejecuta sin errores.

## 8. Uso de herramientas de IA

Se permite utilizar herramientas de IA como apoyo para:

- Comprender código existente.
- Diseñar soluciones.
- Generar código y pruebas.
- Detectar errores.
- Mejorar documentación.
- Explicar tecnologías y decisiones técnicas.

Sin embargo, cada integrante es responsable de comprender, revisar y probar los cambios que incorpora al repositorio.

Toda herramienta de IA debe recibir como referencia:

- `README.md`
- `docs/CONTEXTO_PROYECTO.md`
- Los archivos relacionados con la tarea correspondiente.

Las herramientas de IA deben respetar la arquitectura y las decisiones del equipo.

No se deben aceptar cambios automáticamente sin revisar su impacto en otros módulos.

La IA es una herramienta de apoyo, no sustituye la revisión técnica del equipo.

## 9. Coordinación del equipo

Antes de comenzar una tarea, se recomienda comunicar al equipo:

- Qué funcionalidad se desarrollará.
- Qué módulo se modificará.
- Qué archivos podrían verse afectados.
- Si se necesitan cambios en interfaces compartidas.

Cuando una tarea dependa de otra, los integrantes deben acordar el orden de implementación y las interfaces necesarias.

Las decisiones que modifiquen la arquitectura deben discutirse y documentarse antes de implementarse.

## 10. Objetivo de estas reglas

El propósito de esta guía es que los cuatro integrantes puedan desarrollar funcionalidades de manera paralela, manteniendo una base de código organizada, revisable y estable.

La prioridad es integrar cambios compatibles con la arquitectura de Pumaverso POS y garantizar que el sistema evolucione sin comprometer su funcionamiento local ni su capacidad de sincronización.
