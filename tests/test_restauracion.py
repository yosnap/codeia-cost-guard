"""Restauración: la base antigua no resucita por su WAL (experimento medido en la revisión), nada cambia si falla algo, el puesto de
escritor se respeta, la copia previa se guarda y se rechazan copias de versión nueva, ajenas o dañadas."""
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, exportacion, restauracion, rutas  # noqa: E402

try:
    import cryptography  # noqa: F401
    from infobar import cgx
    HAY = True
except ImportError:
    HAY = False

FRASE = "una frase de prueba larga"


def turno(clave, **kw):
    t = {"clave": clave, "hora_utc": 1_790_000_000, "agente": "claude", "proveedor": "", "modelo": "m", "gi": 1, "go": 2, "cr": 3, "cw": 4,
         "proyecto_raw": "p"}
    t.update(kw)
    return t


def base_con(ruta, claves, mantener_abierta=False):
    con = almacen.abrir(ruta)
    q = almacen.identidad("test")
    almacen.adquirir_escritura(con, q)
    esp = almacen.espacio(con, q)
    fid = almacen.fuente(con, q, esp, "claude", "f")
    almacen.ingerir_turnos(con, q, esp, fid, [turno(c) for c in claves])
    almacen.soltar_escritura(con, q)
    if mantener_abierta:
        return con
    con.close()


def claves_de(ruta):
    con = sqlite3.connect(ruta)
    try:
        return sorted(f[0] for f in con.execute("SELECT clave FROM turnos"))
    finally:
        con.close()


@unittest.skipUnless(HAY, "falta cryptography")
class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-restaura-")
        self._datos, self._copias = rutas.DATOS, rutas.COPIAS
        rutas.DATOS = os.path.join(self.dir, "datos")
        rutas.COPIAS = os.path.join(rutas.DATOS, "copias")
        self.destino = os.path.join(self.dir, "datos", "consumo.sqlite")
        self.priv, self.pub = cgx.generar_par()
        self.fpriv, self.fpub = cgx.generar_par_firma()
        # la copia que se restaurará: contiene "claude:nueva-1/2"
        origen = base_con(os.path.join(self.dir, "origen", "consumo.sqlite"), ["claude:nueva-1", "claude:nueva-2"], True)
        self.carpeta = os.path.join(self.dir, "cifradas")
        os.makedirs(self.carpeta)
        r = exportacion.hacer_copia(origen, self.carpeta, self.pub, self.fpriv, self.fpub)
        origen.close()
        self.cgx_ruta = os.path.join(self.carpeta, r["archivo"])

    def tearDown(self):
        rutas.DATOS, rutas.COPIAS = self._datos, self._copias
        shutil.rmtree(self.dir, ignore_errors=True)

    def descifrar(self, entrada, salida):
        cgx.descifrar_con_privada(entrada, salida, self.priv, [self.fpub])

    def restaura(self, confirmar=lambda info: True, **kw):
        return restauracion.restaurar(self.cgx_ruta, self.descifrar, confirmar, destino=self.destino, **kw)


