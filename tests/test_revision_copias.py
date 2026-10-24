"""Hallazgos de la revisión de las copias: restaurar en un equipo nuevo, sobre una base dañada, con la barra viva; rotación por
dispositivo, `.danada` acotadas, reloj atrasado, carpeta sin montar; y las protecciones que antes ningún test vigilaba."""
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, cli_copias, exportacion, hilo_copias, ingestor, plataforma, restauracion, rutas  # noqa: E402
from infobar.config_y_estado import cfg, guardar_config  # noqa: E402
from test_cli_copias import Falso, llena_base  # noqa: E402
from test_ingesta import linea  # noqa: E402

try:
    import cryptography  # noqa: F401
    from infobar import claves_copias, cgx
    HAY = True
except ImportError:
    HAY = False

DIA = 86400
T0 = 1_790_000_000


@unittest.skipUnless(HAY, "falta cryptography")
class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-rev-")
        env = mock.patch.dict(os.environ, {"COSTGUARD_DATOS": self.dir})
        env.start()
        self.addCleanup(env.stop)
        self.ant = (rutas.DATOS, rutas.ALMACEN, rutas.COPIAS)
        rutas.DATOS = os.path.join(self.dir, "datos")
        rutas.ALMACEN = os.path.join(rutas.DATOS, "consumo.sqlite")
        rutas.COPIAS = os.path.join(rutas.DATOS, "copias")
        llena_base(rutas.ALMACEN)
        self.carpeta = os.path.join(self.dir, "cifradas")
        os.makedirs(self.carpeta)
        self.fuera = os.path.join(self.dir, "extraible")
        os.makedirs(self.fuera)
        self.config_ant = cfg().get("copias")
        guardar_config({"copias": {"carpeta": self.carpeta, "destinatario": [], "firmas_publicas": []}})
        self.priv, self.pub = cgx.generar_par()
        self.fpriv, self.fpub = cgx.generar_par_firma()

    def tearDown(self):
        rutas.DATOS, rutas.ALMACEN, rutas.COPIAS = self.ant
        guardar_config({"copias": self.config_ant or {}})
        shutil.rmtree(self.dir, ignore_errors=True)

    def copia(self, ahora, con=None, carpeta=None, fpriv=None, fpub=None):
        c = con or almacen.abrir(rutas.ALMACEN, solo_lectura=True)
        try:
            return exportacion.hacer_copia(c, carpeta or self.carpeta, self.pub, fpriv or self.fpriv, fpub or self.fpub, ahora=ahora)
        finally:
            if con is None:
                c.close()


