"""Memoria: poda y tope de tamaño, bordes de la escritura y del candado, y el candado huérfano.

Candado huérfano: el candado guarda el pid de quien lo tiene. Si ese proceso ya no existe (corte,
reinicio), la corrida siguiente lo retira y sigue; el owner no tiene que borrar nada a mano. Si el
pid está vivo o el contenido no se entiende, es `MemoriaOcupada`, como siempre.
"""

import errno
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from radar.lectura.modelos import Novedad
from radar.memoria import Memoria, MemoriaDemasiadoGrande, MemoriaOcupada
from radar.memoria import memoria as modulo_memoria

HOY = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
CUANDO = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)


def nov(clave: str, titulo: str = "Titulo") -> Novedad:
    return Novedad("openai", clave, titulo, f"https://ejemplo.test/{clave}", None, "frase")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = Path(self.dir.name) / "generated" / "memoria.json"
        self.candado = self.ruta.with_name("memoria.json.lock")
        parche = mock.patch.object(modulo_memoria, "_ahora", return_value=HOY)
        parche.start()
        self.addCleanup(parche.stop)

    def carpeta(self):
        return sorted(p.name for p in self.ruta.parent.iterdir())

    def escribir_candado(self, contenido: bytes):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.candado.write_bytes(contenido)


# -------------------------------------------------------------------------- poda


class PodaTest(Base):
    def guardar_con(self, **marcas):
        """marcas: clave -> primera_vez. Guarda con el reloj fijo y devuelve cuántas podó."""
        with Memoria.abrir(self.ruta) as m:
            for clave, cuando in marcas.items():
                m.marcar([nov(clave)], cuando)
            return m.guardar()

    def claves(self):
        return sorted(json.loads(self.ruta.read_text(encoding="utf-8"))["vistas"])

    def test_se_borran_las_vistas_de_mas_de_365_dias_y_se_conserva_el_borde(self):
        hace = lambda **kw: HOY - timedelta(**kw)  # noqa: E731
        podadas = self.guardar_con(
            vieja=hace(days=365, seconds=1),  # más de 365 días: afuera
            borde=hace(days=365),  # exactamente 365 días: se queda
            casi=hace(days=364, hours=23),
            reciente=hace(days=1),
        )
        self.assertEqual(podadas, 1)
        self.assertEqual(self.claves(), ["borde", "casi", "reciente"])

    def test_la_poda_sale_en_el_siguiente_guardado_de_una_memoria_ya_en_disco(self):
        self.guardar_con(vieja=HOY - timedelta(days=400), nueva=HOY)
        # la primera guardada ya podó; sembramos a mano una vieja y se va en el siguiente
        datos = json.loads(self.ruta.read_text(encoding="utf-8"))
        datos["vistas"]["vieja2"] = {
            "fuente": "f", "titulo": "t", "enlace": "https://e.test/", "primera_vez": "2024-01-01T00:00:00+00:00",
        }  # fmt: skip
        self.ruta.write_text(json.dumps(datos), encoding="utf-8")
        with Memoria.abrir(self.ruta) as m:
            self.assertIn("vieja2", m)  # abrir no poda
            self.assertEqual(m.guardar(), 1)
        self.assertEqual(self.claves(), ["nueva"])

    def test_una_fecha_sin_zona_se_toma_como_utc(self):
        self.ruta.parent.mkdir(parents=True)
        datos = {"version": 1, "vistas": {
            "vieja": {"fuente": "f", "titulo": "t", "enlace": "https://e.test/", "primera_vez": "2025-01-01T00:00:00"},
            "nueva": {"fuente": "f", "titulo": "t", "enlace": "https://e.test/", "primera_vez": "2026-10-01T00:00:00"},
        }}  # fmt: skip
        self.ruta.write_text(json.dumps(datos), encoding="utf-8")
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(m.guardar(), 1)
        self.assertEqual(self.claves(), ["nueva"])

    def test_la_fecha_la_pasa_quien_llama_y_si_no_es_el_reloj(self):
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("a")], HOY)
            self.assertEqual(m.guardar(ahora=HOY + timedelta(days=366)), 1)  # explícita: gana
        self.assertEqual(self.claves(), [])

    def test_lo_podado_vuelve_a_salir_como_nuevo(self):
        self.guardar_con(a=HOY - timedelta(days=400))
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual([n.clave for n in m.nuevas([nov("a")])], ["a"])


