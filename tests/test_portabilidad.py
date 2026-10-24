"""Lo que hace falta para que el ciclo de servidores y la retención funcionen igual en macOS, Linux y Windows: cerrojo, matar árboles de
procesos, exclusión de copias del sistema, poda de IPs de la caché, purga horaria de imágenes y el modo sin interfaz."""
import ast
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import permisos, plataforma, rutas, servidores_ciclo, trabajador_barra  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class TestNadaDeSoloPosixAlImportar(unittest.TestCase):
    def test_ningun_modulo_importa_fcntl_ni_resource_arriba(self):
        for f in glob.glob(os.path.join(RAIZ, "infobar", "*.py")) + [os.path.join(RAIZ, "costbar.py")]:
            arbol = ast.parse(open(f, encoding="utf-8").read())
            for nodo in arbol.body:                                   # solo el nivel del módulo: dentro de funciones vale
                nombres = [a.name for a in nodo.names] if isinstance(nodo, ast.Import) else ([nodo.module] if isinstance(nodo, ast.ImportFrom) else [])
                for n in nombres:
                    self.assertNotIn((n or "").split(".")[0], ("fcntl", "resource", "pwd", "grp", "termios"), f)

    def test_killpg_y_sesiones_nuevas_solo_en_plataforma(self):
        for f in glob.glob(os.path.join(RAIZ, "infobar", "*.py")) + [os.path.join(RAIZ, "costbar.py")]:
            if f.endswith("plataforma.py"):
                continue
            texto = open(f, encoding="utf-8").read()
            self.assertNotIn("os.killpg", texto, f)
            self.assertNotIn("start_new_session", texto, f)


