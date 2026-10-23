"""Desplegable: zonas de clic (y la conversion al eje de AppKit), escapado de datos remotos, CSP,
y que las dos pestanas se generan en todos los estados (sin servidores, con error, con datos).

Arrancar con:  python3 -m unittest discover tests
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401  (HOME y rutas temporales: antes que costbar)
from infobar import desplegable_agentes, desplegable_servidores, servidores_estado, servidores_ssh  # noqa: E402
from infobar import geometria_desplegable as G  # noqa: E402

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos")


class ZonasTest(unittest.TestCase):
    def test_cabecera_desde_arriba_y_pie_desde_abajo(self):
        for alto in (600, 1100):                       # el mismo clic vale con cualquier alto
            # AppKit: vista sin voltear, y=0 abajo
            self.assertEqual(G.zona_en(100, alto - (G.PESTANAS_Y + 10), alto, 3, True), "pestana:agentes")
            self.assertEqual(G.zona_en(300, alto - (G.PESTANAS_Y + 10), alto, 3, True), "pestana:servidores")
            self.assertEqual(G.zona_en(200, alto - (G.SELECTOR_Y + 5), alto, 3, True), "selector")
            self.assertEqual(G.zona_en(G.ANCHO - 30, alto - 30, alto, 3, True), "refrescar")
            self.assertEqual(G.zona_en(40, G.LINEA_ALTO + 20, alto, 3, True), "boton:0")
            self.assertEqual(G.zona_en(G.ANCHO - 40, G.LINEA_ALTO + 20, alto, 3, True), "boton:2")
            self.assertEqual(G.zona_en(G.ANCHO - 10, 10, alto, 3, True), "menu")
            self.assertIsNone(G.zona_en(200, alto / 2, alto, 3, True))

    def test_sin_selector_en_agentes(self):
        self.assertIsNone(G.zona_en(200, 600 - (G.SELECTOR_Y + 5), 600, 2, False))


def _entrada(nombre_fichero, nombre_servicio="tienda-production_tienda-app", servicio_ok=True):
    with open(os.path.join(DATOS, nombre_fichero), encoding="utf-8") as f:
        d = servidores_ssh.interpretar(servidores_ssh.secciones(f.read()))
    d["servicios"] = [{"nombre": nombre_servicio, "ok": servicio_ok, "detalle": "1/1"}]
    webs = [{"url": "https://tienda.example.com", "ms": 120, "codigo": 200, "error": ""}]
    t = servidores_estado.tarjetas(d, webs, [22, 80, 443])
    checks = servidores_estado.bastionado(d, [22, 80, 443])
    return {"datos": d, "webs": webs, "expuestos": [22, 80, 443], "tarjetas": t, "bastionado": checks,
            "bastionado_resumen": servidores_estado.resumen_bastionado(checks), "estado": "ambar",
            "ok_en": time.time(), "error": ""}


TODAS = frozenset({"motivos", "falta", "intentos", "caidos"})


class PaginasTest(unittest.TestCase):
    CFG = [{"nombre": "tienda", "ssh": "infobar@tienda.example.com"}]

    def test_datos_hostiles_se_escapan(self):
        e = _entrada("servidor-dokploy-con-root.txt", '<img src=x onerror=alert(1)>', servicio_ok=False)
        e["webs"] = [{"url": "https://<b>x</b>.example.com", "ms": 5, "codigo": 200, "error": ""}]
        h = desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, True, abiertas=TODAS)
        self.assertNotIn("<img src=x", h)
        self.assertNotIn("<b>x</b>", h)
        self.assertIn("&lt;img src=x", h)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", h)
        self.assertIn("default-src 'none'", h)          # CSP: ni scripts ni red

    def test_la_tarjeta_de_webs_rotula_el_certificado(self):
        def pagina(webs):
            e = _entrada("servidor-dokploy-con-root.txt")
            e["webs"] = webs
            e["tarjetas"] = servidores_estado.tarjetas(e["datos"], webs, [22, 80, 443])
            return desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, False)
        w = {"url": "https://tienda.example.com", "ms": 120, "codigo": 200, "error": "", "cert_dias": 25}
        self.assertIn("certificado: 25 d", pagina([w]))
        malo = dict(w, cert_dias=None, ms=None, codigo=None, error="certificado: CERTIFICATE_VERIFY_FAILED")
        h = pagina([w, malo])
        self.assertIn("certificado inválido", h)
        self.assertNotIn("certificado: 25 d", h)                      # con una web inválida no se presume la fecha de otra
        self.assertNotIn("certificado:", pagina([dict(w, cert_dias=None)]))

    def test_estados_de_la_pestana(self):
        vacio = desplegable_servidores.documento("", {}, [], False)
        self.assertIn("alta-servidor.sh", vacio)
        sin_datos = desplegable_servidores.documento("tienda", {}, self.CFG, False)
        self.assertIn("Todavía sin datos", sin_datos)
        caido = desplegable_servidores.documento(
            "tienda", {"servidores": {"tienda": {"error": "timeout", "estado": "rojo", "intento_en": time.time()}}},
            self.CFG, False)
        self.assertIn("Sin conexión: timeout", caido)
        ok = desplegable_servidores.documento("tienda", {"servidores": {"tienda": _entrada("servidor-dokploy-con-root.txt")}},
                                              self.CFG, True, abiertas=TODAS)
        self.assertIn('class="ip">tienda.example.com<', ok)       # sin host en cache: el de la config
        for txt in ("Procesador", "Protección", "Qué falta", "Quién más intenta entrar", "tienda.example.com", "Por qué no está en verde", "Terminal"):
            self.assertIn(txt, ok)

    def pagina(self, webs=None, servicios=None, abiertas=TODAS):
        e = _entrada("servidor-dokploy-con-root.txt")
        if webs is not None:
            e["webs"] = webs
            e["tarjetas"] = servidores_estado.tarjetas(e["datos"], webs, [22, 80, 443])
        if servicios is not None:
            e["datos"]["servicios"] = servicios
        return desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, False, abiertas=abiertas)

    def test_enseña_todas_las_webs_con_las_que_fallan_primero(self):
        ok = lambda n: {"url": f"https://ok{n}.example.com/", "ms": 100 + n, "codigo": 200, "error": ""}  # noqa: E731
        h = self.pagina([ok(1), {"url": "https://rota.example.com/", "ms": 80, "codigo": 502, "error": ""},
                         {"url": "https://muda.example.com/", "ms": None, "codigo": None, "error": "timeout"},
                         {"url": "https://falta.example.com/", "ms": 50, "codigo": 404, "error": ""}, ok(2)])
        self.assertIn("5 · 2 con error", h)
        for dominio in ("ok1.example.com", "rota.example.com", "muda.example.com", "falta.example.com", "ok2.example.com"):
            self.assertIn(dominio, h)
        self.assertIn("502 · 80 ms", h)
        self.assertIn("sin respuesta", h)
        self.assertLess(h.index("rota.example.com"), h.index("falta.example.com"))      # rojo antes que ámbar
        self.assertLess(h.index("falta.example.com"), h.index("ok1.example.com"))       # ámbar antes que verde
        self.assertIn("2 webs con error o sin responder", h)

    def test_todas_las_webs_bien_lo_dicen_y_un_tope_las_recorta(self):
        webs = [{"url": f"https://w{n}.example.com/", "ms": 10, "codigo": 200, "error": ""} for n in range(20)]
        h = self.pagina(webs)
        self.assertIn("20 · todas responden", h)
        self.assertIn(f"+{20 - desplegable_servidores.MAX_WEBS} más", h)

    def test_los_servicios_sanos_no_se_enseñan_y_los_caidos_si(self):
        sano = self.pagina(servicios=[{"nombre": "p_sano-uno", "ok": True, "detalle": "1/1"}])
        self.assertNotIn("sano-uno", sano)
        self.assertNotIn("Servicios caídos", sano)
        roto = self.pagina(servicios=[{"nombre": "p_sano-uno", "ok": True, "detalle": "1/1"}, {"nombre": "p_roto-dos", "ok": False, "detalle": "0/1"}])
        self.assertIn("Servicios caídos", roto)
        self.assertIn("roto-dos", roto)
        self.assertNotIn("sano-uno", roto)

    def test_explica_por_que_no_esta_en_verde(self):
        h = self.pagina()
        self.assertIn("Por qué no está en verde", h)
        self.assertIn("ajustes de seguridad hechos", h)

    def test_el_motivo_de_fail2ban_distingue_instalado_de_no(self):
        t = {"bloqueadas": {"estado": "ambar"}}
        self.assertIn("no está instalado", desplegable_servidores._motivos(t, {}, {"fail2ban": {"instalado": False}}, [])[0][1])
        self.assertIn("Muchas direcciones", desplegable_servidores._motivos(t, {}, {"fail2ban": {"instalado": True}}, [])[0][1])

    def test_las_secciones_pesadas_empiezan_cerradas_con_aviso_y_se_abren_al_pedirlo(self):
        cerrado = self.pagina(abiertas=frozenset())
        for clave in ("motivos", "falta", "intentos"):
            self.assertIn(f'data-plg="{clave}"', cerrado)
        self.assertIn('class="sec plg cerrado"', cerrado)
        self.assertNotIn("ajustes de seguridad hechos", cerrado)             # el detalle no se ve hasta abrirlo
        self.assertNotIn("ufw default deny", cerrado)
        self.assertIn('<b class="exc rojo">!</b>', cerrado)                  # y el título avisa de que hay algo
        abierto = self.pagina(abiertas=frozenset({"motivos"}))
        self.assertIn("ajustes de seguridad hechos", abierto)
        self.assertIn('class="sec plg abierto"', abierto)
        self.assertNotIn("ufw default deny", abierto)                        # las demás siguen cerradas
        self.assertEqual(cerrado.count('class="mk"'), cerrado.count("data-plg="))

    def test_sin_abiertas_explicitas_se_leen_del_estado_de_la_interfaz(self):
        e = _entrada("servidor-dokploy-con-root.txt")
        with mock.patch.object(desplegable_servidores, "leer_ui", return_value={"abiertas": ["falta", 5, None]}):
            h = desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, False)
        self.assertIn('class="sec plg abierto" data-plg="falta"', h)
        self.assertIn('class="sec plg cerrado" data-plg="motivos"', h)
        with mock.patch.object(desplegable_servidores, "leer_ui", return_value={"abiertas": "no es una lista"}):
            self.assertNotIn("plg abierto", desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, self.CFG, False))

    def test_la_web_lenta_se_marca_y_se_nombra_en_el_motivo(self):
        webs = [{"url": "https://rapida.example/", "codigo": 200, "ms": 300, "error": ""},
                {"url": "https://lenta.example/", "codigo": 200, "ms": servidores_estado.WEB_LENTA_MS + 700, "error": ""}]
        t = servidores_estado.tarjetas({}, webs, [])
        self.assertEqual(t["webs"]["estado"], "ambar")
        self.assertEqual([desplegable_servidores._clase_web(w) for w in webs], ["verde", "ambar"])
        texto = desplegable_servidores._motivos(t, {}, {}, webs)[0][1]
        self.assertIn("lenta.example", texto)
        self.assertNotIn("rapida.example", texto)

    def test_sin_motivos_no_hay_seccion(self):
        t = {k: {"estado": "verde"} for k in ("cpu", "memoria", "disco", "webs", "certificado", "sesiones", "intentos", "bloqueadas", "puertos", "mantenimiento", "servicios")}
        self.assertEqual(desplegable_servidores._motivos(t, {"estado": "verde", "total": 12, "hechos": 12}, {}, []), [])

    def test_agentes(self):
        hoy = time.strftime("%Y-%m-%d")
        vacio = {"gi": 1, "go": 2, "cr": 3, "cw": 4, "tok": 10, "turnos": 1, "usd": 0}
        r = {"limites": {"limite_hora_tokens": 8_000_000, "limite_5h_tokens": 0}, "ritmo": {"tok": 1000},
             "cinco_h": {"tok": 1_500_000}, "semana": dict(vacio), "hoy": dict(vacio), "pct_5h": 0,
             "proveedores": {"NaN <x>": {"cinco_h": 5, "semana": 6, "pct_5h": 0, "pct_semana": 0,
                                         "modelos_cuota": [{"modelo": "glm", "mes": 10, "limite": 100, "pct": 10.0}],
                                         "cuota_fuente": "api"}},
             "cache_pct_hoy": 50.0, "dias": {hoy: {"tok": 10}}, "modelos_hoy": {"claude-opus-5": {"tok": 9}},
             "proyectos": {"GitHub/blog/es": {"tok": 9}}}
        h = desplegable_agentes.documento(r, False)
        self.assertIn("1,5 M", h)
        self.assertIn("NaN &lt;x&gt;", h)
        self.assertIn("en vivo · NaN", h)
        self.assertIn("blog/es", h)


try:
    import PIL  # noqa: F401
    HAY_PIL = True
except ImportError:
    HAY_PIL = False


@unittest.skipUnless(HAY_PIL, "hace falta Pillow")
class MarcasTest(unittest.TestCase):
    def test_encuentra_y_borra_los_marcadores(self):
        from PIL import Image
        from infobar import desplegable_png
        im = Image.new("RGBA", (40, 100), (127, 127, 127, 3))
        for y in list(range(10, 22)) + list(range(50, 62)):
            for x in (0, 1):
                im.putpixel((x, y), (255, 0, 255, 255))
        im.putpixel((0, 80), (255, 0, 255, 128))                 # un píxel parecido pero translúcido no cuenta
        self.assertEqual(desplegable_png.marcas_de_secciones(im), [(10, 22), (50, 62)])
        self.assertEqual(im.getpixel((0, 15)), (127, 127, 127, 3))
        self.assertEqual(im.getpixel((1, 55)), (127, 127, 127, 3))

    def test_marcador_pegado_al_final(self):
        from PIL import Image
        from infobar import desplegable_png
        im = Image.new("RGBA", (10, 20), (0, 0, 0, 0))
        for y in range(15, 20):
            im.putpixel((0, y), (255, 0, 255, 255))
        self.assertEqual(desplegable_png.marcas_de_secciones(im), [(15, 20)])

    def test_render_real_da_las_zonas_de_cada_titulo(self):
        import tempfile
        from PIL import Image
        from infobar import desplegable_png
        chrome = desplegable_png.navegador()
        if chrome is None:
            self.skipTest("no hay Chrome ni navegador compatible")
        e = _entrada("servidor-dokploy-con-root.txt")
        html = desplegable_servidores.documento("tienda", {"servidores": {"tienda": e}}, PaginasTest.CFG, True, abiertas=frozenset({"falta"}))
        destino = os.path.join(tempfile.mkdtemp(prefix="cg-png-"), "p.png")
        r = desplegable_png.html_a_png(html, destino, chrome)
        self.assertIsNotNone(r)
        ancho, alto, secciones = r
        self.assertEqual([i for i, _, _ in secciones], ["motivos", "falta", "intentos"])
        for _, y0, y1 in secciones:
            self.assertTrue(0 < y0 < y1 < alto)
            self.assertAlmostEqual(y1 - y0, 30, delta=1)             # la altura del título plegable
        self.assertLess(secciones[0][2], secciones[1][1])
        self.assertGreater(secciones[2][1] - secciones[1][2], 60)    # «falta» está abierta: entre su título y el siguiente hay contenido
        im = Image.open(destino).convert("RGBA")
        self.assertTrue(all(im.getpixel((0, y)) != (255, 0, 255, 255) for y in range(0, im.size[1], 2)))     # el marcador no queda a la vista


class TandaDeRenderTest(unittest.TestCase):
    """Cuánto Chrome cuesta cada tanda: solo el tema que se ve, Agentes solo cuando toca y un clic corta la tanda."""
    CFG = [{"nombre": "a", "ssh": "u@a"}, {"nombre": "b", "ssh": "u@b"}]

    def setUp(self):
        from infobar import desplegable_png
        self.png = desplegable_png
        self.pintados = []
        for p in (mock.patch.object(desplegable_png, "navegador", return_value="chrome"),
                  mock.patch.object(desplegable_png, "html_a_png", side_effect=self._pinta),
                  mock.patch.object(desplegable_agentes, "documento", return_value="<agentes>"),
                  mock.patch.object(desplegable_servidores, "documento", side_effect=lambda n, *a, **k: f"<{n}>"),
                  mock.patch.object(desplegable_png.permisos, "purgar_render")):
            p.start()
            self.addCleanup(p.stop)

    def _pinta(self, html, ruta, chrome):
        self.pintados.append((html, os.path.basename(ruta).split("-", 1)[1]))
        return (10, 20, [])

    def test_solo_el_tema_pedido_y_sin_restos_del_otro(self):
        previo = self.png.renderizar({}, {}, self.CFG)
        self.assertEqual(len(self.pintados), 6)                     # Agentes + 2 servidores, en claro y oscuro
        self.pintados.clear()
        out = self.png.renderizar({}, {}, self.CFG, previo=previo, temas=("oscuro",))
        self.assertEqual(len(self.pintados), 3)
        self.assertEqual(set(out["agentes"]), {"oscuro"})
        self.assertEqual({n: set(v) for n, v in out["servidores"].items()}, {"a": {"oscuro"}, "b": {"oscuro"}})

    def test_lo_que_no_se_repinta_conserva_su_imagen_del_otro_tema(self):
        previo = self.png.renderizar({}, {}, self.CFG, temas=("claro",))
        out = self.png.renderizar(None, {}, self.CFG, previo=previo, temas=("oscuro",), solo_servidor="a")
        self.assertEqual(set(out["agentes"]), {"claro"})            # Agentes no se repintó: sigue habiendo qué enseñar
        self.assertEqual(set(out["servidores"]["a"]), {"oscuro"})
        self.assertEqual(set(out["servidores"]["b"]), {"claro"})
        self.assertIs(self.png.renderizar(None, {}, [], previo=previo, parar=lambda: True), previo)    # nada pintado: el previo tal cual

    def test_parar_corta_la_tanda_y_entrega_lo_pintado(self):
        out = self.png.renderizar({}, {}, self.CFG, temas=("claro",), parar=lambda: len(self.pintados) >= 2)
        self.assertEqual(len(self.pintados), 2)
        self.assertIn("claro", out["agentes"])
        self.assertEqual(set(out["servidores"]), {"a"})

    def test_el_trabajador_no_repinta_agentes_por_un_cambio_de_webs_y_cede_al_clic(self):
        from infobar import trabajador_barra
        t = trabajador_barra.Trabajador(lambda *a: None, tema=lambda: "claro")
        with mock.patch.object(trabajador_barra, "cfg", return_value={"servidores": self.CFG}):
            t.pide("plegar")                                     # un clic ya esperando: la tanda no llega a pintar nada
            self.assertFalse(t._pinta({}, {}, cortable=True))
            self.assertEqual(self.pintados, [])
            self.assertTrue({"render", "agentes"} <= t._pedidos)  # se vuelve a pedir entera, después del clic
            t._pedidos.clear()
            self.assertTrue(t._pinta(None, {}, cortable=True))     # 'render' sin Agentes: solo los servidores, y del tema que se ve
        self.assertEqual(self.pintados, [("<a>", "servidor0-claro.png"), ("<b>", "servidor1-claro.png")])

    def test_un_clic_pendiente_salta_el_ciclo_sin_perder_lo_pedido(self):
        from infobar import trabajador_barra
        t = trabajador_barra.Trabajador(lambda *a: None, tema=lambda: "claro")
        self.assertFalse(t._clic_esperando({"refrescar"}))
        t.pide("plegar")
        t._evento.clear()
        self.assertTrue(t._clic_esperando({"refrescar", "plegar"}))
        self.assertEqual(t._pedidos, {"plegar", "refrescar"})       # el refresco pedido no se pierde
        self.assertTrue(t._evento.is_set())                         # y la vuelta siguiente empieza ya


if __name__ == "__main__":
    unittest.main()