# ----------------------------------------------------------- tope de tamaño al guardar


class TopeAlGuardarTest(Base):
    def sembrar(self, *claves):
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov(c) for c in claves], CUANDO)
            m.guardar()
        return self.ruta.read_bytes()

    def test_guardar_no_escribe_lo_que_abrir_no_leeria(self):
        original = self.sembrar("a")
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("b")], CUANDO)
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(original)):
                with self.assertRaises(MemoriaDemasiadoGrande) as ctx:
                    m.guardar()
            self.assertIn("No se escribió nada", str(ctx.exception))
            self.assertEqual(self.ruta.read_bytes(), original)  # el archivo anterior, intacto
            self.assertEqual(self.carpeta(), ["memoria.json", "memoria.json.lock"])  # sin temporales
        self.assertFalse(self.candado.exists())
        with Memoria.abrir(self.ruta) as m:  # y se sigue abriendo
            self.assertEqual(len(m), 1)

    def test_el_borde_es_estrictamente_mayor_que(self):
        original = self.sembrar("a")
        with Memoria.abrir(self.ruta) as m:
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(original)):
                m.guardar()  # exacto: se escribe
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(original) - 1):
                with self.assertRaises(MemoriaDemasiadoGrande):
                    m.guardar()

    def test_la_poda_va_antes_del_tope(self):
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("nueva")], HOY)
            m.guardar()
        chico = self.ruta.read_bytes()
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("vieja")], HOY - timedelta(days=500))
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(chico)):
                self.assertEqual(m.guardar(), 1)  # con la vieja no entraría; podada, sí
        self.assertEqual(self.ruta.read_bytes(), chico)


# -------------------------------------------------------------- bordes de escritura


class EscrituraTest(Base):
    def test_flush_antes_de_fsync(self):
        eventos = []

        class Archivo:
            def __init__(self, real):
                self._real = real

            def __enter__(self):
                self._real.__enter__()
                return self

            def __exit__(self, *exc):
                return self._real.__exit__(*exc)

            def write(self, datos):
                eventos.append("write")
                return self._real.write(datos)

            def flush(self):
                eventos.append("flush")
                return self._real.flush()

            def fileno(self):
                return self._real.fileno()

        real_fdopen, real_fsync = os.fdopen, os.fsync
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("a")], CUANDO)
            with mock.patch("os.fdopen", lambda fd, *a, **k: Archivo(real_fdopen(fd, *a, **k))), mock.patch(
                "os.fsync", lambda fd: (eventos.append("fsync"), real_fsync(fd))[1]
            ):
                m.guardar()
        self.assertEqual(eventos[:3], ["write", "flush", "fsync"])

    def test_guardar_sincroniza_el_directorio_despues_del_reemplazo(self):
        eventos = []
        real_replace = os.replace
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("a")], CUANDO)
            with mock.patch("os.replace", lambda a, b: (eventos.append("replace"), real_replace(a, b))[1]), mock.patch.object(
                Memoria, "_sincronizar_directorio", staticmethod(lambda d: eventos.append(("dir", d)))
            ):
                m.guardar()
        self.assertEqual(eventos, ["replace", ("dir", self.ruta.parent)])

    def test_sincronizar_directorio_en_posix_abre_sincroniza_y_cierra(self):
        llamadas = []
        with mock.patch.object(modulo_memoria, "_ES_WINDOWS", False), mock.patch(
            "os.open", lambda ruta, flags: (llamadas.append(("open", ruta, flags)), 77)[1]
        ), mock.patch("os.fsync", lambda fd: llamadas.append(("fsync", fd))), mock.patch(
            "os.close", lambda fd: llamadas.append(("close", fd))
        ):
            Memoria._sincronizar_directorio(self.ruta.parent)
        self.assertEqual(llamadas, [("open", self.ruta.parent, os.O_RDONLY), ("fsync", 77), ("close", 77)])

    def test_sincronizar_directorio_en_windows_no_hace_nada(self):
        with mock.patch.object(modulo_memoria, "_ES_WINDOWS", True), mock.patch(
            "os.open", side_effect=AssertionError("no debía abrir el directorio")
        ), mock.patch("os.fsync", side_effect=AssertionError("no debía sincronizar")):
            Memoria._sincronizar_directorio(self.ruta.parent)

    def test_un_error_al_sincronizar_el_directorio_no_hace_fallar_el_guardado(self):
        for roto in ("os.open", "os.fsync"):
            with self.subTest(roto):
                cierres = []
                with mock.patch.object(modulo_memoria, "_ES_WINDOWS", False), mock.patch(
                    "os.open", lambda *a: 77
                ), mock.patch("os.fsync", side_effect=OSError(errno.EIO, "io")) if roto == "os.fsync" else mock.patch(
                    "os.open", side_effect=OSError(errno.EACCES, "no")
                ), mock.patch("os.close", lambda fd: cierres.append(fd)):
                    Memoria._sincronizar_directorio(self.ruta.parent)  # no levanta
                if roto == "os.fsync":
                    self.assertEqual(cierres, [77])  # y el descriptor se cierra


