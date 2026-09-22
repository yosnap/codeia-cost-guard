# Reglas para pegar en AGENTS.md / CLAUDE.md

`python3 instalar_reglas_globales.py` lo hace solo en `~/.codex/AGENTS.md` y `~/.claude/CLAUDE.md`.
Este es el bloque que escribe (y que puedes pegar a mano donde quieras):

```markdown
## Gasto de tokens (obligatorio)

- **Contexto**: no pases de 200.000 tokens. Compacta o resume antes; no cruces los 272K (a partir de
  ahi toda la peticion se factura a 2x entrada y 1.5x salida).
- **Editar ficheros**: parches/diffs, nunca reescribir el fichero entero (la salida es lo caro).
- **Paralelismo**: tarea especificada -> 1 agente. Fan-out maximo 3, solo para tareas ambiguas, y
  dispersando entre modelos distintos en vez de copias del mismo.
- **Cache**: no cambies el prompt de sistema ni el orden de herramientas a mitad de sesion.
- **Lotes**: trabajo asincrono por Batch (-50 %). Fast (2x) apagado.
```

**Importante**: estas reglas van en el fichero de instrucciones de los agentes, **nunca** en la
memoria de un agente (la memoria se inyecta en *cada* turno y el remedio costaría más que la
enfermedad).
