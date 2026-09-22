#!/usr/bin/env python3
"""Instala o actualiza las reglas de coste en los ficheros de instrucciones GLOBALES de los
agentes CLI (Codex lee ~/.codex/AGENTS.md, Claude Code lee ~/.claude/CLAUDE.md).

Idempotente: se puede ejecutar mil veces. Hace copia de seguridad la primera vez y respeta
todo lo que ya hubiera escrito el usuario.

NO toca la memoria de Hermes a proposito: la memoria se inyecta en cada turno y las reglas
solo deben cargarse cuando hacen falta.
"""
import os, shutil, time, sys

MARCA_INI, MARCA_FIN = "<!-- gasto-tokens:inicio -->", "<!-- gasto-tokens:fin -->"
BLOQUE = """%s
## Gasto de tokens (obligatorio)

- **Contexto**: no pases de 200.000 tokens. Compacta o resume antes; no cruces los 272K (a partir de
  ahi toda la peticion se factura a 2x entrada y 1.5x salida).
- **Editar ficheros**: parches/diffs, nunca reescribir el fichero entero (la salida es lo caro).
- **Paralelismo**: tarea especificada -> 1 agente. Fan-out maximo 3, solo para tareas ambiguas, y
  dispersando entre modelos distintos en vez de copias del mismo.
- **Cache**: no cambies el prompt de sistema ni el orden de herramientas a mitad de sesion.
- **Lotes**: trabajo asincrono por Batch (-50 %%). Fast (2x) apagado.
%s
""" % (MARCA_INI, MARCA_FIN)

DESTINOS = ["~/.codex/AGENTS.md", "~/.claude/CLAUDE.md"]

def instalar(ruta):
    ruta = os.path.expanduser(ruta)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    original = open(ruta, encoding="utf-8").read() if os.path.exists(ruta) else ""
    respaldo = None
    if original.strip():
        respaldo = f"{ruta}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy(ruta, respaldo)
    if MARCA_INI in original and MARCA_FIN in original:
        i = original.index(MARCA_INI); f = original.index(MARCA_FIN) + len(MARCA_FIN)
        nuevo, accion = original[:i] + BLOQUE.strip() + original[f:], "actualizado"
    else:
        nuevo, accion = ((original.rstrip() + "\n\n" + BLOQUE) if original.strip() else BLOQUE), "añadido"
    open(ruta, "w", encoding="utf-8").write(nuevo)
    return f"{ruta}: {accion} ({len(nuevo)} bytes)" + (f" · respaldo {os.path.basename(respaldo)}" if respaldo else "")

if __name__ == "__main__":
    for d in (sys.argv[1:] or DESTINOS):
        print(" ", instalar(d))