class TestEquipoNuevo(Base):
    def test_restaurar_en_otro_equipo_con_solo_la_clave_privada(self):
        clave = os.path.join(self.fuera, "clave.txt")
        self.assertEqual(cli_copias.main(["--claves-generar", clave], Falso(textos=["SI"])), 0)
        self.assertEqual(cli_copias.main(["--copia-ahora"], Falso()), 0)
        copia = os.path.join(self.carpeta, [f for f in os.listdir(self.carpeta) if f.endswith(".cgx")][0])
        # equipo nuevo: otros datos, otra clave de firma, configuración vacía
        rutas.DATOS = os.path.join(self.dir, "datos-nuevo")
        rutas.ALMACEN = os.path.join(rutas.DATOS, "consumo.sqlite")
        rutas.COPIAS = os.path.join(rutas.DATOS, "copias")
        guardar_config({"copias": {}})
        t = Falso(textos=["SI"])
        self.assertEqual(cli_copias.main(["--importar", copia, "--clave-privada", clave], t), 0, t.todo)
        self.assertIn("30 turnos", t.todo)

    def test_firma_pasada_a_mano_si_la_clave_no_la_lleva(self):
        clave = os.path.join(self.fuera, "clave.txt")
        cli_copias.main(["--claves-generar", clave], Falso(textos=["SI"]))
        firma = cfg()["copias"]["firmas_publicas"][0]
        cli_copias.main(["--copia-ahora"], Falso())
        copia = os.path.join(self.carpeta, [f for f in os.listdir(self.carpeta) if f.endswith(".cgx")][0])
        priv, _firmas, _a = claves_copias.cargar_paquete(clave)
        sin_firma = os.path.join(self.fuera, "sola.txt")
        with open(sin_firma, "w") as f:
            f.write(cgx.codificar_clave("privada", priv) + "\n")
        os.chmod(sin_firma, 0o600)
        rutas.DATOS = os.path.join(self.dir, "datos-nuevo")
        rutas.ALMACEN = os.path.join(rutas.DATOS, "consumo.sqlite")
        rutas.COPIAS = os.path.join(rutas.DATOS, "copias")
        guardar_config({"copias": {}})
        t = Falso(textos=["SI"])
        self.assertEqual(cli_copias.main(["--importar", copia, "--clave-privada", sin_firma], t), 1)
        self.assertIn("--firma-publica", t.todo)                                    # el mensaje dice qué hacer
        t2 = Falso(textos=["SI"])
        self.assertEqual(cli_copias.main(["--importar", copia, "--clave-privada", sin_firma, "--firma-publica", firma], t2), 0, t2.todo)

    def test_la_privada_lleva_la_firma_publica(self):
        clave = os.path.join(self.fuera, "clave.txt")
        r = claves_copias.generar(clave, self.carpeta)
        _p, firmas, _a = claves_copias.cargar_paquete(clave)
        self.assertEqual([cgx.codificar_clave("firma_publica", f) for f in firmas], [r["firma_publica"]])


