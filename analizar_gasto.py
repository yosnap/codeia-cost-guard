#!/usr/bin/env python3
"""Informe de gasto de los agentes. Envoltorio del escaner de CostBar: mismos numeros que el
icono de la barra de menus y que el aviso de los lunes. Acotado por fecha de turno (no por mtime).

Uso:  python3 analizar_gasto.py [dias]
"""
import os, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if len(sys.argv) > 1 and sys.argv[1].isdigit():
    import costbar
    costbar.DIAS = int(sys.argv[1])
import costbar
r = costbar.escanear()
print(costbar.informa(r))
print(f"\nficheros reescaneados en esta pasada: {r['nuevos']} · limite ritmo {r['limites']['limite_hora_tokens']:,.0f} tokens/h")
print("modelos hoy (detalle):", " · ".join(f"{k}={v['tok']:.0f}" for k, v in sorted(r["modelos_hoy"].items(), key=lambda x: -x[1]["tok"])))