# ---------------------------------------------------------------- bordes del candado


class CandadoBordesTest(Base):
    def test_ctrl_c_dentro_de_abrir_suelta_el_candado(self):
        with mock.patch.object(Memoria, "_cargar", staticmethod(mock.Mock(side_effect=KeyboardInterrupt))):
            with self.assertRaises(KeyboardInterrupt):
                Memoria.abrir(self.ruta)
        self.assertFalse(self.candado.exists())
        with Memoria.abrir(self.ruta):
            pass

    def test_un_error_al_escribir_el_pid_suelta_el_candado(self):
        with mock.patch("os.write", side_effect=OSError(errno.ENOSPC, "lleno")):
            with self.assertRaises(OSError):
                Memoria.abrir(self.ruta)
        self.assertFalse(self.candado.exists())

    def test_en_windows_un_acceso_denegado_pasajero_se_reintenta(self):
        intentos = []

        def os_open(ruta, flags, modo=0o777):
            intentos.append(ruta)
            if len(intentos) <= 3:
                raise PermissionError(errno.EACCES, "borrado pendiente")
            return 55

        with mock.patch.object(modulo_memoria, "_ES_WINDOWS", True), mock.patch("os.open", os_open), mock.patch(
            "time.sleep"
        ) as dormir:
            fd = Memoria._crear_candado(self.candado)
        self.assertEqual((fd, len(intentos)), (55, 4))
        self.assertEqual(dormir.call_count, 3)

    def test_en_windows_un_acceso_denegado_que_no_pasa_es_ocupada_si_el_candado_existe_y_error_si_no(self):
        with mock.patch.object(modulo_memoria, "_ES_WINDOWS", True), mock.patch(
            "os.open", side_effect=PermissionError(errno.EACCES, "no")
        ) as abrir, mock.patch("time.sleep"):
            with mock.patch("os.path.lexists", return_value=False):
                with self.assertRaises(PermissionError):
                    Memoria._crear_candado(self.candado)
            self.assertEqual(abrir.call_count, 20)
            with mock.patch("os.path.lexists", return_value=True):
                with self.assertRaises(FileExistsError):
                    Memoria._crear_candado(self.candado)

    def test_fuera_de_windows_el_acceso_denegado_no_se_reintenta(self):
        with mock.patch.object(modulo_memoria, "_ES_WINDOWS", False), mock.patch(
            "os.open", side_effect=PermissionError(errno.EACCES, "no")
        ) as abrir, mock.patch("os.path.lexists", return_value=False):
            with self.assertRaises(PermissionError):
                Memoria._crear_candado(self.candado)
        self.assertEqual(abrir.call_count, 1)

    def test_soltar_en_windows_reintenta_si_otra_corrida_lo_esta_leyendo(self):
        llamadas = []

        def desvincular(ruta):
            llamadas.append(ruta)
            if len(llamadas) <= 2:
                raise PermissionError(errno.EACCES, "en uso")

        with mock.patch.object(modulo_memoria, "_ES_WINDOWS", True), mock.patch("os.unlink", desvincular), mock.patch(
            "time.sleep"
        ):
            Memoria._soltar(self.candado)
        self.assertEqual(len(llamadas), 3)
        with mock.patch.object(modulo_memoria, "_ES_WINDOWS", True), mock.patch(
            "os.unlink", side_effect=PermissionError(errno.EACCES, "en uso")
        ) as desv, mock.patch("time.sleep"):
            with self.assertRaises(PermissionError):
                Memoria._soltar(self.candado)
        self.assertEqual(desv.call_count, 100)
        with mock.patch.object(modulo_memoria, "_ES_WINDOWS", False), mock.patch(
            "os.unlink", side_effect=PermissionError(errno.EACCES, "no")
        ) as desv:
            with self.assertRaises(PermissionError):
                Memoria._soltar(self.candado)
        self.assertEqual(desv.call_count, 1)