class TestProcesos(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX")
    def test_matar_arbol_mata_tambien_a_los_nietos(self):
        codigo = textwrap.dedent("""
            import subprocess, sys, time
            h = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            print(h.pid, flush=True)
            time.sleep(60)
        """)
        p = subprocess.Popen([sys.executable, "-c", codigo], stdout=subprocess.PIPE, text=True, **plataforma.grupo_nuevo_kwargs())
        nieto = int(p.stdout.readline())
        self.assertTrue(vivo(nieto))
        plataforma.matar_arbol(p)
        p.wait(timeout=10)
        for _ in range(50):
            if not vivo(nieto):
                break
            time.sleep(0.1)
        self.assertFalse(vivo(nieto))

    def test_matar_arbol_de_algo_que_ya_termino_no_falla(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"], **plataforma.grupo_nuevo_kwargs())
        p.wait()
        plataforma.matar_arbol(p)

    def test_argumentos_de_grupo_segun_el_sistema(self):
        with mock.patch.object(plataforma.os, "name", "posix"):
            self.assertEqual(plataforma.grupo_nuevo_kwargs(), {"start_new_session": True})
        with mock.patch.object(plataforma.os, "name", "nt"), \
             mock.patch.object(plataforma.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
             mock.patch.object(plataforma.subprocess, "CREATE_NO_WINDOW", 0x8000000, create=True):
            self.assertEqual(plataforma.grupo_nuevo_kwargs(), {"creationflags": 512 | 0x8000000})

    def test_en_windows_se_usa_taskkill_con_arbol(self):
        falso = mock.Mock(pid=4242)
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.object(plataforma.subprocess, "run") as run:
            plataforma.matar_arbol(falso)
        self.assertEqual(run.call_args[0][0], ["taskkill", "/T", "/F", "/PID", "4242"])
        falso.kill.assert_called_once()


class TestCerrojo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-lock-")
        self.ruta = os.path.join(self.dir, "x.lock")

    @unittest.skipIf(os.name == "nt", "POSIX")
    def test_exclusion_mutua_entre_dos_aperturas(self):
        orden, dentro = [], threading.Event()

        def primero():
            with open(self.ruta, "a+") as f, plataforma.bloqueo_de_fichero(f):
                dentro.set()
                time.sleep(0.4)
                orden.append("A")

        def segundo():
            dentro.wait()
            with open(self.ruta, "a+") as f, plataforma.bloqueo_de_fichero(f):
                orden.append("B")

        hilos = [threading.Thread(target=primero), threading.Thread(target=segundo)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        self.assertEqual(orden, ["A", "B"])

    def falso_msvcrt(self, fallos):
        m = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, llamadas=[], pendientes=fallos)

        def locking(fd, modo, n):
            m.llamadas.append((modo, n))
            if modo == m.LK_NBLCK and m.pendientes > 0:
                m.pendientes -= 1
                raise OSError("ocupado")
        m.locking = locking
        return m

    def test_en_windows_reintenta_y_suelta(self):
        m = self.falso_msvcrt(fallos=2)
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.dict(sys.modules, {"msvcrt": m}), open(self.ruta, "a+") as f:
            with plataforma.bloqueo_de_fichero(f, espera_s=5):
                pass
        self.assertEqual([c[0] for c in m.llamadas], [2, 2, 2, 0])         # dos fallos, éxito y liberación

    def test_en_windows_no_espera_para_siempre(self):
        m = self.falso_msvcrt(fallos=10 ** 6)
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.dict(sys.modules, {"msvcrt": m}), open(self.ruta, "a+") as f:
            with self.assertRaises(TimeoutError):
                with plataforma.bloqueo_de_fichero(f, espera_s=0.2):
                    self.fail("no debería entrar")

    def test_bloqueo_de_config_crea_el_byte_que_windows_necesita(self):
        from infobar import config_y_estado
        m = self.falso_msvcrt(fallos=0)
        ruta = os.path.join(self.dir, "datos.json")
        with mock.patch.object(plataforma.os, "name", "nt"), mock.patch.object(config_y_estado.os, "name", "nt"), \
                mock.patch.dict(sys.modules, {"msvcrt": m}):
            with config_y_estado._bloqueo(ruta):
                pass
        self.assertEqual(open(ruta + ".lock", "rb").read(), b"\0")


class TestExcluirDeCopias(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-excl-")

    def test_ruta_inexistente(self):
        self.assertFalse(plataforma.excluir_de_copias_del_sistema(os.path.join(self.dir, "no")))

    def test_macos_ya_excluida_no_vuelve_a_pedirlo(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run",
                                                                              return_value=mock.Mock(stdout="[Excluded]  /x", returncode=0)) as run:
            self.assertTrue(plataforma.excluir_de_copias_del_sistema(self.dir))
        self.assertEqual(run.call_count, 1)

    def test_macos_la_excluye_si_no_lo_estaba_y_lo_verifica(self):
        respuestas = [mock.Mock(stdout="[Included]  /x", returncode=0), mock.Mock(stdout="", returncode=0), mock.Mock(stdout="[Excluded]  /x", returncode=0)]
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run", side_effect=respuestas) as run, \
                mock.patch("time.sleep"):
            self.assertTrue(plataforma.excluir_de_copias_del_sistema(self.dir))
        self.assertEqual([c[0][0][1] for c in run.call_args_list], ["isexcluded", "addexclusion", "isexcluded"])

    def test_macos_si_el_sistema_tarda_en_reflejarlo_reintenta(self):
        incluida, ok = mock.Mock(stdout="[Included]", returncode=0), mock.Mock(stdout="", returncode=0)
        respuestas = [incluida, ok, incluida, ok, mock.Mock(stdout="[Excluded]", returncode=0)]
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run", side_effect=respuestas), mock.patch("time.sleep"):
            self.assertTrue(plataforma.excluir_de_copias_del_sistema(self.dir))

    def test_macos_si_nunca_queda_excluida_dice_que_no(self):
        incluida, ok = mock.Mock(stdout="[Included]", returncode=0), mock.Mock(stdout="", returncode=0)
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run", side_effect=[incluida, ok] * 3), mock.patch("time.sleep"):
            self.assertFalse(plataforma.excluir_de_copias_del_sistema(self.dir))

    def test_macos_si_tmutil_falla_dice_que_no(self):
        respuestas = [mock.Mock(stdout="[Included]", returncode=0), mock.Mock(stdout="", returncode=1)]
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run", side_effect=respuestas), mock.patch("time.sleep"):
            self.assertFalse(plataforma.excluir_de_copias_del_sistema(self.dir))
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(plataforma.subprocess, "run", side_effect=OSError("sin tmutil")):
            self.assertFalse(plataforma.excluir_de_copias_del_sistema(self.dir))

    def test_windows_marca_no_indexar(self):
        kernel = mock.Mock()
        kernel.GetFileAttributesW.return_value = 0x10
        kernel.SetFileAttributesW.return_value = 1
        falso_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=kernel))
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(plataforma.os, "name", "nt"), mock.patch.dict(sys.modules, {"ctypes": falso_ctypes}):
            self.assertTrue(plataforma.excluir_de_copias_del_sistema(self.dir))
        kernel.SetFileAttributesW.assert_called_once_with(self.dir, 0x10 | 0x2000)


class TestRetencionFueraDeLaBase(unittest.TestCase):
    def entrada(self, ok_en):
        return {"ok_en": ok_en, "estado": "verde", "datos": {"intentos": {"total": 9, "hora": 1, "top": [["203.0.113.9", 5], ["198.51.100.7", 2]]},
                                                            "sesiones": [{"usuario": "root", "desde": "203.0.113.50"}]}}

    def test_pasadas_48_horas_se_quitan_las_ips_y_se_conservan_los_recuentos(self):
        ahora = 10 ** 9
        e = servidores_ciclo.podar_ips(self.entrada(ahora - 49 * 3600), ahora)
        self.assertEqual(e["datos"]["intentos"]["top"], [])
        self.assertEqual((e["datos"]["intentos"]["total"], e["datos"]["intentos"]["hora"]), (9, 1))
        self.assertEqual(e["datos"]["sesiones"][0], {"usuario": "root", "desde": ""})

    def test_dentro_de_las_48_horas_no_se_toca(self):
        ahora = 10 ** 9
        e = servidores_ciclo.podar_ips(self.entrada(ahora - 47 * 3600), ahora)
        self.assertEqual(e["datos"]["intentos"]["top"][0][0], "203.0.113.9")

    def test_entradas_raras_no_rompen(self):
        for rara in ({}, {"ok_en": 1}, {"ok_en": 1, "datos": None}, {"ok_en": 1, "datos": {"intentos": 5, "sesiones": [7]}}):
            servidores_ciclo.podar_ips(rara, 10 ** 9)

    def test_guardar_aplica_la_poda_al_escribir_la_cache(self):
        ahora = 10 ** 9
        servidores = [{"nombre": "uno"}]
        cache = {"uno": self.entrada(ahora - 60 * 3600)}
        out = servidores_ciclo._guardar(servidores, cache, {}, ahora)
        self.assertEqual(out["servidores"]["uno"]["datos"]["intentos"]["top"], [])
        import json
        en_disco = json.load(open(rutas.SERVIDORES))
        self.assertNotIn("203.0.113", json.dumps(en_disco))

    def test_purgar_render_borra_lo_viejo_tambien_en_windows(self):
        render = os.path.join(os.path.dirname(rutas.LOG), "render")
        os.makedirs(render, exist_ok=True)
        viejo, nuevo = os.path.join(render, "viejo.png"), os.path.join(render, "nuevo.png")
        for f in (viejo, nuevo):
            open(f, "wb").write(b"x")
        pasado = time.time() - permisos.RENDER_CADUCA_S - 60
        os.utime(viejo, (pasado, pasado))
        with mock.patch.object(permisos.os, "name", "nt"), mock.patch.object(permisos, "_modo") as modo:
            permisos.purgar_render()
        self.assertFalse(os.path.exists(viejo))
        self.assertTrue(os.path.exists(nuevo))
        modo.assert_not_called()                        # en Windows los permisos van por la carpeta del usuario, no por chmod

    def test_excluir_datos_sensibles_cuenta_lo_conseguido(self):
        with mock.patch.object(plataforma, "excluir_de_copias_del_sistema", side_effect=[True, False]) as ex:
            self.assertEqual(permisos.excluir_datos_sensibles(), 1)
        self.assertEqual({c[0][0] for c in ex.call_args_list}, {rutas.DATOS, os.path.dirname(rutas.LOG)})


class TestModoSinInterfaz(unittest.TestCase):
    def test_el_trabajador_sin_pintar_no_llama_a_chrome(self):
        t = trabajador_barra.Trabajador(lambda *a: None, pintar=False)
        with mock.patch.object(trabajador_barra.desplegable_png, "renderizar", side_effect=AssertionError("no debe renderizar")):
            self.assertFalse(t._pinta({}, {}))

    def test_el_demonio_arranca_trabajador_ingesta_y_copias(self):
        import costbar
        creados = []

        class Falso:
            def __init__(self, *a, **k):
                creados.append((type(self).__name__, k))
            def start(self):
                pass

        class FalsoTrabajador(Falso):
            pass

        class FalsoIngesta(Falso):
            pass

        class FalsoCopias(Falso):
            pass

        with mock.patch.object(costbar, "servir"), mock.patch.object(trabajador_barra, "Trabajador", FalsoTrabajador), \
                mock.patch("infobar.ingestor.HiloIngesta", FalsoIngesta), mock.patch("infobar.hilo_copias.HiloCopias", FalsoCopias), \
                mock.patch.object(permisos, "excluir_datos_sensibles"), mock.patch.object(costbar, "cfg", lambda: {"almacen": "activo"}):
            hilos = costbar.demonio(esperar=False)
        self.assertEqual([c[0] for c in creados], ["FalsoTrabajador", "FalsoIngesta", "FalsoCopias"])
        self.assertEqual(len(hilos), 3)

    def test_con_el_almacen_desactivado_solo_el_trabajador(self):
        import costbar
        arrancados = []

        class FalsoTrabajador:
            def __init__(self, entregar, pintar=True):
                self.pintar = pintar
            def start(self):
                arrancados.append(self.pintar)

        with mock.patch.object(costbar, "servir"), mock.patch.object(trabajador_barra, "Trabajador", FalsoTrabajador), \
                mock.patch.object(permisos, "excluir_datos_sensibles"), mock.patch.object(costbar, "cfg", lambda: {"almacen": "desactivado"}):
            hilos = costbar.demonio(esperar=False)
        self.assertEqual((len(hilos), arrancados), (1, [False]))        # sin interfaz: el trabajador no pinta



class RutasMezcladasTest(unittest.TestCase):
    """En Windows glob deja "/" en el trozo literal del patrón y "\\" en lo que recorre: la misma ruta trae las dos."""

    def test_sombra_reconoce_el_agente_con_separadores_mezclados(self):
        from infobar import sombra
        self.assertEqual(sombra.agente_de("C:/Users/x/.claude/projects\\p\\s.jsonl", {}), "claude")
        self.assertEqual(sombra.agente_de("C:\\Users\\x\\.codex\\sessions\\s.jsonl", {}), "codex")

    def test_lector_antiguo_normaliza_las_barras_invertidas(self):
        from infobar import lectores_logs
        self.assertEqual(lectores_logs._posix("C:\\Users\\x\\proyecto"), "C:/Users/x/proyecto")
        self.assertEqual(lectores_logs._posix(None), "")



class ProcesosSinVentanaTest(unittest.TestCase):
    """La bandeja corre con pythonw.exe: cada hijo de consola sin CREATE_NO_WINDOW abriría su propia ventana."""

    def test_en_windows_los_hijos_no_abren_consola_y_siguen_en_grupo_propio(self):
        with mock.patch.object(plataforma.os, "name", "nt"), \
             mock.patch.object(plataforma.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
             mock.patch.object(plataforma.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True):
            self.assertEqual(plataforma.sin_ventana_kwargs(), {"creationflags": 0x08000000})
            self.assertEqual(plataforma.grupo_nuevo_kwargs(), {"creationflags": 0x08000200})

    def test_fuera_de_windows_no_cambia_nada(self):
        if os.name == "nt":
            self.skipTest("solo POSIX")
        self.assertEqual(plataforma.sin_ventana_kwargs(), {})
        self.assertEqual(plataforma.grupo_nuevo_kwargs(), {"start_new_session": True})

    def test_ssh_y_alta_lanzan_sin_ventana(self):
        for rel in ("infobar/servidores_externo.py", "infobar/servidores_alta.py"):
            with open(os.path.join(RAIZ, rel), encoding="utf-8") as f:
                self.assertIn("sin_ventana_kwargs()", f.read(), rel)


class ResuelveProgramaTest(unittest.TestCase):
    """plataforma.resuelve_programa(): nunca el directorio de trabajo actual, nunca .cmd/.bat (cmd.exe
    reparsearia mal un argumento con caracteres especiales), y el .py de pega solo si COSTGUARD_FAKES_DIR
    lo apunta explicitamente - nunca escaneando el PATH, ni siquiera en los tests."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-resuelve-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self._cwd = os.getcwd()
        self.addCleanup(os.chdir, self._cwd)
        self._path = os.environ.get("PATH", "")
        self.addCleanup(os.environ.__setitem__, "PATH", self._path)
        self.addCleanup(os.environ.pop, "COSTGUARD_FAKES_DIR", None)
        os.environ.pop("COSTGUARD_FAKES_DIR", None)
        # sin esto, si el proceso ya la tuviera puesta, test_el_directorio_de_trabajo_actual_no_se_consulta
        # pasaria igual con shutil.which (el codigo viejo) y no probaria nada
        nodefault = os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
        if nodefault is None:
            self.addCleanup(os.environ.pop, "NoDefaultCurrentDirectoryInExePath", None)
        else:
            self.addCleanup(os.environ.__setitem__, "NoDefaultCurrentDirectoryInExePath", nodefault)

    def _toca(self, *partes):
        ruta = os.path.join(self.dir, *partes)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        open(ruta, "w").close()
        return ruta

    def test_un_programa_que_ya_trae_ruta_no_se_toca(self):
        # algunos tests pasan sys.executable (ruta absoluta) en vez de "ssh" para simular el servidor
        # con un proceso controlable; no hay nada que resolver por PATH ahi, debe devolverse tal cual
        os.environ["PATH"] = ""                        # ni siquiera estar vacio debe importar
        self.assertEqual(plataforma.resuelve_programa([sys.executable, "-c", "1"]), [sys.executable, "-c", "1"])
        relativo = os.path.join("subdir", "programa")  # con componente de ruta aunque sea relativa
        self.assertEqual(plataforma.resuelve_programa([relativo]), [relativo])

    def _asegura_no_encontrado(self, orden):
        # en Windows, no encontrarlo en ningun PATH lanza (ver resuelve_programa); dejarlo pasar sin
        # tocar reabriria la misma puerta del directorio de trabajo actual via el CreateProcess de
        # subprocess. En POSIX no hace falta: el kernel no mira el directorio de trabajo actual al
        # lanzar un nombre pelado salvo que este literalmente en el PATH
        if os.name == "nt":
            with self.assertRaises(FileNotFoundError):
                plataforma.resuelve_programa(orden)
        else:
            self.assertEqual(plataforma.resuelve_programa(orden), orden)

    @unittest.skipIf(os.name != "nt", "shutil.which consultando antes el directorio de trabajo actual es un "
                      "comportamiento especifico de Windows (Python 3.12+); no aplica en POSIX")
    def test_el_directorio_de_trabajo_actual_no_se_consulta(self):
        trampa, real_dir = os.path.join(self.dir, "trampa"), os.path.join(self.dir, "real")
        self._toca("trampa", "programa.exe")
        ruta_real = self._toca("real", "programa.exe")
        os.chdir(trampa)
        os.environ["PATH"] = real_dir
        self.assertEqual(plataforma.resuelve_programa(["programa", "-x"]), [ruta_real, "-x"])

    @unittest.skipIf(os.name != "nt", "cmd/bat solo existen en Windows")
    def test_nunca_resuelve_a_cmd_o_bat(self):
        self._toca("bin", "programa.cmd")
        os.environ["PATH"] = os.path.join(self.dir, "bin")
        self._asegura_no_encontrado(["programa", "-x"])   # nada que valga (.cmd no cuenta): no se encuentra

    def test_entradas_de_path_vacias_o_relativas_se_ignoran(self):
        ruta_real = self._toca("bin", "programa" + (".exe" if os.name == "nt" else ""))
        if os.name != "nt":
            os.chmod(ruta_real, 0o755)
        os.environ["PATH"] = os.pathsep.join(("", ".", os.path.join(self.dir, "bin")))
        self.assertEqual(plataforma.resuelve_programa(["programa"]), [ruta_real])

    def test_el_py_de_pega_solo_se_usa_si_costguard_fakes_dir_lo_apunta(self):
        fakes = os.path.join(self.dir, "fakes")
        self._toca("fakes", "programa.py")
        os.environ["PATH"] = ""
        self._asegura_no_encontrado(["programa"])         # sin la variable: no se usa, y no hay nada mas
        os.environ["COSTGUARD_FAKES_DIR"] = fakes
        self.assertEqual(plataforma.resuelve_programa(["programa", "-x"]),
                         [sys.executable, os.path.join(fakes, "programa.py"), "-x"])

    def test_el_py_de_pega_nunca_se_busca_escaneando_el_path(self):
        self._toca("bin", "programa.py")                 # un .py suelto en el PATH (no via COSTGUARD_FAKES_DIR)
        os.environ["PATH"] = os.path.join(self.dir, "bin")
        self._asegura_no_encontrado(["programa"])         # nunca se mira: no se encuentra


class PuertoExclusivoTest(unittest.TestCase):
    def test_un_segundo_servidor_no_comparte_el_puerto(self):
        from infobar import servidor_local
        uno = servidor_local.crear_servidor(0)
        try:
            puerto = uno.server_address[1]
            with self.assertRaises(OSError):
                servidor_local.crear_servidor(puerto).server_close()
        finally:
            uno.server_close()


if __name__ == "__main__":
    unittest.main()
