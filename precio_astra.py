#!/usr/bin/env python3
"""Coste real de una llamada a GPT-6 Astra (y de una flota de agentes). Verificado 22-09-2026.

Reglas de la tarjeta oficial (developers.openai.com/api/docs/models/gpt-6-astra):
  · standard: entrada 10 $/M · caché leída 1 $/M · escritura de caché 12,50 $/M · salida 50 $/M
  · si la ENTRADA de la petición pasa de 272.000 tokens, TODA la petición se recalcula:
    2x entrada y caché, 1,5x salida (no solo el exceso)
  · batch y flex = 50% de standard · fast = 2x standard · residencia de datos = +10%
  · los tokens de razonamiento se facturan como salida
""" 
CLIFF = 272_000
RATES = {"in": 10.0, "cache_read": 1.0, "cache_write": 12.50, "out": 50.0}
MODE = {"standard": 1.0, "batch": 0.5, "flex": 0.5, "fast": 2.0}

def price(usage, mode="standard", residency=False):
    """usage: {"in": total de entrada (incluye la cacheada), "cache_read": ..., "cache_write": ..., "out": ...}"""
    over = usage["in"] > CLIFF
    inm, outm = (2.0, 1.5) if over else (1.0, 1.0)
    r = RATES
    usd = (max(usage["in"] - usage.get("cache_read", 0), 0) * r["in"] * inm
           + usage.get("cache_read", 0) * r["cache_read"] * inm
           + usage.get("cache_write", 0) * r["cache_write"] * inm
           + usage["out"] * r["out"] * outm) / 1e6
    usd *= MODE[mode]
    if residency:
        usd *= 1.10
    return round(usd, 4)

def preflight(prompt_tokens, tier_tpm, margen=0.30):
    """Devuelve un informe ANTES de gastar. margen: % sobre el cliff que dejamos libre."""
    techo_seguro = int(CLIFF * (1 + margen))
    informe = {
        "tokens_entrada": prompt_tokens,
        "cabe_en_tu_tier": prompt_tokens <= tier_tpm,
        "pasa_el_cliff": prompt_tokens > CLIFF,
        "usa_residencia_datos": None,
        "coste_solo_entrada_standard": round(prompt_tokens * RATES["in"] / 1e6, 4),
        "coste_solo_entrada_si_pasa": round(prompt_tokens * RATES["in"] * 2 / 1e6, 4),
    }
    if prompt_tokens > tier_tpm:
        informe["veredicto"] = "BLOQUEA: no cabe en un minuto de tu tier"
    elif prompt_tokens > techo_seguro:
        informe["veredicto"] = "RECORTA: vas a cruzar el cliff y toda la peticion se recalcula"
    elif prompt_tokens > CLIFF:
        informe["veredicto"] = "AVISO: ya estas en tarifa doble"
    else:
        informe["veredicto"] = "OK"
    return informe

def coste_turno(contexto, salida=4000, cache_hit=0.95, modo="standard"):
    """Lo que cuesta UN turno de un agente: contexto entero + salida (el razonamiento cuenta como salida)."""
    cache = int(contexto * cache_hit)
    return price({"in": contexto, "cache_read": cache, "out": salida}, mode=modo)

if __name__ == "__main__":
    # autopruebas contra cifras publicadas
    assert price({"in": 272_000, "out": 0}) == 2.72, price({"in": 272_000, "out": 0})
    assert price({"in": 272_001, "out": 0}) == 5.44, price({"in": 272_001, "out": 0})     # doblado
    assert price({"in": 300_000, "out": 10_000}) == 6.75                                 # Atlas Cloud
    assert price({"in": 300_000, "out": 10_000}, mode="batch") == 3.375                  # batch -50%
    assert price({"in": 300_000, "out": 10_000}, mode="fast") == 13.5                    # fast 2x
    print("autopruebas OK · 272K ==", price({"in":272_000,"out":0}), "| 272.001 ==", price({"in":272_001,"out":0}))
    print("turno con 180K de contexto, 4K de salida, 95% cache:", coste_turno(180_000), "USD")
    print("lo mismo x 3 agentes en paralelo:", round(coste_turno(180_000)*3, 2), "USD por ronda")
    print("preflight 250K en Tier 3:", preflight(250_000, 2_000_000)["veredicto"])
    print("preflight 300K en Tier 3:", preflight(300_000, 2_000_000)["veredicto"])