class TestRestaurar(Base):
    def test_sobre_destino_vacio(self):
        r = self.restaura()
        self.assertEqual(r["turnos"], 2)
        self.assertFalse(r["sustituyo"])
        self.assertEqual(claves_de(self.destino), ["claude:nueva-1", "claude:nueva-2"])

    def test_no_pisa_sin_forzar(self):
        base_con(self.destino, ["claude:vieja"])
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "--forzar"):
            self.restaura()
        self.assertEqual(claves_de(self.destino), ["claude:vieja"])

    @unittest.skipIf(os.name == "nt", "requiere borrar un -wal con una conexion abierta (con) todo el rato: en Windows la VFS de "
                      "SQLite no pide FILE_SHARE_DELETE, asi que eso bloquea el borrado (no hay unlink-con-fd-abierto como en "
                      "POSIX); no se puede cerrar `con` antes porque CIERRA->checkpoint borraria el WAL huerfano que el test "
                      "necesita crear a mano (verificado: Connection.close() hace checkpoint final aunque wal_autocheckpoint=0)")
    def test_el_wal_de_la_base_antigua_no_resucita(self):
        # la base antigua tiene una fila que SOLO vive en su -wal (la conexión abierta impide el checkpoint)
        viva = base_con(self.destino, ["claude:vieja-checkpoint"], True)
        viva.execute("PRAGMA wal_autocheckpoint=0")
        q = almacen.identidad("otro")
        almacen.adquirir_escritura(viva, q)
        fid = viva.execute("SELECT id FROM fuentes").fetchone()[0]
        almacen.ingerir_turnos(viva, q, viva.execute("SELECT id FROM espacios").fetchone()[0], fid, [turno("claude:SOLO-EN-WAL")])
        almacen.soltar_escritura(viva, q)
        self.assertTrue(os.path.exists(self.destino + "-wal"))
        viva.close()                                   # el cierre limpio haría checkpoint: recreamos el huérfano a mano
        con = sqlite3.connect(self.destino)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("INSERT INTO turnos(clave, espacio_id, fuente_id, hora_utc, agente, proveedor, modelo, gi, go, cr, cw, proyecto_raw,"
                    " productor_version, actualizado_en) SELECT 'claude:SOLO-EN-WAL-2', espacio_id, fuente_id, hora_utc, agente, proveedor,"
                    " modelo, gi, go, cr, cw, proyecto_raw, productor_version, actualizado_en FROM turnos LIMIT 1")
        con.commit()
        r = self.restaura(forzar=True)
        self.assertTrue(r["sustituyo"])
        self.assertEqual(claves_de(self.destino), ["claude:nueva-1", "claude:nueva-2"])
        self.assertFalse(os.path.exists(self.destino + "-wal"))
        con.close()

    @unittest.skipIf(os.name == "nt", "lector se mantiene abierto a proposito durante TODA la llamada (no es un bloqueo transitorio "
                      "que el reintento de _borra_wal pueda esperar): en Windows la VFS de SQLite no pide FILE_SHARE_DELETE, asi que "
                      "esto bloquea el borrado del -wal sin remedio, ver test_un_lector_transitorio_no_aborta_la_restauracion para "
                      "el caso equivalente con un lector que SI suelta a tiempo")
    def test_wal_huerfano_con_un_lector_abierto_no_resucita(self):
        # un lector con la lectura abierta impide que el cierre borre el -wal: queda un WAL con filas que no están en la base
        base_con(self.destino, ["claude:vieja"])
        lector = sqlite3.connect(self.destino)
        lector.execute("PRAGMA journal_mode=WAL")
        lector.execute("BEGIN")
        lector.execute("SELECT COUNT(*) FROM turnos").fetchone()
        escritor = sqlite3.connect(self.destino)
        escritor.execute("PRAGMA wal_autocheckpoint=0")
        escritor.execute("INSERT INTO turnos(clave, espacio_id, fuente_id, hora_utc, agente, proveedor, modelo, gi, go, cr, cw, proyecto_raw,"
                         " productor_version, actualizado_en) SELECT 'claude:SOLO-EN-WAL', espacio_id, fuente_id, hora_utc, agente, proveedor,"
                         " modelo, gi, go, cr, cw, proyecto_raw, productor_version, actualizado_en FROM turnos LIMIT 1")
        escritor.commit()
        escritor.close()
        self.restaura(forzar=True)
        lector.close()
        self.assertEqual(claves_de(self.destino), ["claude:nueva-1", "claude:nueva-2"])

    @unittest.skipIf(os.name != "nt", "solo tiene sentido en Windows: en POSIX unlink() nunca falla "
                      "por tener el fichero abierto, asi que os.remove() de _borra_wal acertaria a la "
                      "primera, time.sleep no se llamaria nunca, e intentos se quedaria vacio")
    def test_un_lector_transitorio_no_aborta_la_restauracion(self):
        # mismo montaje que test_wal_huerfano_con_un_lector_abierto_no_resucita (lector + escritor con
        # autocheckpoint=0), pero aqui el lector suelta el -wal DURANTE el reintento de _borra_wal
        # (en su 2a espera, no al final): en Windows debe bastar para no abortar por un bloqueo
        # transitorio. Se libera desde el propio time.sleep del reintento (no con un reloj real) para
        # no depender de cuanto tarde el resto de restaurar() en llegar a _borra_wal.
        base_con(self.destino, ["claude:vieja"])
        lector = sqlite3.connect(self.destino)
        lector.execute("PRAGMA journal_mode=WAL")
        lector.execute("BEGIN")
        lector.execute("SELECT COUNT(*) FROM turnos").fetchone()
        escritor = sqlite3.connect(self.destino)
        escritor.execute("PRAGMA wal_autocheckpoint=0")
        escritor.execute("INSERT INTO turnos(clave, espacio_id, fuente_id, hora_utc, agente, proveedor, modelo, gi, go, cr, cw, proyecto_raw,"
                         " productor_version, actualizado_en) SELECT 'claude:SOLO-EN-WAL', espacio_id, fuente_id, hora_utc, agente, proveedor,"
                         " modelo, gi, go, cr, cw, proyecto_raw, productor_version, actualizado_en FROM turnos LIMIT 1")
        escritor.commit()
        escritor.close()
        intentos = []

        def duerme(_):
            intentos.append(1)
            if len(intentos) == 2:
                lector.close()

        with mock.patch.object(restauracion.time, "sleep", side_effect=duerme):
            r = self.restaura(forzar=True)
        self.assertGreaterEqual(len(intentos), 2)          # de verdad reintento, no paso a la primera
        self.assertTrue(r["sustituyo"])
        self.assertEqual(claves_de(self.destino), ["claude:nueva-1", "claude:nueva-2"])

    def test_guarda_copia_previa_de_la_base_actual(self):
        base_con(self.destino, ["claude:vieja"])
        r = self.restaura(forzar=True)
        self.assertTrue(r["copia_previa"] and os.path.exists(r["copia_previa"]))
        self.assertEqual(claves_de(r["copia_previa"]), ["claude:vieja"])
        if os.name != "nt":                # NTFS no tiene bits de permiso POSIX
            self.assertEqual(oct(os.stat(r["copia_previa"]).st_mode & 0o777), "0o600")

    def test_si_hay_otro_escritor_no_toca_nada(self):
        viva = base_con(self.destino, ["claude:vieja"], True)
        almacen.adquirir_escritura(viva, almacen.identidad("barra"))
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "escribiendo"):
            self.restaura(forzar=True)
        viva.close()
        self.assertEqual(claves_de(self.destino), ["claude:vieja"])

    def test_confirmacion_negativa_no_cambia_nada(self):
        base_con(self.destino, ["claude:vieja"])
        vistos = []
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "cancelada"):
            self.restaura(confirmar=lambda info: vistos.append(info) or False, forzar=True)
        self.assertEqual(vistos[0]["turnos"], 2)
        self.assertTrue(vistos[0]["sustituye"])
        self.assertEqual(claves_de(self.destino), ["claude:vieja"])
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.destino)) if f.startswith(".restaurando")], [])

    def test_no_se_puede_borrar_el_wal_aborta_sin_cambiar(self):
        base_con(self.destino, ["claude:vieja"])
        existe, quita = os.path.exists, os.remove

        def hay(ruta):
            return True if str(ruta).endswith("-wal") else existe(ruta)

        def remove(ruta):
            if str(ruta).endswith("-wal"):
                raise PermissionError("en uso")
            return quita(ruta)
        with mock.patch.object(os.path, "exists", hay), mock.patch.object(os, "remove", remove):
            with self.assertRaisesRegex(restauracion.ErrorRestauracion, "no se ha cambiado nada"):
                self.restaura(forzar=True)
        self.assertEqual(claves_de(self.destino), ["claude:vieja"])

    def test_copia_manipulada_no_cambia_nada(self):
        base_con(self.destino, ["claude:vieja"])
        with open(self.cgx_ruta, "r+b") as f:
            f.seek(cgx.TAM_CABECERA + 100)
            b = f.read(1)
            f.seek(cgx.TAM_CABECERA + 100)
            f.write(bytes([b[0] ^ 1]))
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "manipulado|dañado"):
            self.restaura(forzar=True)
        self.assertEqual(claves_de(self.destino), ["claude:vieja"])
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.destino)) if f.startswith(".restaurando")], [])

    def test_copia_de_otro_dispositivo_sin_firma_conocida(self):
        _otra, otra_pub = cgx.generar_par_firma()
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "firmada"):
            restauracion.restaurar(self.cgx_ruta, lambda e, s: cgx.descifrar_con_privada(e, s, self.priv, [otra_pub]),
                                   lambda i: True, destino=self.destino)
        self.assertFalse(os.path.exists(self.destino))

    def test_clave_equivocada(self):
        otra, _ = cgx.generar_par()
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "otra clave"):
            restauracion.restaurar(self.cgx_ruta, lambda e, s: cgx.descifrar_con_privada(e, s, otra, [self.fpub]),
                                   lambda i: True, destino=self.destino)

    def test_version_mas_nueva_se_rechaza(self):
        origen = base_con(os.path.join(self.dir, "o2", "c.sqlite"), ["claude:x"], True)
        origen.execute(f"PRAGMA user_version={almacen.VERSION_ESQUEMA + 1}")
        r = exportacion.hacer_copia(origen, self.carpeta, self.pub, self.fpriv, self.fpub)
        origen.close()
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "más nueva"):
            restauracion.restaurar(os.path.join(self.carpeta, r["archivo"]), self.descifrar, lambda i: True, destino=self.destino)
        self.assertFalse(os.path.exists(self.destino))

    def test_contenido_que_no_es_una_base_de_consumo(self):
        sal = io.BytesIO()
        cgx.cifrar_a_destinatario(io.BytesIO(b"no soy sqlite" * 100), 1300, sal, self.pub, self.fpriv)
        ruta = os.path.join(self.dir, "falsa.cgx")
        with open(ruta, "wb") as f:
            f.write(sal.getvalue())
        with self.assertRaisesRegex(restauracion.ErrorRestauracion, "no es una base"):
            restauracion.restaurar(ruta, self.descifrar, lambda i: True, destino=self.destino)
        self.assertFalse(os.path.exists(self.destino))

    def test_la_base_restaurada_se_abre_y_acepta_escritura(self):
        self.restaura()
        con = almacen.abrir(self.destino)
        q = almacen.identidad("barra")
        self.assertTrue(almacen.adquirir_escritura(con, q))
        con.close()


if __name__ == "__main__":
    unittest.main()
