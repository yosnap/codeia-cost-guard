"""Copias cifradas: origen consistente en temporal privado, nombres y rotación escalonada que no toca lo ajeno."""
import datetime
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, exportacion, rutas  # noqa: E402


DISP = "aaaaaaaa"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-export-")
        self._datos = rutas.DATOS
        rutas.DATOS = os.path.join(self.dir, "datos")

    def tearDown(self):
        rutas.DATOS = self._datos
        shutil.rmtree(self.dir, ignore_errors=True)


class TestOrigen(Base):
    def test_copia_consistente_sin_wal_y_privada(self):
        con = almacen.abrir(os.path.join(self.dir, "viva", "consumo.sqlite"))
        q = almacen.identidad("barra")
        almacen.adquirir_escritura(con, q)
        esp = almacen.espacio(con, q)
        con.execute("PRAGMA wal_autocheckpoint=0")
        fid = almacen.fuente(con, q, esp, "claude", "f")
        almacen.ingerir_turnos(con, q, esp, fid, [{"clave": "claude:m1", "hora_utc": 1_790_000_000, "agente": "claude", "proveedor": "",
                                                    "modelo": "m", "gi": 1, "go": 2, "cr": 3, "cw": 4, "proyecto_raw": "p"}])
        ruta, estado = exportacion.copiar_base(con)
        try:
            self.assertEqual(estado, "ok")
            if os.name != "nt":                # NTFS no tiene bits de permiso POSIX
                self.assertEqual(stat.S_IMODE(os.stat(ruta).st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(ruta)).st_mode), 0o700)
            self.assertFalse(os.path.exists(ruta + "-wal"))
            c = sqlite3.connect(ruta)
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM turnos").fetchone()[0], 1)   # lo que aún estaba solo en el WAL
            c.close()
        finally:
            os.remove(ruta)
            con.close()

    def test_barrido_de_temporales(self):
        d = exportacion.dir_tmp()
        for n in ("a.tmp", "b.tmp"):
            open(os.path.join(d, n), "w").close()
        self.assertEqual(exportacion.barrer_tmp(edad_min_s=0), 2)
        self.assertEqual(os.listdir(d), [])


class TestNombresYRotacion(Base):
    def test_nombre(self):
        n = exportacion.nombre_copia(1_790_000_000, DISP)
        self.assertRegex(n, r"^copia-aaaaaaaa-\d{8}-\d{6}-[0-9a-f]{4}\.cgx$")
        self.assertTrue(exportacion.nombre_copia(1_790_000_000, DISP, danada=True).endswith(".danada.cgx"))
        self.assertNotEqual(exportacion.nombre_copia(1_790_000_000, DISP), exportacion.nombre_copia(1_790_000_000, DISP))

    @staticmethod
    def copia(dia, hora="030000", extra="aaaa", disp=None):
        return f"copia-{disp or DISP}-{dia}-{hora}-{extra}.cgx"

    def test_conserva_14_diarias_y_6_mensuales_y_no_toca_lo_ajeno(self):
        inicio = datetime.date(2026, 1, 1)
        nombres = [self.copia((inicio + datetime.timedelta(days=i)).strftime("%Y%m%d")) for i in range(300)]
        ajenos = ["notas.txt", "copia-aaaaaaaa-20260101-030000-aaaa.danada.cgx", "copia-mala.cgx", "clave.cgx"]
        borrar = set(exportacion.a_borrar(nombres + ajenos, DISP))
        self.assertFalse(borrar & set(ajenos))
        quedan = [n for n in nombres if n not in borrar]
        ultimos = nombres[-14:]
        self.assertTrue(set(ultimos) <= set(quedan))
        primeras_de_mes = {n for n in nombres if n[21:23] == "01"}
        self.assertTrue({n for n in primeras_de_mes if n[15:21] >= "202605"} <= set(quedan))     # mayo..octubre
        self.assertEqual(len(quedan), 14 + 6 - len(set(ultimos) & primeras_de_mes))
        self.assertFalse({n for n in primeras_de_mes if n[15:21] < "202605"} & set(quedan))

    def test_mismo_dia_se_queda_la_ultima(self):
        a, b = self.copia("20261014", "010000", "aaaa"), self.copia("20261014", "230000", "bbbb")
        self.assertEqual(exportacion.a_borrar([a, b], DISP), [])       # son la primera del mes y la última del día: ambas se conservan
        c = self.copia("20261014", "120000", "cccc")
        self.assertEqual(exportacion.a_borrar([a, c, b], DISP), [c])

    def test_una_sola_copia_nunca_se_borra(self):
        self.assertEqual(exportacion.a_borrar([self.copia("20260101")], DISP), [])
        self.assertEqual(exportacion.a_borrar([], DISP), [])



