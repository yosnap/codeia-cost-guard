"""Avisos de escritorio de los servidores: reglas, duración, rearme, silencio, entrega, relojes y ficheros hostiles."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import ajustes, rutas, servidores, servidores_avisos as sa, servidores_historico as sh, servidores_seguimiento as sg  # noqa: E402
from test_servidores_integracion import limpia_config, tearDownModule  # noqa: E402,F401

ID = "a" * 32
ID2 = "b" * 32
T0 = 1_790_000_000
NOMBRE = "srv-secreto"
URL = "https://dominio-secreto.example.com/"


def cache(disco=None, ok=T0, intento=None, error="", nombre=NOMBRE):
    e = {"ok_en": ok, "intento_en": intento if intento is not None else ok, "error": error}
    if disco is not None:
        e["datos"] = {"disco": {"usado": disco, "total": 100}}
    return {"servidores": {nombre: e}}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-av-")
        p = mock.patch.object(rutas, "DATOS", self.dir)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.t = float(T0)
        self.salto = 0.0
        self.enviados = []
        self.entrega = True
        self.hay_red = True
        self.servidores = [{"id": ID, "nombre": NOMBRE, "ssh": "infobar@a.example.com"}]
        self.conf = {"servidores": self.servidores, "intervalo_normal_s": 300}
        self.v = sa.Vigilante(reloj=lambda: self.t, mono=lambda: self.t + self.salto, canal=self.canal, sin_red=lambda: not self.hay_red, canal_real=True)

    def canal(self, titulo, texto):
        self.enviados.append(texto)
        return self.entrega

    def pasa(self, segundos=0, disco=None, **kw):
        self.t += segundos
        if "cache" not in kw:
            kw["cache"] = cache(disco=disco, ok=int(self.t))          # la lectura es siempre «de ahora», ya con el reloj avanzado
        return self.v.vigilar(self.conf, **kw)

    def arranca(self, **kw):
        """Primera pasada (línea base): lo que haya no avisa."""
        self.pasa(**kw)
        self.assertEqual(self.enviados, [])

    def estado(self):
        with open(sa.ruta(), encoding="utf-8") as f:
            return json.load(f)


class PasoTest(unittest.TestCase):
    def e(self):
        return {c: None for c in sa.CAMPOS} | {"intentos": 0}

    def test_confirma_avisa_una_vez_y_no_repite(self):
        e = self.e()
        self.assertFalse(sa.paso(e, True, "disco_lleno", 1000))
        self.assertFalse(sa.paso(e, True, "disco_lleno", 1500))
        self.assertTrue(sa.paso(e, True, "disco_lleno", 1600))
        e["avisado"] = e["ultimo"] = 1600
        self.assertFalse(sa.paso(e, True, "disco_lleno", 5000))

    def test_parpadeo_antes_de_avisar_no_cuenta(self):
        e = self.e()
        sa.paso(e, True, "disco_lleno", 1000)
        sa.paso(e, False, "disco_lleno", 1200)
        self.assertIsNone(e["desde"])
        self.assertFalse(sa.paso(e, True, "disco_lleno", 1500))
        self.assertFalse(sa.paso(e, True, "disco_lleno", 2000))

    def test_no_se_sabe_no_mueve_nada(self):
        e = self.e()
        sa.paso(e, True, "disco_lleno", 1000)
        antes = dict(e)
        self.assertFalse(sa.paso(e, None, "disco_lleno", 9999))
        self.assertEqual(e, antes)

    def test_rearme_solo_tras_el_tiempo_y_el_enfriamiento_manda(self):
        e = self.e()
        e.update(desde=1000, avisado=1600, ultimo=1600)
        sa.paso(e, False, "disco_lleno", 2000)
        sa.paso(e, True, "disco_lleno", 2500)              # vuelve antes del rearme: sigue siendo el mismo aviso
        self.assertEqual(e["avisado"], 1600)
        sa.paso(e, False, "disco_lleno", 3000)
        sa.paso(e, False, "disco_lleno", 3000 + 1800)
        self.assertIsNone(e["avisado"])
        sa.paso(e, True, "disco_lleno", 6000)
        self.assertFalse(sa.paso(e, True, "disco_lleno", 6700))        # confirmado, pero dentro del enfriamiento de 12 h
        self.assertTrue(sa.paso(e, True, "disco_lleno", 1600 + 12 * 3600))

    def test_reintento_espera(self):
        e = self.e()
        e.update(desde=0, reintento_en=500)
        self.assertFalse(sa.paso(e, True, "sin_conexion", 400))
        self.assertTrue(sa.paso(e, True, "sin_conexion", 500))


class DiscoTest(Base):
    def test_linea_base_silenciosa_y_luego_aviso_unico(self):
        self.arranca(cache=cache(disco=95, ok=T0))
        self.pasa(300, cache=cache(disco=95, ok=T0 + 300))
        self.assertEqual(self.enviados, [])
        self.pasa(300, cache=cache(disco=95, ok=T0 + 600))
        self.assertEqual(self.enviados, [])                # lo presente al arrancar ya estaba avisado
        # una condición nueva sí avisa: baja, se rearma y vuelve a subir tras el enfriamiento
        self.pasa(60, disco=50)
        self.pasa(1800, disco=50)
        self.t += 12 * 3600
        self.pasa(0, disco=96)
        self.pasa(600, disco=96)
        self.assertEqual(self.enviados, [sa.TEXTO["disco_lleno"]])
        for _ in range(5):
            self.pasa(300, disco=96)
        self.assertEqual(len(self.enviados), 1)

    def test_una_situacion_que_dura_dias_avisa_una_sola_vez(self):
        self.arranca(cache=cache(disco=50))
        self.pasa(60, disco=95)
        self.pasa(700, disco=95)
        self.assertEqual(len(self.enviados), 1)
        for _ in range(60):
            self.pasa(1800, disco=95)
        self.assertEqual(len(self.enviados), 1)

    def test_histeresis_entre_85_y_90_no_rearma(self):
        self.arranca(cache=cache(disco=50))
        self.pasa(60, cache=cache(disco=95, ok=int(self.t) + 60))
        self.pasa(600, disco=95)
        self.assertEqual(len(self.enviados), 1)
        for _ in range(10):
            self.pasa(300, disco=87)
        self.assertIsNotNone(self.estado()["entradas"][f"disco_lleno|{ID}"]["avisado"])
        self.pasa(300, disco=95)
        self.assertEqual(len(self.enviados), 1)

    def test_picos_aislados_en_la_franja_de_histeresis_no_suman(self):
        self.arranca(cache=cache(disco=88))
        self.pasa(60, disco=91)                            # un pico de una muestra
        self.pasa(60, disco=88)
        self.t += 6 * 3600
        self.pasa(0, disco=91)                             # otro pico, horas después: no hay 10 minutos seguidos
        self.pasa(60, disco=88)
        self.pasa(700, disco=88)
        self.assertEqual(self.enviados, [])

    def test_dato_viejo_o_imposible_no_sabe(self):
        self.arranca(cache=cache(disco=50))
        self.pasa(60, cache=cache(disco=95, ok=T0 - 10000))        # medida de hace tiempo: no cuenta
        self.pasa(900, cache=cache(disco=95, ok=T0 - 10000))
        self.pasa(900, disco=500)   # imposible: ni avisa ni rearma
        self.assertEqual(self.enviados, [])

    def test_ventana_crece_con_el_intervalo(self):
        self.conf["intervalo_normal_s"] = 3600
        self.arranca(cache=cache(disco=50, ok=T0))
        self.pasa(60, cache=cache(disco=95, ok=int(self.t) - 3000))      # lectura de hace 50 min con intervalo de 1 h: sigue valiendo
        self.pasa(600, cache=cache(disco=95, ok=int(self.t) - 3000))
        self.assertEqual(len(self.enviados), 1)


class ConexionTest(Base):
    def pasa_caido(self, segundos, error="timeout"):
        self.t += segundos
        return self.pasa(0, cache={"servidores": {NOMBRE: {"ok_en": T0, "intento_en": int(self.t), "error": error}}})

    def test_sin_conexion_avisa_cuando_pasa_la_ventana(self):
        self.arranca()
        self.pasa_caido(300)
        self.assertEqual(self.enviados, [])
        self.pasa_caido(900)
        self.assertEqual(self.enviados, [])                # pasó la ventana, falta confirmarlo
        self.pasa_caido(130)
        self.assertEqual(self.enviados, [sa.TEXTO["sin_conexion"]])
        self.pasa_caido(300)
        self.assertEqual(len(self.enviados), 1)

    def test_si_el_mac_no_tiene_red_no_avisa(self):
        self.arranca()
        self.hay_red = False
        self.pasa_caido(1200)
        self.pasa_caido(300)
        self.assertEqual(self.enviados, [])

    def test_error_de_configuracion_no_es_una_caida(self):
        self.arranca()
        self.pasa_caido(1200, "falta la clave ~/.ssh/infobar_ed25519")
        self.pasa_caido(200, "falta la clave ~/.ssh/infobar_ed25519")
        self.assertEqual(self.enviados, [])

    def test_lo_que_ya_estaba_caido_al_arrancar_no_avisa(self):
        self.t += 3000
        self.pasa(0, cache={"servidores": {NOMBRE: {"ok_en": T0, "intento_en": int(self.t), "error": "timeout"}}})
        self.assertEqual(self.enviados, [])
        self.pasa_caido(300)
        self.assertEqual(self.enviados, [])

    def test_servidor_que_nunca_conecto_no_avisa(self):
        self.arranca()
        self.pasa(1200, cache={"servidores": {NOMBRE: {"ok_en": 0, "intento_en": int(self.t), "error": "timeout"}}})
        self.assertEqual(self.enviados, [])

    def test_un_estado_guardado_antes_de_apagar_no_confirma_nada_al_arrancar(self):
        self.arranca()
        self.pasa_caido(60)                                 # empieza a contar la caída
        nuevo = sa.Vigilante(reloj=lambda: self.t, mono=lambda: self.t + self.salto, canal=self.canal, sin_red=lambda: not self.hay_red, canal_real=True)
        self.t += 7200                                      # la app estuvo cerrada; al abrir, el servidor sigue sin responder
        nuevo.vigilar(self.conf, cache={"servidores": {NOMBRE: {"ok_en": T0, "intento_en": int(self.t), "error": "timeout"}}})
        self.assertEqual(self.enviados, [])

    def test_acceso_rechazado_avisa_con_texto_propio(self):
        self.arranca()
        c = {"servidores": {NOMBRE: {"ok_en": T0, "intento_en": int(self.t), "error": "clave de host no confiada"}}}
        self.pasa(60, cache=c)
        self.assertEqual(self.enviados, [])                # primero se confirma
        self.pasa(130, cache=c)
        self.assertEqual(self.enviados, [sa.TEXTO["acceso_rechazado"]])


class SilencioYEntregaTest(Base):
    def test_apagado_global_anota_y_al_reactivar_no_cae_lo_acumulado(self):
        self.arranca(cache=cache(disco=50))
        self.conf["avisos_servidores"] = False
        self.pasa(60, cache=cache(disco=95, ok=int(self.t) + 60))
        self.pasa(900, disco=95)
        self.assertEqual(self.enviados, [])
        del self.conf["avisos_servidores"]
        self.pasa(300, disco=95)
        self.assertEqual(self.enviados, [])

    def test_servidor_silenciado_y_reactivado(self):
        self.servidores[0]["avisos"] = False
        self.arranca(cache=cache(disco=50))
        self.pasa(60, disco=95)
        self.pasa(900, disco=95)
        self.assertEqual(self.enviados, [])
        del self.servidores[0]["avisos"]
        self.pasa(300, disco=95)
        self.assertEqual(self.enviados, [])

    def test_los_criticos_tienen_mas_tope_que_los_demas(self):
        self.arranca(cache=cache(disco=50))
        e = self.estado()
        e["envios"] = [int(self.t) - 10] * sa.TOPE_NO_CRITICAS_HORA
        with open(sa.ruta(), "w", encoding="utf-8") as f:
            json.dump(e, f)
        c = {"servidores": {NOMBRE: {"ok_en": T0, "intento_en": int(self.t), "error": "clave de host no confiada"}}}
        self.pasa(60, cache=c)
        self.pasa(130, cache=c)
        self.assertEqual(self.enviados, [sa.TEXTO["acceso_rechazado"]])
        e = self.estado()
        e["envios"] = [int(self.t) - 10] * sa.TOPE_HORA
        with open(sa.ruta(), "w", encoding="utf-8") as f:
            json.dump(e, f)
        self.assertEqual(len(self.estado()["envios"]), sa.TOPE_HORA)

    def test_si_la_notificacion_falla_se_reintenta_y_luego_se_rinde(self):
        self.arranca(cache=cache(disco=50))
        self.entrega = False
        self.pasa(60, disco=95)
        self.pasa(600, disco=95)
        self.assertEqual(len(self.enviados), 1)
        self.pasa(60, disco=95)
        self.assertEqual(len(self.enviados), 1)            # aún en espera del reintento
        for _ in range(8):
            self.pasa(sa.REINTENTO_S, disco=95)
        self.assertEqual(len(self.enviados), sa.INTENTOS)  # se rinde y no insiste más

    def test_si_falla_y_luego_entrega_queda_avisado(self):
        self.arranca(cache=cache(disco=50))
        self.entrega = False
        self.pasa(60, disco=95)
        self.pasa(600, disco=95)
        self.entrega = True
        self.pasa(sa.REINTENTO_S, disco=95)
        self.pasa(sa.REINTENTO_S, disco=95)
        self.assertEqual(len(self.enviados), 2)

    def test_sin_notificaciones_del_sistema_se_da_por_avisado(self):
        self.v.canal_real = False
        self.arranca(cache=cache(disco=50))
        self.pasa(60, disco=95)
        self.pasa(600, disco=95)
        self.assertEqual(self.enviados, [])
        self.assertTrue(any(e["avisado"] for e in self.estado()["entradas"].values()))

    def test_varios_avisos_van_en_una_sola_notificacion_sin_nombres(self):
        self.arranca(cache=cache(disco=50))
        self.pasa(60, disco=95)
        self.t += 600
        con = sh.abrir()
        try:
            sh._alta_servidor(con, ID, NOMBRE, T0)
            con.execute("INSERT INTO eventos(servidor_id, ts, clave, tipo, evento, severidad) VALUES(?,?,?,?,?,?)",
                        (ID, int(self.t) - 10, "k" * 32, "contenedor", "reinicios_en_bucle", "critica"))
        finally:
            con.close()
        self.pasa(0, disco=95)
        self.assertEqual(len(self.enviados), 1)
        texto = self.enviados[0]
        self.assertIn("2 avisos de servidores", texto)
        for secreto in (NOMBRE, "dominio-secreto", "example.com", "1.2.3.4"):
            self.assertNotIn(secreto, texto)

    def test_tope_por_hora(self):
        self.arranca(cache=cache(disco=50))
        self.pasa(0, disco=50)
        e = self.estado()
        e["envios"] = [int(self.t) - 10] * sa.TOPE_NO_CRITICAS_HORA
        with open(sa.ruta(), "w", encoding="utf-8") as f:
            json.dump(e, f)
        self.pasa(60, disco=95)
        self.pasa(700, disco=95)
        self.assertEqual(self.enviados, [])                # la no crítica espera al hueco
        self.t += 3600
        self.pasa(0, disco=95)
        self.assertEqual(len(self.enviados), 1)


class RelojYFicheroTest(Base):
    def test_al_despertar_el_ciclo_no_evalua(self):
        self.arranca(cache=cache(disco=50))
        self.pasa(60, disco=95)
        self.salto = -4000                                  # el reloj monotónico no avanzó lo que el de pared: el Mac durmió
        self.pasa(700, disco=95)
        self.assertEqual(self.enviados, [])
        self.pasa(60, disco=95)
        self.assertEqual(len(self.enviados), 0)             # la confirmación empieza de nuevo tras despertar
        self.pasa(600, disco=95)
        self.assertEqual(len(self.enviados), 1)

    def test_marcas_futuras_se_recortan(self):
        self.arranca(cache=cache(disco=50))
        e = self.estado()
        clave = f"disco_lleno|{ID}"
        e["entradas"][clave].update(ultimo=T0 + 10 ** 7)          # un reloj adelantado dejó un enfriamiento de 115 días
        with open(sa.ruta(), "w", encoding="utf-8") as f:
            json.dump(e, f)
        self.pasa(60, disco=95)
        self.t += 12 * 3600
        self.pasa(0, disco=95)
        self.pasa(700, disco=95)
        self.assertEqual(len(self.enviados), 1)

    def test_fichero_roto_se_aparta_y_arranca_en_silencio(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(sa.ruta(), "w") as f:
            f.write("{no es json")
        self.pasa(cache=cache(disco=95))
        self.assertEqual(self.enviados, [])
        self.assertTrue(os.path.exists(sa.ruta() + ".corrupto"))
        self.assertTrue(self.estado()["base"])

    def test_fichero_enorme_se_aparta_y_arranca_en_silencio(self):
        with open(sa.ruta(), "w") as f:
            f.write(" " * (sa.MAX_BYTES + 1))
        self.pasa(disco=95)
        self.assertEqual(self.enviados, [])
        self.assertTrue(os.path.exists(sa.ruta() + ".corrupto"))
        self.assertTrue(self.estado()["base"])

    def test_json_muy_anidado_no_desactiva_los_avisos(self):
        with open(sa.ruta(), "w") as f:
            f.write("[" * 200000)
        self.pasa(disco=50)
        self.assertTrue(os.path.exists(sa.ruta() + ".corrupto"))
        self.assertTrue(self.estado()["base"])

    @unittest.skipIf(os.name == "nt", "os.mkfifo no existe en Windows")
    def test_un_fifo_en_la_ruta_no_cuelga_nada(self):
        os.mkfifo(sa.ruta())
        self.pasa(disco=50)
        self.assertTrue(os.path.exists(sa.ruta() + ".corrupto"))
        self.assertTrue(self.estado()["base"])

    @unittest.skipIf(os.name == "nt", "os.symlink necesita Modo Desarrollador o admin en Windows, "
                     "no se puede dar por hecho en la maquina de un usuario")
    def test_un_enlace_simbolico_no_se_sigue(self):
        destino = os.path.join(self.dir, "otro.json")
        with open(destino, "w") as f:
            json.dump({"base": True, "envios": [], "entradas": {}}, f)
        os.symlink(destino, sa.ruta())
        self.pasa(disco=50)
        self.assertTrue(os.path.exists(sa.ruta() + ".corrupto"))

    def test_entradas_hostiles_se_descartan_una_a_una(self):
        hostil = {"base": True, "envios": ["x", -1, None, True], "entradas": {
            "web_caida|" + "z" * 32: {"desde": 1}, "../../etc": {"desde": 1}, f"disco_lleno|{ID}": {"desde": "no", "avisado": float("nan"), "intentos": True},
            f"sin_conexion|{ID}": "no es un dict"}}
        with open(sa.ruta(), "w") as f:
            json.dump(hostil, f)
        estado, existia = sa.leer(T0)
        self.assertTrue(existia)
        self.assertEqual(list(estado["entradas"]), [f"disco_lleno|{ID}"])
        self.assertEqual(estado["envios"], [])
        self.assertIsNone(estado["entradas"][f"disco_lleno|{ID}"]["desde"])
        self.assertEqual(estado["entradas"][f"disco_lleno|{ID}"]["intentos"], 0)

    def test_sin_servidores_legibles_no_se_poda_ni_se_avisa(self):
        self.arranca(cache=cache(disco=50))
        antes = self.estado()
        self.conf["servidores"] = []
        self.t += sa.GRACIA_PODA_S + 10
        self.v.vigilar(self.conf, cache={})
        self.assertEqual(self.estado(), antes)

    def test_poda_con_gracia(self):
        self.arranca(cache=cache(disco=50))
        self.conf["servidores"] = [{"id": ID2, "nombre": "otro", "ssh": "infobar@b.example.com"}]
        self.pasa(3600, cache=cache(nombre="otro"))
        self.assertIn(f"disco_lleno|{ID}", self.estado()["entradas"])
        self.pasa(sa.GRACIA_PODA_S + 10, cache=cache(nombre="otro"))
        self.assertNotIn(f"disco_lleno|{ID}", self.estado()["entradas"])

    def test_el_estado_no_lleva_nombres_ni_urls(self):
        self.arranca(cache=cache(disco=50))
        with open(sa.ruta(), encoding="utf-8") as f:
            crudo = f.read()
        self.assertNotIn(NOMBRE, crudo)
        self.assertNotIn("example.com", crudo)

    def test_un_fallo_interno_no_propaga_y_no_loguea_valores(self):
        with mock.patch.object(sa, "_desde_cache", side_effect=KeyError(URL)), mock.patch.object(sa, "log") as lg:
            self.assertEqual(self.v.vigilar(self.conf, cache=cache()), [])
        self.assertEqual(lg.call_count, 1)
        self.assertNotIn("dominio-secreto", str(lg.call_args))


class WebsYBaseTest(Base):
    def web(self, r, ts):
        con = sh.abrir()
        try:
            sh._alta_servidor(con, ID, NOMBRE, T0)
            con.execute("BEGIN IMMEDIATE")
            sg.anota_web(con, ID, URL, r, ts)
            con.execute("COMMIT")
        finally:
            con.close()

    def test_web_caida_avisa_tras_cinco_minutos_con_texto_neutro(self):
        self.web({"codigo": 200, "ms": 5, "cert_dias": 90}, int(self.t))
        self.arranca()
        self.web({"codigo": 502}, int(self.t) + 60)
        self.web({"codigo": 502}, int(self.t) + 120)
        self.pasa(120)
        self.assertEqual(self.enviados, [])
        self.web({"codigo": 502}, int(self.t) + 200)
        self.pasa(310)
        self.assertEqual(self.enviados, [sa.TEXTO["web_caida"]])
        self.assertNotIn("caída", self.enviados[0])
        self.assertNotIn("dominio-secreto", self.enviados[0])

    def test_web_que_vuelve_no_avisa(self):
        self.web({"codigo": 200, "ms": 5}, int(self.t))
        self.arranca()
        self.web({"codigo": 502}, int(self.t) + 60)
        self.web({"codigo": 502}, int(self.t) + 120)
        self.pasa(130)
        self.web({"codigo": 200, "ms": 5}, int(self.t) + 10)
        self.pasa(200)
        self.assertEqual(self.enviados, [])

    def test_medida_vieja_de_una_web_no_cuenta(self):
        self.web({"codigo": 200, "ms": 5}, int(self.t))
        self.arranca()
        self.web({"codigo": 502}, int(self.t) + 60)
        self.web({"codigo": 502}, int(self.t) + 120)
        self.pasa(2000)                                    # el sondeador lleva mucho sin medir: no se sabe
        self.pasa(2000)
        self.assertEqual(self.enviados, [])

    def test_certificado_a_punto_de_caducar(self):
        self.web({"codigo": 200, "ms": 5, "cert_dias": 60}, int(self.t))
        self.arranca()
        self.web({"codigo": 200, "ms": 5, "cert_dias": 5}, int(self.t) + 30)
        self.pasa(40)
        self.web({"codigo": 200, "ms": 5, "cert_dias": 5}, int(self.t) + 700)
        self.pasa(700)
        self.assertEqual(self.enviados, [sa.TEXTO["certificado"]])

    def test_certificado_entre_siete_y_catorce_dias_no_cambia_nada(self):
        self.web({"codigo": 200, "ms": 5, "cert_dias": 60}, int(self.t))
        self.arranca()
        for _ in range(4):
            self.web({"codigo": 200, "ms": 5, "cert_dias": 10}, int(self.t) + 300)
            self.pasa(300)
        self.assertEqual(self.enviados, [])

    def test_bucle_de_contenedor_avisa_y_un_evento_viejo_no(self):
        con = sh.abrir()
        try:
            sh._alta_servidor(con, ID, NOMBRE, T0)
            con.execute("INSERT INTO eventos(servidor_id, ts, clave, tipo, evento, severidad) VALUES(?,?,?,?,?,?)",
                        (ID, int(self.t) - 3 * 3600, "k" * 32, "contenedor", "reinicios_en_bucle", "critica"))
        finally:
            con.close()
        self.arranca()
        self.pasa(60)
        self.assertEqual(self.enviados, [])
        con = sh.abrir()
        try:
            con.execute("INSERT INTO eventos(servidor_id, ts, clave, tipo, evento, severidad) VALUES(?,?,?,?,?,?)",
                        (ID, int(self.t), "j" * 32, "contenedor", "reinicios_en_bucle", "critica"))
        finally:
            con.close()
        self.pasa(60)
        self.assertEqual(self.enviados, [sa.TEXTO["bucle"]])

    def evento_bucle(self):
        con = sh.abrir()
        try:
            sh._alta_servidor(con, ID, NOMBRE, T0)
            con.execute("INSERT OR IGNORE INTO eventos(servidor_id, ts, clave, tipo, evento, severidad) VALUES(?,?,?,?,?,?)",
                        (ID, int(self.t), "m" * 32, "contenedor", "reinicios_en_bucle", "critica"))
        finally:
            con.close()

    def test_un_bucle_ya_presente_al_arrancar_no_avisa(self):
        self.evento_bucle()
        self.arranca()
        self.pasa(60)
        self.assertEqual(self.enviados, [])

    def test_al_despertar_ese_ciclo_no_avisa_de_un_bucle(self):
        self.arranca()
        self.pasa(60)
        self.salto = -4000
        self.evento_bucle()
        self.pasa(60)
        self.assertEqual(self.enviados, [])
        self.pasa(60)
        self.assertEqual(self.enviados, [sa.TEXTO["bucle"]])

    def test_base_ilegible_es_no_se_sabe_y_la_conexion_sigue_funcionando(self):
        self.arranca()
        with mock.patch.object(sh, "abrir", side_effect=sh.ErrorAlmacen("x")):
            c = {"servidores": {NOMBRE: {"ok_en": T0, "intento_en": int(self.t) + 1500, "error": "timeout"}}}
            self.pasa(1500, cache=c)
            c = {"servidores": {NOMBRE: {"ok_en": T0, "intento_en": int(self.t) + 130, "error": "timeout"}}}
            self.pasa(130, cache=c)
        self.assertEqual(self.enviados, [sa.TEXTO["sin_conexion"]])


class ConfiguracionTest(unittest.TestCase):
    def test_campo_avisos_del_servidor(self):
        base = {"nombre": "a", "ssh": "infobar@a.example.com"}
        self.assertEqual(servidores.errores_servidores([{**base, "avisos": False}]), [])
        self.assertTrue(servidores.errores_servidores([{**base, "avisos": "no"}]))
        self.assertEqual(len(servidores.servidores_validos([{**base, "avisos": False}])), 1)

    def test_avisos_servidores_global(self):
        base = {"proveedores": [], "aliases": {}}
        self.assertIs(ajustes.normalizar({**base, "avisos_servidores": False}, {})["avisos_servidores"], False)
        with self.assertRaises(ValueError):
            ajustes.normalizar({**base, "avisos_servidores": "si"}, {})


if __name__ == "__main__":
    unittest.main()