class TestBaseDanadaYBarra(Base):
    def descifrar(self, e, s):
        cgx.descifrar_con_privada(e, s, self.priv, [self.fpub])

    def hace_copia(self):
        r = self.copia(T0)
        return os.path.join(self.carpeta, r["archivo"])

    def test_restaura_sobre_una_base_ilegible_apartandola(self):
        ruta = self.hace_copia()
        with open(rutas.ALMACEN, "wb") as f:
            f.write(os.urandom(8192))
        r = restauracion.restaurar(ruta, self.descifrar, lambda i: True, destino=rutas.ALMACEN, forzar=True)
        self.assertTrue(r["apartada"])
        self.assertTrue(os.path.exists(r["copia_previa"]))
        self.assertEqual(sqlite3.connect(rutas.ALMACEN).execute("SELECT COUNT(*) FROM turnos").fetchone()[0], 30)

    def test_marcador_de_restauracion_durante_y_borrado_al_acabar_o_fallar(self):
        ruta = self.hace_copia()
        vistos = []

        def descifrar(e, s):
            vistos.append(almacen.marcador_activo())
            self.descifrar(e, s)
        restauracion.restaurar(ruta, descifrar, lambda i: True, destino=rutas.ALMACEN, forzar=True)
        self.assertEqual(vistos, [True])
        self.assertFalse(almacen.marcador_activo())
        with self.assertRaises(restauracion.ErrorRestauracion):
            restauracion.restaurar(ruta, self.descifrar, lambda i: False, destino=rutas.ALMACEN, forzar=True)
        self.assertFalse(almacen.marcador_activo())

    def test_la_ingesta_y_las_copias_respetan_el_marcador(self):
        home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(home, ".claude", "projects", "p"))
        ing = ingestor.Ingestor(home=home, ruta_base=rutas.ALMACEN)
        self.assertTrue(ing.vuelta()["escritor"])
        almacen.poner_marcador()
        try:
            self.assertFalse(ing.vuelta()["escritor"])
            con = almacen.abrir(rutas.ALMACEN, solo_lectura=True)
            self.assertIn(almacen.meta(con, "escritor"), ("", None))                 # soltó el puesto
            con.close()
            guardar_config({"copias": {"carpeta": self.carpeta, "destinatario": [cgx.codificar_clave("publica", self.pub)]}})
            self.assertEqual(hilo_copias.una_vez(T0), "restaurando")
        finally:
            almacen.quitar_marcador()
            ing.cierra()

    @unittest.skipIf(os.name == "nt", "`viva` se mantiene abierta a proposito durante TODA la llamada a restaurar() (solo se libera "
                      "el puesto de escritor por otra conexion, no el fichero): en Windows la VFS de SQLite no pide "
                      "FILE_SHARE_DELETE, asi que eso bloquea el borrado del -wal sin remedio (no es un bloqueo transitorio "
                      "reintentable)")
    def test_espera_a_que_la_barra_suelte_el_puesto(self):
        ruta = self.hace_copia()
        viva = almacen.abrir(rutas.ALMACEN)
        q = almacen.identidad("barra")
        almacen.adquirir_escritura(viva, q)
        def suelta():
            c = almacen.abrir(rutas.ALMACEN)              # cada hilo con su propia conexión
            almacen.soltar_escritura(c, q)
            c.close()
        threading.Timer(1.2, suelta).start()
        r = restauracion.restaurar(ruta, self.descifrar, lambda i: True, destino=rutas.ALMACEN, forzar=True, espera_s=10)
        self.assertEqual(r["turnos"], 30)
        viva.close()

    def test_sin_espera_se_niega_con_la_orden_de_parada(self):
        ruta = self.hace_copia()
        viva = almacen.abrir(rutas.ALMACEN)
        almacen.adquirir_escritura(viva, almacen.identidad("barra"))
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "launchctl bootout"):
            restauracion.restaurar(ruta, self.descifrar, lambda i: True, destino=rutas.ALMACEN, forzar=True)
        viva.close()

    def test_se_pierde_el_puesto_antes_de_sustituir(self):
        ruta = self.hace_copia()

        def descifrar(e, s):
            self.descifrar(e, s)
            c = almacen.abrir(rutas.ALMACEN)                 # otro proceso se hace con el puesto tras caducar el nuestro
            c.execute("UPDATE meta SET valor='0' WHERE clave='escritor_latido'")
            almacen.adquirir_escritura(c, almacen.identidad("otro"))
            c.close()
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "se perdió el puesto"):
            restauracion.restaurar(ruta, descifrar, lambda i: True, destino=rutas.ALMACEN, forzar=True)
        self.assertEqual(sqlite3.connect(rutas.ALMACEN).execute("SELECT COUNT(*) FROM turnos").fetchone()[0], 30)

    def test_el_puesto_sigue_tomado_en_el_momento_del_reemplazo(self):
        ruta = self.hace_copia()
        real = exportacion.reemplazar
        vistos = []

        def reemplaza(origen, destino, *a, **k):
            c = sqlite3.connect(rutas.ALMACEN)
            vistos.append(c.execute("SELECT valor FROM meta WHERE clave='escritor'").fetchone()[0])
            c.close()
            return real(origen, destino, *a, **k)
        with mock.patch.object(exportacion, "reemplazar", reemplaza):
            restauracion.restaurar(ruta, self.descifrar, lambda i: True, destino=rutas.ALMACEN, forzar=True)
        self.assertTrue(vistos[0].startswith("restauracion:"), vistos)

    def test_reemplazo_que_falla_da_un_mensaje_y_no_una_traza(self):
        ruta = self.hace_copia()
        with mock.patch.object(exportacion, "reemplazar", side_effect=PermissionError("en uso")):
            with self.assertRaisesRegex(restauracion.ErrorRestauracion, "a salvo"):
                restauracion.restaurar(ruta, self.descifrar, lambda i: True, destino=rutas.ALMACEN, forzar=True)

    def test_wal_que_no_se_borra_tras_reemplazar_avisa_que_ya_se_restauro(self):
        ruta = self.hace_copia()
        llamadas = []
        real = restauracion._borra_wal

        def borra(destino, tras_reemplazar=False):
            llamadas.append(tras_reemplazar)
            if tras_reemplazar:
                raise restauracion.ErrorRestauracion("la base YA se restauró, pero no se pudo borrar x-wal")
            return real(destino, tras_reemplazar)
        with mock.patch.object(restauracion, "_borra_wal", borra):
            with self.assertRaisesRegex(restauracion.ErrorRestauracion, "YA se restauró"):
                restauracion.restaurar(ruta, self.descifrar, lambda i: True, destino=rutas.ALMACEN, forzar=True)
        self.assertEqual(llamadas, [False, True])

    def test_el_checkout_de_desarrollo_no_pisa_los_datos_reales(self):
        ruta = self.hace_copia()
        with mock.patch.object(plataforma, "dir_datos", return_value=rutas.DATOS), \
                mock.patch.object(plataforma, "es_checkout_de_desarrollo", return_value=True):
            with self.assertRaisesRegex(restauracion.ErrorRestauracion, "COSTGUARD_DATOS"):
                restauracion.restaurar(ruta, self.descifrar, lambda i: True, forzar=True)

    def test_no_hay_sitio_para_descifrar(self):
        ruta = self.hace_copia()
        with mock.patch.object(restauracion.shutil, "disk_usage", return_value=mock.Mock(free=1000)):
            with self.assertRaisesRegex(restauracion.ErrorRestauracion, "espacio"):
                restauracion.restaurar(ruta, self.descifrar, lambda i: True, destino=os.path.join(self.dir, "x", "c.sqlite"))
            with self.assertRaisesRegex(restauracion.ErrorRestauracion, "espacio"):
                restauracion.probar(ruta, self.descifrar)