try:
    import cryptography  # noqa: F401
    HAY = True
except ImportError:
    HAY = False


@unittest.skipUnless(HAY, "falta cryptography")
class TestCopia(Base):
    def setUp(self):
        super().setUp()
        from infobar import cgx
        self.cgx = cgx
        self.priv, self.pub = cgx.generar_par()
        self.firma_priv, self.firma_pub = cgx.generar_par_firma()
        self.carpeta = os.path.join(self.dir, "copias-cifradas")
        os.makedirs(self.carpeta)                                     # una carpeta configurada tiene que existir (disco montado)
        self.con = almacen.abrir(os.path.join(self.dir, "viva", "consumo.sqlite"))
        q = almacen.identidad("barra")
        almacen.adquirir_escritura(self.con, q)
        esp = almacen.espacio(self.con, q)
        fid = almacen.fuente(self.con, q, esp, "claude", "f")
        almacen.ingerir_turnos(self.con, q, esp, fid, [{"clave": f"claude:m{i}", "hora_utc": 1_790_000_000 + i, "agente": "claude",
                                                         "proveedor": "", "modelo": "m", "gi": 1, "go": 2, "cr": 3, "cw": 4,
                                                         "proyecto_raw": "p"} for i in range(50)])

    def tearDown(self):
        self.con.close()
        super().tearDown()

    def copia(self, **kw):
        return exportacion.hacer_copia(self.con, self.carpeta, self.pub, self.firma_priv, self.firma_pub, **kw)

    def restos(self):
        return [f for f in os.listdir(self.carpeta) if not f.endswith(".cgx")] + os.listdir(exportacion.dir_tmp())

    def test_copia_verificada_se_descifra_con_la_privada(self):
        import io
        r = self.copia(ahora=1_790_000_000)
        self.assertRegex(r["archivo"], r"^copia-[0-9a-f]{8}-\d{8}-\d{6}-[0-9a-f]{4}\.cgx$")
        self.assertFalse(r["danada"])
        self.assertEqual(self.restos(), [])
        salida = io.BytesIO()
        with open(os.path.join(self.carpeta, r["archivo"]), "rb") as f:
            self.cgx.descifrar_con_privada(f, salida, self.priv, [self.firma_pub])
        ruta = os.path.join(self.dir, "restaurada.sqlite")
        with open(ruta, "wb") as f:
            f.write(salida.getvalue())
        self.assertEqual(sqlite3.connect(ruta).execute("SELECT COUNT(*) FROM turnos").fetchone()[0], 50)
        with open(os.path.join(self.carpeta, r["archivo"]), "rb") as f:
            self.assertNotIn(b"claude:m1", f.read())

    def test_fallo_al_escribir_no_deja_nada_ni_rota(self):
        self.copia(ahora=1_790_000_000)
        antes = sorted(os.listdir(self.carpeta))
        real = self.cgx.cifrar_a_destinatario

        def falla(entrada, longitud, salida, *a, **kw):
            salida.write(b"x" * 1000)
            raise OSError("disco lleno")
        with mock.patch.object(self.cgx, "cifrar_a_destinatario", falla):
            with self.assertRaises(OSError):
                self.copia(ahora=1_790_086_400)
        self.assertEqual(sorted(os.listdir(self.carpeta)), antes)
        self.assertEqual(os.listdir(exportacion.dir_tmp()), [])

    def test_verificacion_fallida_no_rota_las_buenas(self):
        nombres = []
        for dia in range(20):
            nombres.append(self.copia(ahora=1_790_000_000 + dia * 86400)["archivo"])
        antes = sorted(os.listdir(self.carpeta))
        with mock.patch.object(self.cgx, "verificar_con_clave", side_effect=self.cgx.ErrorCgx("corrupta")):
            with self.assertRaises(self.cgx.ErrorCgx):
                self.copia(ahora=1_790_000_000 + 30 * 86400)
        self.assertEqual(sorted(os.listdir(self.carpeta)), antes)
        self.assertEqual(self.restos(), [])

    def test_rotacion_tras_verificar(self):
        for dia in range(20):
            self.copia(ahora=1_790_000_000 + dia * 86400)
        buenas = [f for f in os.listdir(self.carpeta) if f.endswith(".cgx")]
        self.assertLessEqual(len(buenas), 14 + 2)
        self.assertGreaterEqual(len(buenas), 14)

    def test_base_danada_se_copia_aparte_y_no_rota(self):
        for dia in range(3):
            self.copia(ahora=1_790_000_000 + dia * 86400)
        real = exportacion.copiar_base

        def con_fallo(con):
            ruta, _estado = real(con)
            return ruta, "fallo de prueba en la pagina 7"
        with mock.patch.object(exportacion, "copiar_base", con_fallo):
            r = self.copia(ahora=1_790_000_000 + 10 * 86400)
        self.assertTrue(r["danada"])
        self.assertTrue(r["archivo"].endswith(".danada.cgx"))
        self.assertEqual(r["rotadas"], 0)
        self.assertEqual(len([f for f in os.listdir(self.carpeta) if not f.endswith(".danada.cgx")]), 3)

    def test_dos_copias_en_el_mismo_segundo_no_se_pisan(self):
        a, b = self.copia(ahora=1_790_000_000), self.copia(ahora=1_790_000_000)
        self.assertNotEqual(a["archivo"], b["archivo"])

    def test_cuando_toca(self):
        self.assertTrue(exportacion.toca_copia(1_790_000_000))
        exportacion.registrar_copia(self.copia(ahora=1_790_000_000))
        self.assertFalse(exportacion.toca_copia(1_790_000_000 + 3600))
        self.assertTrue(exportacion.toca_copia(1_790_000_000 + 86400))
        exportacion.registrar_copia({"archivo": "x", "ts": 1_790_100_000, "danada": True, "huella": "h", "bytes": 1})
        self.assertFalse(exportacion.toca_copia(1_790_100_100))            # una .danada cuenta como hecha: como mucho una al día
        self.assertTrue(exportacion.toca_copia(1_790_100_000 + 86400))


class TestReemplazo(Base):
    def test_reintenta_en_windows_y_abandona_al_agotarlos(self):
        a, b = os.path.join(self.dir, "a"), os.path.join(self.dir, "b")
        open(a, "w").close()
        reales = os.replace
        llamadas = []

        def falla_dos_veces(x, y):
            llamadas.append(1)
            if len(llamadas) <= 2:
                raise PermissionError("en uso")
            return reales(x, y)
        with mock.patch.object(os, "replace", falla_dos_veces):
            exportacion.reemplazar(a, b, espera=0)
        self.assertTrue(os.path.exists(b))
        open(a, "w").close()
        with mock.patch.object(os, "replace", side_effect=PermissionError("en uso")):
            with self.assertRaises(PermissionError):
                exportacion.reemplazar(a, b, intentos=3, espera=0)


if __name__ == "__main__":
    unittest.main()
