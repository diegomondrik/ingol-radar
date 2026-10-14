"""La memoria de lo ya visto: uso normal, archivo corrupto, fallas inyectadas y concurrencia.

Habilidades aplicadas: `fallas-y-concurrencia` (falla forzada en cada frontera de escritura de
`guardar`; el orden de los accesos se fuerza con candados y no con la suerte; "dos procesos" son
dos instancias independientes y, una vez, dos procesos de verdad) y `entradas-adversariales`
(el archivo de la memoria también es una entrada: puede estar roto, ser de otra versión o hostil).
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
from radar.memoria import (
    RUTA_POR_DEFECTO,
    Memoria,
    MemoriaCorrupta,
    MemoriaDemasiadoGrande,
    MemoriaError,
    MemoriaOcupada,
)
from radar.memoria import memoria as modulo_memoria

RAIZ = Path(__file__).resolve().parents[2]
CUANDO = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)
HOY = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)  # el reloj fijo de las pruebas (poda)


def nov(clave: str, titulo: str = "Titulo", fuente: str = "openai") -> Novedad:
    return Novedad(fuente, clave, titulo, f"https://ejemplo.test/{clave}", None, "frase")


class Corte(BaseException):
    """Simula que el proceso muere: no es una Exception, ningún `except Exception` lo atrapa."""


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.carpeta = Path(self.dir.name)
        self.ruta = self.carpeta / "generated" / "memoria.json"
        self.candado = self.ruta.with_name("memoria.json.lock")
        # La poda mira "ahora": con el reloj fijo, las pruebas no dependen del día en que corren.
        parche = mock.patch.object(modulo_memoria, "_ahora", return_value=HOY)
        parche.start()
        self.addCleanup(parche.stop)

    def sembrar(self, *claves: str) -> bytes:
        """Deja una memoria guardada con esas claves y devuelve sus bytes."""
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov(c) for c in claves], CUANDO)
            m.guardar()
        return self.ruta.read_bytes()

    def contenido_de_la_carpeta(self):
        return sorted(p.name for p in self.ruta.parent.iterdir())


# ---------------------------------------------------------------------------- uso


class UsoTest(Base):
    def test_ruta_por_defecto_esta_anclada_a_la_raiz_del_repo_y_no_al_directorio_de_trabajo(self):
        self.assertTrue(RUTA_POR_DEFECTO.is_absolute())
        self.assertEqual(RUTA_POR_DEFECTO, RAIZ / "generated" / "memoria.json")
        anterior = os.getcwd()
        with tempfile.TemporaryDirectory() as otro:
            os.chdir(otro)
            try:  # desde otro directorio de trabajo, la ruta por defecto sigue siendo la misma
                self.assertEqual(modulo_memoria.RUTA_POR_DEFECTO, RAIZ / "generated" / "memoria.json")
                self.assertTrue(modulo_memoria.RUTA_POR_DEFECTO.is_absolute())
            finally:
                os.chdir(anterior)

    def test_archivo_que_falta_es_memoria_vacia_y_crea_la_carpeta(self):
        self.assertFalse(self.ruta.parent.exists())
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), 0)
            self.assertEqual([n.clave for n in m.nuevas([nov("a")])], ["a"])
        self.assertFalse(self.ruta.exists())  # abrir no escribe la memoria: sólo guardar

    def test_nuevas_saca_las_vistas_y_las_repetidas_de_la_misma_tanda(self):
        self.sembrar("a", "b")
        with Memoria.abrir(self.ruta) as m:
            tanda = [nov("b"), nov("c"), nov("a"), nov("c", "otro titulo"), nov("d")]
            self.assertEqual([n.clave for n in m.nuevas(tanda)], ["c", "d"])
            self.assertEqual(len(m), 2)  # nuevas no marca nada

    def test_marcar_y_guardar_escriben_la_forma_pedida(self):
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(m.marcar([nov("a", "Árbol ñandú"), nov("a")], CUANDO), 1)
            m.guardar()
        datos = json.loads(self.ruta.read_text(encoding="utf-8"))
        self.assertEqual(
            datos,
            {
                "version": 1,
                "vistas": {
                    "a": {
                        "fuente": "openai",
                        "titulo": "Árbol ñandú",
                        "enlace": "https://ejemplo.test/a",
                        "primera_vez": "2026-10-10T08:00:00+00:00",
                    }
                },
            },
        )

    def test_la_memoria_guardada_se_relee_igual(self):
        self.sembrar("a", "b", "c")
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), 3)
            self.assertIn("b", m)
            self.assertNotIn("z", m)
            self.assertEqual(m.nuevas([nov("a"), nov("z")])[0].clave, "z")

    def test_marcar_pide_zona_horaria_y_la_pasa_a_utc(self):
        with Memoria.abrir(self.ruta) as m:
            with self.assertRaises(ValueError):
                m.marcar([nov("a")], datetime(2026, 10, 10, 8, 0))
            m.marcar([nov("a")], datetime(2026, 10, 10, 8, 0, tzinfo=timezone(timedelta(hours=-3))))
            m.guardar()
        self.assertEqual(json.loads(self.ruta.read_text())["vistas"]["a"]["primera_vez"], "2026-10-10T11:00:00+00:00")

    def test_el_archivo_guardado_termina_en_salto_de_linea_lf_y_sin_restos(self):
        self.sembrar("a")
        crudo = self.ruta.read_bytes()
        self.assertTrue(crudo.endswith(b"}\n"))
        self.assertNotIn(b"\r", crudo)
        self.assertEqual(self.contenido_de_la_carpeta(), ["memoria.json"])  # ni candado ni temporales

    def test_usar_la_memoria_cerrada_para_guardar_es_un_error(self):
        with Memoria.abrir(self.ruta) as m:
            pass
        with self.assertRaises(MemoriaError):
            m.guardar()


# ------------------------------------------------------------------------ corrupta


def vista_valida(**cambios):
    entrada = {"fuente": "f", "titulo": "t", "enlace": "https://e.test/", "primera_vez": "2026-10-10T08:00:00+00:00"}
    entrada.update(cambios)
    return entrada


ARCHIVOS_ROTOS = {
    "vacio": b"",
    "solo espacios": b"  \n",
    "no es json": b"esto no es json",
    "json cortado": b'{"version": 1, "vistas": {"a": ',
    "binario": bytes(range(256)),
    "utf-16 con BOM": '{"version": 1, "vistas": {}}'.encode("utf-16"),
    "utf-8 invalido": b'{"version": 1, "vistas": {"a\xff": {}}}',
    "BOM doble": b'\xef\xbb\xbf\xef\xbb\xbf{"version": 1, "vistas": {}}',
    "clave repetida en la raiz": b'{"version": 1, "vistas": {}, "version": 1}',
    "vistas repetidas": b'{"version": 1, "vistas": {}, "vistas": {}}',
    "entrada repetida": (
        b'{"version": 1, "vistas": {"a": {"fuente": "f", "titulo": "t", "enlace": "e", '
        b'"primera_vez": "2026-10-10T08:00:00+00:00"}, "a": {"fuente": "f", "titulo": "t", '
        b'"enlace": "e", "primera_vez": "2026-10-10T08:00:00+00:00"}}}'
    ),
    "campo repetido en una entrada": (
        b'{"version": 1, "vistas": {"a": {"fuente": "f", "fuente": "g", "titulo": "t", '
        b'"enlace": "e", "primera_vez": "2026-10-10T08:00:00+00:00"}}}'
    ),
    "NaN": b'{"version": NaN, "vistas": {}}',
    "lista en la raiz": b"[]",
    "null": b"null",
    "texto": b'"hola"',
    "sin version": b'{"vistas": {}}',
    "sin vistas": b'{"version": 1}',
    "version 2": b'{"version": 2, "vistas": {}}',
    "version como texto": b'{"version": "1", "vistas": {}}',
    "version booleana": b'{"version": true, "vistas": {}}',
    "version decimal": b'{"version": 1.0, "vistas": {}}',
    "version null": b'{"version": null, "vistas": {}}',
    "campo extra en la raiz": b'{"version": 1, "vistas": {}, "extra": 1}',
    "vistas es lista": b'{"version": 1, "vistas": []}',
    "vistas es null": b'{"version": 1, "vistas": null}',
    "entrada no es objeto": b'{"version": 1, "vistas": {"a": "x"}}',
    "entrada vacia": b'{"version": 1, "vistas": {"a": {}}}',
    "entrada con campo de menos": json.dumps(
        {"version": 1, "vistas": {"a": {k: v for k, v in vista_valida().items() if k != "enlace"}}}
    ).encode(),
    "entrada con campo de mas": json.dumps({"version": 1, "vistas": {"a": vista_valida(extra="x")}}).encode(),
    "campo que no es texto": json.dumps({"version": 1, "vistas": {"a": vista_valida(titulo=5)}}).encode(),
    "campo null": json.dumps({"version": 1, "vistas": {"a": vista_valida(enlace=None)}}).encode(),
    "fecha ilegible": json.dumps({"version": 1, "vistas": {"a": vista_valida(primera_vez="ayer")}}).encode(),
    "anidamiento profundo": b"[" * 100000 + b"]" * 100000,
}


class CorruptaTest(Base):
    def preparar(self, contenido: bytes):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta.write_bytes(contenido)

    def test_cada_forma_rota_se_rechaza_con_ruta_y_motivo_y_el_archivo_queda_igual(self):
        for nombre, contenido in ARCHIVOS_ROTOS.items():
            with self.subTest(nombre):
                self.preparar(contenido)
                with self.assertRaises(MemoriaCorrupta) as ctx:
                    Memoria.abrir(self.ruta)
                mensaje = str(ctx.exception)
                self.assertIn(str(self.ruta), mensaje)
                self.assertIn("corrupta", mensaje)
                self.assertEqual(self.ruta.read_bytes(), contenido)  # ni reseteado ni pisado
                self.assertFalse(self.candado.exists(), "el candado quedó tomado")
                self.assertEqual(self.contenido_de_la_carpeta(), ["memoria.json"])

    def test_el_cuerpo_del_with_no_corre_con_una_memoria_corrupta(self):
        self.preparar(b"basura")
        corrio = []
        with self.assertRaises(MemoriaCorrupta):
            with Memoria.abrir(self.ruta):
                corrio.append(True)
        self.assertEqual(corrio, [])

    def test_reintentar_no_la_arregla_en_silencio(self):
        self.preparar(b"basura")
        for _ in range(3):
            with self.assertRaises(MemoriaCorrupta):
                Memoria.abrir(self.ruta)
        self.assertEqual(self.ruta.read_bytes(), b"basura")

    def test_la_memoria_con_bom_utf8_se_acepta_como_forma_equivalente(self):
        self.preparar(b'\xef\xbb\xbf{"version": 1, "vistas": {}}')
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), 0)
            m.marcar([nov("a")], CUANDO)
            m.guardar()
        self.assertFalse(self.ruta.read_bytes().startswith(b"\xef\xbb\xbf"))  # al guardar se reescribe sin BOM
        with Memoria.abrir(self.ruta) as m:
            self.assertIn("a", m)

    def test_archivo_demasiado_grande_no_es_corrupto(self):
        self.sembrar("a")
        original = self.ruta.read_bytes()
        with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", 10):
            with self.assertRaises(MemoriaDemasiadoGrande) as ctx:
                Memoria.abrir(self.ruta)
        self.assertNotIsInstance(ctx.exception, MemoriaCorrupta)
        self.assertIn("No se tocó", str(ctx.exception))
        self.assertEqual(self.ruta.read_bytes(), original)
        self.assertFalse(self.candado.exists())

    def test_el_tope_de_tamano_es_estrictamente_mayor_que(self):
        original = self.sembrar("a")
        with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(original)):  # exacto: se abre
            with Memoria.abrir(self.ruta) as m:
                self.assertEqual(len(m), 1)
        with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(original) - 1):  # un byte menos: no
            with self.assertRaises(MemoriaDemasiadoGrande):
                Memoria.abrir(self.ruta)

    def test_el_mensaje_de_corrupta_dice_que_no_se_toco(self):
        self.preparar(b"basura")
        with self.assertRaises(MemoriaCorrupta) as ctx:
            Memoria.abrir(self.ruta)
        self.assertIn("No se tocó", str(ctx.exception))


# ------------------------------------------------------------------------- fallas


class Mitad:
    """Un archivo cuya escritura queda a medias y falla con disco lleno."""

    def __init__(self, archivo):
        self._archivo = archivo

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._archivo.close()
        return False

    def write(self, datos):
        self._archivo.write(datos[: len(datos) // 2])
        self._archivo.flush()
        raise OSError(errno.ENOSPC, "disco lleno")

    def __getattr__(self, nombre):
        return getattr(self._archivo, nombre)


def fdopen_a_medias(real):
    return lambda fd, *args, **kwargs: Mitad(real(fd, *args, **kwargs))


def disco_lleno(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "disco lleno")


class FallasTest(Base):
    """Una falla en cada frontera de escritura de `guardar`: crear el temporal, escribir,
    flush, fsync y reemplazar. Después de cada una: el archivo anterior intacto byte a byte,
    sin temporales sueltos y la memoria sigue abriéndose y guardándose."""

    FRONTERAS = {
        "crear el temporal": lambda: mock.patch("tempfile.mkstemp", side_effect=disco_lleno),
        "escribir (a medias)": lambda: mock.patch("os.fdopen", side_effect=fdopen_a_medias(os.fdopen)),
        "fsync": lambda: mock.patch("os.fsync", side_effect=disco_lleno),
        "reemplazar": lambda: mock.patch("os.replace", side_effect=disco_lleno),
    }

    def probar(self, parche, excepcion=OSError):
        original = self.sembrar("a", "b")
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("c")], CUANDO)
            with parche():
                with self.assertRaises(excepcion):
                    m.guardar()
            self.assertEqual(self.ruta.read_bytes(), original)
            # nada a medias ni temporales sueltos (la carpeta sólo tiene la memoria y el candado)
            self.assertEqual(self.contenido_de_la_carpeta(), ["memoria.json", "memoria.json.lock"])
            m.guardar()  # una falla no envenena la memoria: el reintento anda
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), 3)

    def test_error_de_disco_en_cada_frontera_deja_el_archivo_anterior_intacto(self):
        for nombre, parche in self.FRONTERAS.items():
            with self.subTest(nombre):
                self.probar(parche)

    def test_corte_del_proceso_en_cada_frontera_deja_el_archivo_anterior_intacto(self):
        cortes = {
            "fsync": lambda: mock.patch("os.fsync", side_effect=Corte()),
            "reemplazar": lambda: mock.patch("os.replace", side_effect=Corte()),
            "escribir": lambda: mock.patch("os.fdopen", side_effect=Corte()),
        }
        for nombre, parche in cortes.items():
            with self.subTest(nombre):
                self.probar(parche, excepcion=Corte)

    def test_la_primera_escritura_que_falla_no_deja_archivo_a_medias(self):
        for nombre, parche in self.FRONTERAS.items():
            with self.subTest(nombre):
                self.ruta.unlink(missing_ok=True)
                with Memoria.abrir(self.ruta) as m:
                    m.marcar([nov("a")], CUANDO)
                    with parche():
                        with self.assertRaises(OSError):
                            m.guardar()
                self.assertFalse(self.ruta.exists())
                with Memoria.abrir(self.ruta) as m:
                    self.assertEqual(len(m), 0)

    def test_un_temporal_a_medias_de_una_corrida_muerta_no_molesta(self):
        original = self.sembrar("a")
        (self.ruta.parent / "memoria.json.k3j2h1.tmp").write_bytes(original[: len(original) // 2])
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), 1)
            m.marcar([nov("b")], CUANDO)
            m.guardar()
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), 2)

    def test_los_temporales_tienen_nombre_distinto_aunque_el_reloj_este_fijo(self):
        nombres = []

        def captura(origen, destino):
            nombres.append(origen)
            raise OSError(errno.ENOSPC, "disco lleno")

        self.sembrar("a")
        with Memoria.abrir(self.ruta) as m, mock.patch("time.time", return_value=1.0), mock.patch(
            "os.replace", side_effect=captura
        ):
            for _ in range(2):
                with self.assertRaises(OSError):
                    m.guardar()
        self.assertEqual(len(nombres), 2)
        self.assertNotEqual(nombres[0], nombres[1])
        self.assertEqual(os.path.dirname(nombres[0]), str(self.ruta.parent))  # mismo directorio: el reemplazo es atómico

    def test_el_temporal_se_escribe_completo_y_con_fsync_antes_de_reemplazar(self):
        eventos = []
        real_fsync, real_replace = os.fsync, os.replace
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("a")], CUANDO)
            with mock.patch("os.fsync", side_effect=lambda fd: (eventos.append("fsync"), real_fsync(fd))[1]), mock.patch(
                "os.replace", side_effect=lambda a, b: (eventos.append(("replace", Path(a).read_bytes())), real_replace(a, b))[1]
            ):
                m.guardar()
        self.assertEqual(eventos[0], "fsync")
        self.assertEqual(eventos[1][0], "replace")
        self.assertEqual(json.loads(eventos[1][1])["vistas"].keys(), {"a"})  # lo que se reemplaza ya está entero


# ---------------------------------------------------------------------- concurrencia


class ConcurrenciaTest(Base):
    def test_un_segundo_abrir_con_el_candado_tomado_es_memoria_ocupada(self):
        with Memoria.abrir(self.ruta) as primera:
            with self.assertRaises(MemoriaOcupada) as ctx:
                Memoria.abrir(self.ruta)
            mensaje = str(ctx.exception)
            self.assertIn(str(self.candado), mensaje)  # dice qué archivo borrar
            self.assertIn("borrá", mensaje)
            self.assertIn("caída", mensaje)
            # el intento fallido no se llevó el candado de la primera
            self.assertTrue(self.candado.exists())
            with self.assertRaises(MemoriaOcupada):
                Memoria.abrir(self.ruta)
            primera.marcar([nov("a")], CUANDO)
            primera.guardar()
        self.assertFalse(self.candado.exists())
        with Memoria.abrir(self.ruta) as m:  # liberada: ahora sí
            self.assertEqual(len(m), 1)

    def test_un_candado_ilegible_no_se_roba_y_el_mensaje_dice_que_borrar(self):
        self.ruta.parent.mkdir(parents=True)
        self.candado.write_text("lo escribio otro programa\n")
        with self.assertRaises(MemoriaOcupada) as ctx:
            Memoria.abrir(self.ruta)
        self.assertIn(str(self.candado), str(ctx.exception))
        self.assertTrue(self.candado.exists())
        self.assertEqual(self.candado.read_text(), "lo escribio otro programa\n")
        self.candado.unlink()  # el usuario hace lo que dice el mensaje
        with Memoria.abrir(self.ruta):
            pass

    def test_el_candado_se_suelta_despues_de_una_excepcion(self):
        with self.assertRaises(RuntimeError):
            with Memoria.abrir(self.ruta) as m:
                m.marcar([nov("a")], CUANDO)
                raise RuntimeError("falló el análisis")
        self.assertFalse(self.candado.exists())
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), 0)  # lo no guardado no se guardó

    def test_el_candado_se_suelta_tambien_con_ctrl_c_y_con_falla_al_guardar(self):
        with self.assertRaises(KeyboardInterrupt):
            with Memoria.abrir(self.ruta):
                raise KeyboardInterrupt
        self.assertFalse(self.candado.exists())
        with self.assertRaises(OSError):
            with Memoria.abrir(self.ruta) as m:
                with mock.patch("os.replace", side_effect=disco_lleno):
                    m.guardar()
        self.assertFalse(self.candado.exists())

    def test_cerrar_dos_veces_no_borra_el_candado_de_otro(self):
        m = Memoria.abrir(self.ruta)
        m.cerrar()
        otra = Memoria.abrir(self.ruta)
        m.cerrar()  # la primera, ya cerrada, no puede soltar el candado de la segunda
        self.assertTrue(self.candado.exists())
        otra.cerrar()
        self.assertFalse(self.candado.exists())

    def test_dos_instancias_en_orden_no_se_pisan(self):
        with Memoria.abrir(self.ruta) as a:
            a.marcar([nov("a")], CUANDO)
            with self.assertRaises(MemoriaOcupada):
                Memoria.abrir(self.ruta)  # la otra corrida espera su turno
            a.guardar()
        with Memoria.abrir(self.ruta) as b:
            self.assertIn("a", b)  # ve lo de la primera
            b.marcar([nov("b")], CUANDO)
            b.guardar()
        with Memoria.abrir(self.ruta) as c:
            self.assertEqual(sorted(c._vistas), ["a", "b"])

    def test_muchos_hilos_leen_modifican_y_guardan_sin_perder_ninguno(self):
        hilos, dentro, maximo, errores = 8, [0], [0], []
        trabado = threading.Lock()

        def trabajar(i):
            plazo = time.monotonic() + 15  # sin plazo, un candado que nunca se suelta colgaría la prueba
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
                        with trabado:
                            dentro[0] += 1
                            maximo[0] = max(maximo[0], dentro[0])
                        memoria.marcar([nov(f"hilo{i}")], CUANDO)
                        time.sleep(0.005)  # un lector-modificador lento: la ventana donde otro se colaría
                        memoria.guardar()
                        with trabado:
                            dentro[0] -= 1
                    return
            except Exception as exc:  # noqa: BLE001
                errores.append(exc)

        lista = [threading.Thread(target=trabajar, args=(i,), daemon=True) for i in range(hilos)]
        for h in lista:
            h.start()
        for h in lista:
            h.join(30)
        self.assertEqual(errores, [])
        self.assertEqual(maximo[0], 1, "dos corridas tuvieron la memoria a la vez")
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(sorted(m._vistas), sorted(f"hilo{i}" for i in range(hilos)))  # ninguna se perdió

    def test_dos_procesos_de_verdad_el_candado_esta_en_el_disco(self):
        codigo = (
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "from radar.memoria import Memoria, MemoriaOcupada\n"
            "try:\n"
            "    Memoria.abrir(sys.argv[2])\n"
            "except MemoriaOcupada as e:\n"
            "    print('OCUPADA'); sys.exit(3)\n"
            "print('ABIERTA')\n"
        )
        with Memoria.abrir(self.ruta):
            hijo = subprocess.run(
                [sys.executable, "-c", codigo, str(RAIZ), str(self.ruta)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual((hijo.returncode, hijo.stdout.strip()), (3, "OCUPADA"), hijo.stderr)
        hijo = subprocess.run(
            [sys.executable, "-c", codigo, str(RAIZ), str(self.ruta)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(hijo.stdout.strip(), "ABIERTA", hijo.stderr)


if __name__ == "__main__":
    unittest.main()