# ---------------------------------------------------------------- candado huérfano


def pid_de_un_proceso_terminado() -> int:
    hijo = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, timeout=60)
    return int(hijo.stdout.strip())


class HuerfanoTest(Base):
    def test_un_candado_de_un_proceso_que_termino_se_toma(self):
        muerto = pid_de_un_proceso_terminado()
        self.escribir_candado(f"pid={muerto}\n".encode())
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(self.candado.read_bytes(), f"pid={os.getpid()}\n".encode())  # ahora es nuestro
            m.marcar([nov("a")], CUANDO)
            m.guardar()
        self.assertEqual(self.carpeta(), ["memoria.json"])  # sin candado ni restos del retiro
        with Memoria.abrir(self.ruta) as m:
            self.assertIn("a", m)

    def test_el_huerfano_se_toma_aunque_haya_memoria_guardada(self):
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("a")], CUANDO)
            m.guardar()
        self.escribir_candado(f"pid={pid_de_un_proceso_terminado()}".encode())  # sin salto final
        with Memoria.abrir(self.ruta) as m:
            self.assertIn("a", m)

    def test_con_el_pid_propio_es_memoria_ocupada(self):
        contenido = f"pid={os.getpid()}\n".encode()
        self.escribir_candado(contenido)
        with self.assertRaises(MemoriaOcupada) as ctx:
            Memoria.abrir(self.ruta)
        self.assertIn(str(self.candado), str(ctx.exception))
        self.assertEqual(self.candado.read_bytes(), contenido)

    def test_con_el_pid_de_otro_proceso_vivo_es_memoria_ocupada(self):
        vivo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            contenido = f"pid={vivo.pid}\n".encode()
            self.escribir_candado(contenido)
            with self.assertRaises(MemoriaOcupada):
                Memoria.abrir(self.ruta)
            self.assertEqual(self.candado.read_bytes(), contenido)
        finally:
            vivo.kill()
            vivo.wait(timeout=30)
        with Memoria.abrir(self.ruta):  # ahora que murió, el mismo candado ya es huérfano
            pass

    def test_un_contenido_ilegible_es_memoria_ocupada_y_no_se_toca(self):
        ilegibles = [
            b"",
            b"basura",
            b"pid=",
            b"pid=abc",
            b"pid=-5",
            b"pid=0",
            b"pid=00",
            b"pid= 12",
            b"pid=12 34",
            b"pid=1\n\n",
            b"PID=1234\n",
            b"pid=1234\r\n",
            b"pid=99999999999999999999",
            b"pid=4294967296",  # una más que el máximo de un DWORD
            b"pid=" + b"1" * 100,
            b"pid=1234\n" + b"x" * 200,
            bytes(range(256)),
            "pid=١٢٣".encode(),  # cifras arábigo-índicas
        ]
        for contenido in ilegibles:
            with self.subTest(contenido[:24]):
                self.escribir_candado(contenido)
                with self.assertRaises(MemoriaOcupada):
                    Memoria.abrir(self.ruta)
                self.assertEqual(self.candado.read_bytes(), contenido)
                self.assertEqual(self.carpeta(), ["memoria.json.lock"])
                self.candado.unlink()

    def test_si_otra_corrida_retoma_el_candado_mientras_se_juzga_se_lo_devuelve_y_se_respeta(self):
        muerto = pid_de_un_proceso_terminado()
        self.escribir_candado(f"pid={muerto}\n".encode())
        nuevo = f"pid={os.getpid()}\n".encode()

        def juzgar(pid):
            self.candado.write_bytes(nuevo)  # otra corrida lo toma justo ahora...
            return False  # ...pero nosotros ya habíamos leído el pid muerto y lo damos por huérfano

        with mock.patch.object(modulo_memoria, "_proceso_vivo", juzgar):
            with self.assertRaises(MemoriaOcupada):
                Memoria.abrir(self.ruta)
        self.assertEqual(self.candado.read_bytes(), nuevo)  # el candado de la otra corrida sigue en su lugar
        self.assertEqual(self.carpeta(), ["memoria.json.lock"])  # y no quedaron restos del retiro

    def test_si_el_candado_desaparece_mientras_se_juzga_se_vuelve_a_intentar(self):
        muerto = pid_de_un_proceso_terminado()
        self.escribir_candado(f"pid={muerto}\n".encode())

        def juzgar(pid):
            self.candado.unlink()  # lo soltaron (o lo retiró otra) justo ahora
            return False

        with mock.patch.object(modulo_memoria, "_proceso_vivo", juzgar):
            with Memoria.abrir(self.ruta):
                self.assertEqual(self.candado.read_bytes(), f"pid={os.getpid()}\n".encode())

    def test_varias_corridas_ven_el_mismo_huerfano_y_sólo_una_a_la_vez_tiene_la_memoria(self):
        self.escribir_candado(f"pid={pid_de_un_proceso_terminado()}\n".encode())
        hilos, dentro, maximo, errores = 6, [0], [0], []
        cerrojo = threading.Lock()

        def trabajar(i):
            plazo = time.monotonic() + 20
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
                        time.sleep(0.003)
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
        self.assertEqual(self.carpeta(), ["memoria.json"])