class TestRotacion(Base):
    def test_danada_como_mucho_una_al_dia_y_las_3_ultimas(self):
        real = exportacion.copiar_base
        con = almacen.abrir(rutas.ALMACEN, solo_lectura=True)

        def danada(c):
            ruta, _e = real(c)
            return ruta, "fallo de prueba"
        guardar_config({"copias": {"carpeta": self.carpeta, "destinatario": [cgx.codificar_clave("publica", self.pub)]}})
        try:
            with mock.patch.object(exportacion, "copiar_base", danada), mock.patch.object(claves_copias, "clave_de_firma",
                                                                                          return_value=(self.fpriv, self.fpub)):
                self.assertIsInstance(hilo_copias.una_vez(T0), dict)
                for minutos in range(1, 10):                                   # 9 revisiones cada 15 min: ninguna copia más
                    self.assertEqual(hilo_copias.una_vez(T0 + minutos * 900), "no_toca")
        finally:
            con.close()
        self.assertEqual(len([f for f in os.listdir(self.carpeta) if f.endswith(".danada.cgx")]), 1)

    def test_cinco_danadas_quedan_tres(self):
        real = exportacion.copiar_base
        con = almacen.abrir(rutas.ALMACEN, solo_lectura=True)

        def danada(c):
            ruta, _e = real(c)
            return ruta, "fallo de prueba"
        try:
            with mock.patch.object(exportacion, "copiar_base", danada):
                for dia in range(5):
                    self.copia(T0 + dia * DIA, con=con)
        finally:
            con.close()
        self.assertEqual(len([f for f in os.listdir(self.carpeta) if f.endswith(".danada.cgx")]), exportacion.DANADAS)

    def test_dos_equipos_en_la_misma_carpeta_no_se_borran_entre_si(self):
        fpriv_b, fpub_b = cgx.generar_par_firma()
        con = almacen.abrir(rutas.ALMACEN, solo_lectura=True)
        try:
            for dia in range(30):
                self.copia(T0 + dia * DIA, con=con)
                self.copia(T0 + dia * DIA + 3600, con=con, fpriv=fpriv_b, fpub=fpub_b)
        finally:
            con.close()
        a, b = exportacion.dispositivo_id(self.fpub), exportacion.dispositivo_id(fpub_b)
        nombres = os.listdir(self.carpeta)
        self.assertGreaterEqual(len([n for n in nombres if n.startswith(f"copia-{a}")]), 14)
        self.assertGreaterEqual(len([n for n in nombres if n.startswith(f"copia-{b}")]), 14)

    def test_reloj_atrasado_no_borra_la_recien_escrita(self):
        con = almacen.abrir(rutas.ALMACEN, solo_lectura=True)
        try:
            for dia in range(20):
                self.copia(T0 + dia * DIA, con=con)
            r = self.copia(T0 - 400 * DIA, con=con)                          # el reloj vuelve atrás
        finally:
            con.close()
        self.assertTrue(r["reloj_atrasado"])
        self.assertEqual(r["rotadas"], 0)
        self.assertTrue(os.path.exists(os.path.join(self.carpeta, r["archivo"])))
        self.assertGreater(r["bytes"], 0)

    def test_la_copia_recien_escrita_nunca_se_rota_aunque_el_reloj_vaya_unas_horas_atrasado(self):
        con = almacen.abrir(rutas.ALMACEN, solo_lectura=True)
        try:
            medianoche = T0 - T0 % DIA
            for dia in range(3):
                self.copia(medianoche + dia * DIA + 12 * 3600, con=con)
            r = self.copia(medianoche + 2 * DIA + 3600, con=con)          # el mismo día que la más reciente pero más temprano: no es «la última del día»
        finally:
            con.close()
        self.assertFalse(r["reloj_atrasado"])
        self.assertTrue(os.path.exists(os.path.join(self.carpeta, r["archivo"])))

    def test_carpeta_sin_montar_no_se_crea_en_silencio(self):
        ausente = os.path.join(self.dir, "Volumes-nube", "copias")
        with self.assertRaisesRegex(cgx.ErrorCgx, "no está disponible"):
            self.copia(T0, carpeta=ausente)
        self.assertFalse(os.path.exists(ausente))

    def test_part_huerfano_antiguo_se_limpia_y_el_reciente_no(self):
        viejo, nuevo = os.path.join(self.carpeta, ".copia-viejo.part"), os.path.join(self.carpeta, ".copia-nuevo.part")
        for f in (viejo, nuevo):
            open(f, "w").close()
        os.utime(viejo, (T0 - 3 * DIA, T0 - 3 * DIA))
        os.utime(nuevo, (T0, T0))
        self.copia(T0 + 60)
        self.assertFalse(os.path.exists(viejo))
        self.assertTrue(os.path.exists(nuevo))

    def test_barrido_no_borra_el_temporal_de_otro_proceso_en_marcha(self):
        d = exportacion.dir_tmp()
        reciente, antiguo = os.path.join(d, "reciente.tmp"), os.path.join(d, "antiguo.tmp")
        for f in (reciente, antiguo):
            open(f, "w").close()
        os.utime(antiguo, (T0 - 20 * 3600, T0 - 20 * 3600))
        os.utime(reciente, (T0 - 60, T0 - 60))
        exportacion.barrer_tmp(ahora=T0)
        self.assertTrue(os.path.exists(reciente))
        self.assertFalse(os.path.exists(antiguo))


