# En el repo publico este fichero se llama analizar_gasto.py (antes medir_consumo_orca.py).
#!/usr/bin/env python3
"""Informe de gasto de los agentes. Envoltorio del escaner de CostBar: mismos numeros que el
icono de la barra de menus y que el aviso de los lunes. Acotado por fecha de turno (no por mtime).

Uso:  python3 medir_consumo_orca.py [dias]
"""
import os, sys, subprocess
T = os.path.expanduser("~/.hermes/profiles/codeia/tools/costbar")
sys.path.insert(0, T)
if len(sys.argv) > 1 and sys.argv[1].isdigit():
    import costbar
    costbar.DIAS = int(sys.argv[1])
import costbar
r = costbar.escanear()
print(costbar.informa(r))
print(f"\nficheros reescaneados en esta pasada: {r['nuevos']} · limite ritmo {r['limites']['limite_hora_usd']:.0f} USD/h")
print("modelos hoy (detalle):", " · ".join(f"{k}={v:.2f}" for k, v in sorted(r["modelos"].items(), key=lambda x: -x[1])))
