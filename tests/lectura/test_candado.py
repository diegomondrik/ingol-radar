"""El candado de la memoria es un bloqueo del sistema operativo (msvcrt.locking / fcntl.flock).

Se libera solo cuando el proceso muere: no hay candados huérfanos ni nada que borrar a mano. El
archivo `<ruta>.lock` no se borra nunca.
Habilidad `fallas-y-concurrencia`: "dos procesos" son procesos de verdad; los hilos compiten con un
orden forzado por el propio bloqueo y plazos para que un candado que no se suelta no cuelgue la prueba.
"""

import errno
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from radar.lectura.modelos import Novedad
from radar.memoria import Memoria, MemoriaCorrupta, MemoriaError, MemoriaOcupada
from radar.memoria import memoria as modulo_memoria

RAIZ = Path(__file__).resolve().parents[2]
CUANDO = datetime(2026, 10, 10, tzinfo=timezone.utc)

HIJO_ABRE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from radar.memoria import Memoria, MemoriaOcupada\n"
    "try:\n"
    "    m = Memoria.abrir(sys.argv[2])\n"
    "except MemoriaOcupada:\n"
    "    print('OCUPADA')\n"
    "    sys.exit(3)\n"
    "print('ABIERTA')\n"
)

# Abre la memoria y NO la cierra: o se queda dormido hasta que lo matan, o termina de golpe sin limpiar.
HIJO_ABRE_Y_NO_CIERRA = (
    "import os, sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from radar.memoria import Memoria\n"
    "m = Memoria.abrir(sys.argv[2])\n"
    "print('LISTO', flush=True)\n"
    "if sys.argv[3] == 'dormir':\n"
    "    time.sleep(300)\n"
    "os._exit(7)\n"
)


def nov(clave: str) -> Novedad:
    return Novedad("openai", clave, "Titulo", f"https://ejemplo.test/{clave}", None, "frase")


def correr_hijo(codigo: str, ruta, *extra):
    return subprocess.run(
        [sys.executable, "-c", codigo, str(RAIZ), str(ruta), *extra], capture_output=True, text=True, timeout=60
    )


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = Path(self.dir.name) / "generated" / "memoria.json"
        self.candado = self.ruta.with_name("memoria.json.lock")

    def assertLibre(self):
        Memoria.abrir(self.ruta).cerrar()

    def assertTomada(self):
        with self.assertRaises(MemoriaOcupada):
            Memoria.abrir(self.ruta)