class ProcesoVivoTest(unittest.TestCase):
    def test_posix_usa_os_kill_con_la_senal_cero_y_distingue_los_casos(self):
        casos = [
            (ProcessLookupError(), False),
            (PermissionError(errno.EPERM, "no es tuyo"), True),
            (OSError(errno.EINVAL, "raro"), True),
            (OverflowError("pid enorme"), True),
            (None, True),
        ]
        for efecto, vivo in casos:
            with self.subTest(repr(efecto)):
                with mock.patch.object(modulo_memoria, "_ES_WINDOWS", False), mock.patch(
                    "os.kill", side_effect=efecto
                ) as kill:
                    self.assertEqual(modulo_memoria._proceso_vivo(4321), vivo)
                kill.assert_called_once_with(4321, 0)  # la señal 0 sólo comprueba, no mata

    @unittest.skipUnless(os.name == "nt", "usa la API de Windows")
    def test_windows_sin_os_kill(self):
        muerto = pid_de_un_proceso_terminado()
        vivo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            with mock.patch("os.kill", side_effect=AssertionError("en Windows os.kill termina el proceso")):
                self.assertTrue(modulo_memoria._proceso_vivo(os.getpid()))
                self.assertTrue(modulo_memoria._proceso_vivo(vivo.pid))
                self.assertFalse(modulo_memoria._proceso_vivo(muerto))
        finally:
            vivo.kill()
            vivo.wait(timeout=30)
        self.assertEqual(vivo.poll() is not None, True)  # el proceso de prueba sí lo matamos nosotros

    @unittest.skipUnless(os.name == "nt", "usa la API de Windows")
    def test_windows_un_proceso_vivo_no_resulta_muerto_por_haberlo_consultado(self):
        vivo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            for _ in range(3):
                self.assertTrue(modulo_memoria._proceso_vivo(vivo.pid))
            self.assertIsNone(vivo.poll())  # sigue andando: consultar no lo termina
        finally:
            vivo.kill()
            vivo.wait(timeout=30)

    def test_pid_de(self):
        self.assertEqual(modulo_memoria._pid_de(b"pid=1234\n"), 1234)
        self.assertEqual(modulo_memoria._pid_de(b"pid=1234"), 1234)
        self.assertEqual(modulo_memoria._pid_de(b"pid=4294967295\n"), 4294967295)
        for malo in (b"", b"pid=0\n", b"pid=4294967296", b"pid=1234\n\n", b"x"):
            self.assertIsNone(modulo_memoria._pid_de(malo))


if __name__ == "__main__":
    unittest.main()
