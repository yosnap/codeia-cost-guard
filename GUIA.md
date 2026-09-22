# Guía — control del gasto de tokens

## La idea en una frase

No es el *effort* del modelo lo que te arruina: es **cuántas veces le mandas el contexto**. Un turno
caro es un turno con mucho contexto delante. El modelo "low" con 200.000 tokens de contexto, 40
turnos y 3 agentes en paralelo sale más caro que el modelo "high" con 30.000 tokens y un solo agente.

## El precipicio de los 272K

Por encima de 272.000 tokens de entrada, **la petición entera** se factura a 2× entrada y 1,5×
salida. No solo el exceso: toda la petición. Un prompt de 272.001 tokens cuesta el doble que uno de
272.000. Por eso lo importante no es "no pasarse", es **no acercarse**.

## Las cinco palancas

1. **Contexto corto.** Por debajo de 200.000 tokens. Compactar o resumir antes, nunca cruzar 272K.
2. **Menos turnos.** Cada turno reenvía el contexto (y por eso el 75 % del gasto suele ser lectura de
   caché). Menos idas y venidas, más trabajo por turno.
3. **Paralelismo con criterio.** Tarea bien especificada → **un** agente. Solo si la tarea es ambigua
   o exploratoria vale la pena un fan-out, y como máximo **3**, dispersando entre modelos distintos
   en vez de lanzar tres copias del mismo (mismo coste, información repetida).
4. **Modelo por tarea.** El modelo grande para decidir, el pequeño para ejecutar lo mecánico.
5. **Facturación por lotes.** Lo que no es urgente, por Batch: −50 %. Y Fast (2×) apagado.

## Lo que mide esta herramienta

- **Hoy** y **ritmo de la última hora**: para ver si te estás quemando *ahora*, no mañana.
- **Por día / por modelo / por proyecto**: para saber de dónde sale.
- Los turnos **duplicados por los forks** de Claude Code se descartan, para no contar dos veces.

## Lo que NO es

Los dólares son **equivalente API**: lo que habrías pagado por esos tokens al precio de lista. Si vas
con suscripción (ChatGPT/Claude Max/Codex), no pagas por token: lo que se agota es la cuota. Los
números sirven igual, porque miden lo mismo que consume la cuota: **tokens × turnos**.

## Dónde vive el gasto según el caso

- **Claude Code, Codex, OpenCode locales**: en sus logs (`~/.claude`, `~/.codex`, `~/.opencode`). Esto
  es lo que lee el medidor.
- **Agentes que corren en la nube** (tareas en la nube, VPS, CI): **no** dejan log en tu Mac. Hay que
  ejecutar el mismo medidor en esas máquinas y sumar.
- **Hermes**: guarda su consumo real en `~/.hermes/profiles/<perfil>/state.db` (tablas `sessions` y
  `session_model_usage`, con `estimated_cost_usd`). Medido en un perfil normal: céntimos al mes.