class DosProcesosTest(Base):
    def test_uno_con_la_memoria_abierta_y_el_otro_recibe_ocupada(self):
        with Memoria.abrir(self.ruta):
            hijo = correr_hijo(HIJO_ABRE, self.ruta)
            self.assertEqual((hijo.returncode, hijo.stdout.strip()), (3, "OCUPADA"), hijo.stderr)
        hijo = correr_hijo(HIJO_ABRE, self.ruta)  # ya cerrada: el otro puede
        self.assertEqual((hijo.returncode, hijo.stdout.strip()), (0, "ABIERTA"), hijo.stderr)

    def test_el_proceso_que_tiene_la_memoria_cuando_el_otro_termina_la_pierde_para_el_otro(self):
        hijo = correr_hijo(HIJO_ABRE, self.ruta)  # abre y termina sin cerrar: el sistema lo suelta
        self.assertEqual(hijo.stdout.strip(), "ABIERTA", hijo.stderr)
        self.assertLibre()

    def test_un_proceso_que_muere_con_la_memoria_abierta_no_deja_nada_que_borrar(self):
        hijo = subprocess.Popen(
            [sys.executable, "-c", HIJO_ABRE_Y_NO_CIERRA, str(RAIZ), str(self.ruta), "dormir"],
            stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        try:
            self.assertEqual(hijo.stdout.readline().strip(), "LISTO")
            self.assertTomada()  # mientras vive, nadie más entra
            hijo.kill()  # muere de golpe, sin limpiar nada
            hijo.wait(timeout=60)
        finally:
            if hijo.poll() is None:
                hijo.kill()
                hijo.wait(timeout=60)
            hijo.stdout.close()
        self.assertTrue(self.candado.exists())  # el archivo quedó ahí...
        with Memoria.abrir(self.ruta) as m:  # ...y la siguiente corrida abre sin intervención
            m.marcar([nov("a")], CUANDO)
            m.guardar()

    def test_un_proceso_que_termina_de_golpe_sin_cerrar(self):
        hijo = correr_hijo(HIJO_ABRE_Y_NO_CIERRA, self.ruta, "salir")
        self.assertEqual((hijo.returncode, hijo.stdout.strip()), (7, "LISTO"), hijo.stderr)
        self.assertLibre()


class ExclusionTest(Base):
    def test_un_segundo_abrir_en_el_mismo_proceso_es_ocupada_con_un_mensaje_que_no_manda_a_borrar(self):
        with Memoria.abrir(self.ruta):
            with self.assertRaises(MemoriaOcupada) as ctx:
                Memoria.abrir(self.ruta)
            mensaje = str(ctx.exception)
            self.assertIn(str(self.ruta), mensaje)
            self.assertIn("otra corrida", mensaje)
            self.assertIn("no hay que borrar nada", mensaje)
            self.assertNotIn("borrá el archivo", mensaje)
            self.assertTomada()  # el intento fallido no soltó el bloqueo de la primera
        self.assertLibre()

    def test_la_misma_ruta_escrita_de_otra_forma_es_la_misma_memoria(self):
        with Memoria.abrir(self.ruta):
            for forma in (
                self.ruta.parent / ".." / "generated" / "memoria.json",
                Path(os.path.relpath(self.ruta)) if os.name != "nt" or self.ruta.drive == Path.cwd().drive else self.ruta,
                self.ruta.parent / "." / "memoria.json",
            ):
                with self.subTest(str(forma)):
                    with self.assertRaises(MemoriaOcupada):
                        Memoria.abrir(forma)

    def test_el_sistema_operativo_excluye_aunque_el_registro_del_proceso_no_exista(self):
        with Memoria.abrir(self.ruta):
            fd = os.open(self.candado, os.O_RDWR | getattr(os, "O_BINARY", 0))
            try:
                with self.assertRaises(OSError) as ctx:
                    modulo_memoria._bloquear(fd)
                self.assertIn(ctx.exception.errno, modulo_memoria._OCUPADO)
            finally:
                os.close(fd)

    def test_el_registro_del_proceso_excluye_aunque_el_bloqueo_del_sistema_no_lo_haga(self):
        """Dentro de un proceso no se depende de cómo trate el sistema los bloqueos entre descriptores."""
        with mock.patch.object(modulo_memoria, "_bloquear", lambda fd: None):  # un sistema que no excluye
            with Memoria.abrir(self.ruta):
                with self.assertRaises(MemoriaOcupada):
                    Memoria.abrir(self.ruta)
            self.assertEqual(modulo_memoria._EN_USO, set())
            Memoria.abrir(self.ruta).cerrar()

    def test_cerrar_dos_veces_no_suelta_el_bloqueo_de_otra(self):
        primera = Memoria.abrir(self.ruta)
        primera.cerrar()
        segunda = Memoria.abrir(self.ruta)
        primera.cerrar()  # ya cerrada: no puede tocar el bloqueo de la segunda
        self.assertTomada()
        segunda.cerrar()
        self.assertLibre()

    def test_dos_instancias_en_orden_no_se_pisan(self):
        with Memoria.abrir(self.ruta) as a:
            a.marcar([nov("a")], CUANDO)
            self.assertTomada()
            a.guardar()
        with Memoria.abrir(self.ruta) as b:
            self.assertIn("a", b)

    def test_muchos_hilos_leen_modifican_y_guardan_sin_perder_ninguno(self):
        hilos, dentro, maximo, errores = 8, [0], [0], []
        cerrojo = threading.Lock()

        def trabajar(i):
            plazo = time.monotonic() + 20  # sin plazo, un bloqueo que no se suelta colgaría la prueba
            try:
                while True:
                    try:
                        memoria = Memoria.abrir(self.ruta)
                    except MemoriaOcupada:
                        if time.monotonic() > plazo:
                            errores.append(TimeoutError(f"el hilo {i} no consiguió turno"))
                            return
                        time.sleep(0.001)
                        continue
                    with memoria:
                        with cerrojo:
                            dentro[0] += 1
                            maximo[0] = max(maximo[0], dentro[0])
                        memoria.marcar([nov(f"hilo{i}")], CUANDO)
                        time.sleep(0.005)  # leer-modificar-escribir lento: la ventana donde otro se colaría
                        memoria.guardar()
                        with cerrojo:
                            dentro[0] -= 1
                    return
            except Exception as exc:  # noqa: BLE001
                errores.append(exc)

        lista = [threading.Thread(target=trabajar, args=(i,), daemon=True) for i in range(hilos)]
        for h in lista:
            h.start()
        for h in lista:
            h.join(40)
        self.assertEqual(errores, [])
        self.assertEqual(maximo[0], 1, "dos corridas tuvieron la memoria a la vez")
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), hilos)  # ninguna actualización se perdió

    def test_el_registro_del_proceso_queda_limpio_despues_de_cada_salida(self):
        casos = []
        with Memoria.abrir(self.ruta):
            pass
        casos.append("normal")
        with self.assertRaises(RuntimeError):
            with Memoria.abrir(self.ruta):
                raise RuntimeError
        casos.append("excepcion")
        self.ruta.write_bytes(b"basura")
        with self.assertRaises(MemoriaCorrupta):
            Memoria.abrir(self.ruta)
        casos.append("corrupta")
        self.assertEqual(modulo_memoria._EN_USO, set())
        self.assertEqual(len(casos), 3)