class TestEstado(Base):
    def test_reloj_adelantado_y_ensayo_y_error_y_local(self):
        cc = {"destinatario": ["x"], "carpeta": os.path.join(self.dir, "otra")}
        exportacion.registrar_copia({"archivo": "a", "ts": T0 + 365 * DIA, "danada": False, "huella": "h", "bytes": 1})
        self.assertEqual(exportacion.estado_copias(cc, T0)["estado"], "reloj")
        self.assertTrue(exportacion.toca_copia(T0))
        exportacion.registrar_copia({"archivo": "a", "ts": T0, "danada": False, "huella": "h", "bytes": 1})
        e = exportacion.estado_copias(cc, T0 + 3600)
        self.assertEqual((e["estado"], e["ensayo_atrasado"], e["local"]), ("al_dia", True, False))
        exportacion.registrar_ensayo(T0)
        self.assertFalse(exportacion.estado_copias(cc, T0 + 3600)["ensayo_atrasado"])
        self.assertTrue(exportacion.estado_copias(cc, T0 + 91 * DIA)["ensayo_atrasado"])
        exportacion.registrar_error("disco no montado", T0 + 10)
        self.assertEqual(exportacion.estado_copias(cc, T0 + 3600)["ultimo_error"]["mensaje"], "disco no montado")
        self.assertTrue(exportacion.estado_copias({"destinatario": ["x"], "carpeta": exportacion.carpeta_por_defecto()}, T0)["local"])

    def test_el_hilo_anota_su_error_y_acepta_destinatario_como_texto(self):
        guardar_config({"copias": {"carpeta": self.carpeta, "destinatario": cgx.codificar_clave("publica", self.pub)}})
        with mock.patch.object(claves_copias, "clave_de_firma", return_value=(self.fpriv, self.fpub)):
            r = hilo_copias.una_vez(T0)
        self.assertIsInstance(r, dict)

    def test_el_hilo_abre_la_base_en_solo_lectura(self):
        guardar_config({"copias": {"carpeta": self.carpeta, "destinatario": [cgx.codificar_clave("publica", self.pub)]}})
        real = almacen.abrir
        usos = []

        def abrir(*a, **k):
            usos.append(k.get("solo_lectura"))
            return real(*a, **k)
        with mock.patch.object(almacen, "abrir", abrir), mock.patch.object(claves_copias, "clave_de_firma", return_value=(self.fpriv, self.fpub)):
            hilo_copias.una_vez(T0)
        self.assertEqual(usos, [True])

    def test_una_copia_en_curso_no_bloquea_la_ingesta(self):
        guardar_config({"copias": {"carpeta": self.carpeta, "destinatario": [cgx.codificar_clave("publica", self.pub)]}})
        home = os.path.join(self.dir, "home")
        proy = os.path.join(home, ".claude", "projects", "p")
        os.makedirs(proy)
        ing = ingestor.Ingestor(home=home, ruta_base=rutas.ALMACEN)
        ing.vuelta()
        entro, liberar = threading.Event(), threading.Event()
        real = exportacion.copiar_base

        def bloqueada(con):
            import tempfile
            fd, ruta = tempfile.mkstemp(prefix="base-", suffix=".tmp", dir=exportacion.dir_tmp())
            os.close(fd)
            destino = sqlite3.connect(ruta)

            def progreso(_s, _r, _t):
                entro.set()
                liberar.wait(10)
            con.backup(destino, pages=1, progress=progreso)             # la copia real, parada a mitad con la lectura abierta
            destino.execute("PRAGMA journal_mode=DELETE")
            destino.close()
            return ruta, "ok"
        errores = []

        def lanza():
            try:
                with mock.patch.object(claves_copias, "clave_de_firma", return_value=(self.fpriv, self.fpub)):
                    hilo_copias.una_vez(T0)
            except Exception as e:                   # noqa: BLE001
                errores.append(e)
        with mock.patch.object(exportacion, "copiar_base", bloqueada):
            h = threading.Thread(target=lanza)
            h.start()
            self.assertTrue(entro.wait(10))
            with open(os.path.join(proy, "a.jsonl"), "w", encoding="utf-8") as f:
                f.write(linea("msg_01ZZZZZZZZZZZZZZZZ") + "\n")
            r = ing.vuelta()                                                   # la copia está parada a mitad: la ingesta sigue
            self.assertEqual((r["escritor"], r["nuevos"]), (True, 1))
            liberar.set()
            h.join(30)
        self.assertEqual(errores, [])
        ing.cierra()