class SueltaTest(Base):
    def test_se_suelta_tras_una_excepcion_en_el_with(self):
        with self.assertRaises(RuntimeError):
            with Memoria.abrir(self.ruta):
                raise RuntimeError("falló el análisis")
        self.assertLibre()

    def test_se_suelta_tras_ctrl_c_dentro_del_with(self):
        with self.assertRaises(KeyboardInterrupt):
            with Memoria.abrir(self.ruta):
                raise KeyboardInterrupt
        self.assertLibre()

    def test_se_suelta_tras_ctrl_c_dentro_de_abrir(self):
        with mock.patch.object(Memoria, "_cargar", staticmethod(mock.Mock(side_effect=KeyboardInterrupt))):
            with self.assertRaises(KeyboardInterrupt):
                Memoria.abrir(self.ruta)
        self.assertLibre()
        self.assertEqual(modulo_memoria._EN_USO, set())

    def test_se_suelta_si_la_memoria_esta_corrupta(self):
        self.ruta.parent.mkdir(parents=True)
        self.ruta.write_bytes(b"basura")
        with self.assertRaises(MemoriaCorrupta):
            Memoria.abrir(self.ruta)
        fd = os.open(self.candado, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            modulo_memoria._bloquear(fd)  # si siguiera tomado, esto levantaría
        finally:
            os.close(fd)

    def test_se_suelta_si_guardar_falla(self):
        with self.assertRaises(OSError):
            with Memoria.abrir(self.ruta) as m:
                with mock.patch("os.replace", side_effect=OSError(errno.ENOSPC, "lleno")):
                    m.guardar()
        self.assertLibre()

    def test_el_archivo_de_candado_no_se_borra_nunca(self):
        with Memoria.abrir(self.ruta):
            pass
        self.assertTrue(self.candado.exists())
        with self.assertRaises(RuntimeError):
            with Memoria.abrir(self.ruta):
                raise RuntimeError
        self.assertTrue(self.candado.exists())
        self.assertEqual(self.candado.stat().st_size, 0)  # no se le escribe nada: su contenido no importa

    def test_un_lock_viejo_con_basura_adentro_no_impide_abrir(self):
        self.ruta.parent.mkdir(parents=True)
        for basura in (b"basura", b"pid=1234\n", b"pid=" + b"9" * 100, bytes(range(256)) * 4, b"x" * 1_000_000, b"\x00" * 10):
            with self.subTest(basura[:12]):
                self.candado.write_bytes(basura)
                with Memoria.abrir(self.ruta) as m:
                    self.assertEqual(len(m), 0)
                self.assertEqual(self.candado.read_bytes(), basura)  # y no se tocó

    def test_el_bloqueo_se_toma_sobre_el_byte_cero_aunque_el_archivo_este_vacio_o_sea_largo(self):
        self.ruta.parent.mkdir(parents=True)
        for contenido in (b"", b"a" * 5000):
            self.candado.write_bytes(contenido)
            with Memoria.abrir(self.ruta):
                self.assertTomada()
            self.assertLibre()


class ErroresDelBloqueoTest(Base):
    def abrir_con_bloqueo_que_falla(self, errno_):
        abiertos, cerrados = [], []
        real_open, real_close = os.open, os.close

        def abre(ruta, *a, **k):
            fd = real_open(ruta, *a, **k)
            if str(ruta).endswith(".lock"):
                abiertos.append(fd)
            return fd

        def cierra(fd):
            cerrados.append(fd)
            return real_close(fd)

        with mock.patch.object(modulo_memoria, "_bloquear", side_effect=OSError(errno_, "x")), mock.patch(
            "os.open", abre
        ), mock.patch("os.close", cierra):
            with self.assertRaises(MemoriaError) as ctx:
                Memoria.abrir(self.ruta)
        return ctx.exception, abiertos, cerrados

    def test_los_errores_de_ya_tomado_son_memoria_ocupada(self):
        for codigo in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK):
            with self.subTest(errno.errorcode.get(codigo)):
                exc, abiertos, cerrados = self.abrir_con_bloqueo_que_falla(codigo)
                self.assertIsInstance(exc, MemoriaOcupada)
                self.assertEqual(cerrados, abiertos)  # el descriptor se cerró: no se filtra
                self.assertEqual(modulo_memoria._EN_USO, set())

    def test_un_sistema_de_archivos_que_no_bloquea_no_es_ocupada_y_lo_explica(self):
        for codigo in (errno.ENOLCK, errno.ENOSYS, errno.EINVAL, errno.EIO):
            with self.subTest(errno.errorcode.get(codigo)):
                exc, abiertos, cerrados = self.abrir_con_bloqueo_que_falla(codigo)
                self.assertNotIsInstance(exc, MemoriaOcupada)
                self.assertIn("disco local", str(exc))
                self.assertIn("sincronizada", str(exc))
                self.assertEqual(cerrados, abiertos)
                self.assertEqual(modulo_memoria._EN_USO, set())

    def test_el_motivo_ocupada_depende_del_codigo_del_error_y_no_de_su_texto(self):
        self.assertIn(errno.EACCES, modulo_memoria._OCUPADO)
        self.assertIn(errno.EAGAIN, modulo_memoria._OCUPADO)
        self.assertNotIn(errno.ENOLCK, modulo_memoria._OCUPADO)


if __name__ == "__main__":
    unittest.main()