class TestProtecciones(Base):
    def test_si_el_ensayo_falla_no_queda_clave_ni_configuracion(self):
        clave = os.path.join(self.fuera, "clave.txt")
        with mock.patch.object(claves_copias, "ensayo", side_effect=cgx.ErrorCgx("falló")):
            self.assertEqual(cli_copias.main(["--claves-generar", clave], Falso(textos=["SI"])), 1)
        self.assertFalse(os.path.exists(clave))
        self.assertEqual(cfg()["copias"]["destinatario"], [])

    def test_generar_ensaya_siempre(self):
        llamadas = []
        real = claves_copias.ensayo
        with mock.patch.object(claves_copias, "ensayo", lambda *a, **k: (llamadas.append(1), real(*a, **k))[1]):
            claves_copias.generar(os.path.join(self.fuera, "k.txt"), self.carpeta)
        self.assertEqual(llamadas, [1])

    def test_exportar_relee_y_si_no_cuadra_no_deja_fichero(self):
        destino = os.path.join(self.dir, "e.cgx")
        with mock.patch.object(cgx, "verificar_con_clave", side_effect=cgx.ErrorCgx("no coincide")):
            self.assertEqual(cli_copias.main(["--exportar", destino], Falso(frases=["frase larga de prueba 1", "frase larga de prueba 1"])), 1)
        self.assertFalse(os.path.exists(destino))
        self.assertEqual([f for f in os.listdir(self.dir) if f.startswith(".export")], [])

    def test_clave_por_argumento_con_fichero_existente(self):
        existente = os.path.join(self.dir, "x.cgx")
        open(existente, "wb").close()
        t = Falso()
        with mock.patch.object(cli_copias, "_sin_secretos_en_argv", wraps=cli_copias._sin_secretos_en_argv) as guarda:
            self.assertEqual(cli_copias.main(["--importar", existente, "CGX-SECRET-KEY-1-abc"], t), 1)
        guarda.assert_called_once()
        self.assertIn("línea de órdenes", t.todo)

    def test_cli_sin_trazas(self):
        for argv in (["--importar", "/no/existe.cgx"], ["--probar-restauracion", self.dir], ["--importar", self.dir]):
            t = Falso()
            self.assertEqual(cli_copias.main(argv, t), 1, argv)
            self.assertNotIn("Traceback", t.todo)

        class SinEntrada(Falso):
            def texto(self, mensaje):
                raise EOFError
        self.assertEqual(cli_copias.main(["--claves-generar", os.path.join(self.fuera, "k.txt")], SinEntrada()), 1)

    def test_carpeta_elegida_en_generar_se_guarda_y_se_comprueba(self):
        elegida = os.path.join(self.dir, "disco-externo", "copias")
        t = Falso(textos=["SI"])
        self.assertEqual(cli_copias.main(["--claves-generar", os.path.join(self.fuera, "k.txt"), "--carpeta", elegida], t), 0, t.todo)
        self.assertEqual(cfg()["copias"]["carpeta"], elegida)
        self.assertTrue(os.path.isdir(elegida))
        self.assertNotIn("mismo disco", t.todo)

    def test_aviso_si_la_carpeta_esta_en_el_mismo_disco_que_la_base(self):
        guardar_config({"copias": {"carpeta": exportacion.carpeta_por_defecto(), "destinatario": [], "firmas_publicas": []}})
        t = Falso(textos=["SI"])
        cli_copias.main(["--claves-generar", os.path.join(self.fuera, "k.txt")], t)
        self.assertIn("mismo disco", t.todo)

    @unittest.skipIf(os.name == "nt", "os.symlink requiere modo desarrollador o privilegio de administrador en Windows")
    def test_privada_en_la_carpeta_de_copias_con_otro_caso_o_enlace(self):
        enlace = os.path.join(self.dir, "enlace")
        os.symlink(self.carpeta, enlace)
        with self.assertRaisesRegex(cgx.ErrorCgx, "carpeta de las copias"):
            claves_copias.guardar_privada(os.path.join(enlace, "k.txt"), self.priv, carpeta_copias=self.carpeta)
        alterna = self.carpeta.replace("cifradas", "CIFRADAS")
        if os.path.exists(alterna):                               # sistema de ficheros que no distingue mayúsculas (APFS, NTFS)
            with self.assertRaisesRegex(cgx.ErrorCgx, "carpeta de las copias"):
                claves_copias.guardar_privada(os.path.join(alterna, "k.txt"), self.priv, carpeta_copias=self.carpeta)


if __name__ == "__main__":
    unittest.main()
