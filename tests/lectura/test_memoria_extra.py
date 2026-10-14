"""Memoria: poda y tope de tamaño, bordes de la escritura y del candado, y el candado huérfano.

Candado huérfano: el candado guarda el pid de quien lo tiene. Si ese proceso ya no existe (corte,
reinicio), la corrida siguiente lo retira y sigue; el owner no tiene que borrar nada a mano. Si el
pid está vivo o el contenido no se entiende, es `MemoriaOcupada`, como siempre.
"""

import errno
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from radar.lectura.modelos import Novedad
from radar.memoria import Memoria, MemoriaDemasiadoGrande
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
    """Si pasa TOPE_ARCHIVO, guardar poda las más viejas hasta que quepa; no lanza salvo que ni vacío quepa."""

    def sembrar(self, *claves, cuando=CUANDO):
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov(c) for c in claves], cuando)
            m.guardar()
        return self.ruta.read_bytes()

    def memoria_de_tres_dias(self):
        """a (la más vieja), b, c (la más nueva), cada una con su propia primera_vez."""
        with Memoria.abrir(self.ruta) as m:
            for clave, dias in (("a", 3), ("b", 2), ("c", 1)):
                m.marcar([nov(clave)], HOY - timedelta(days=dias))
            m.guardar()
        return self.ruta.read_bytes()

    def claves(self):
        return sorted(json.loads(self.ruta.read_text(encoding="utf-8"))["vistas"])

    def test_el_peso_de_una_vista_es_exacto(self):
        casos = [["a"], ["a", "b"], ["ñandú", "b", "ccc"], ["x" * 50, "y"], ['comillas"y\\barras', "z"]]
        for claves in casos:
            vistas = {
                k: {"fuente": "f", "titulo": 'Árbol ñandú "q" \\ ❤ \n tab\t', "enlace": "https://e.test/" + k, "primera_vez": "2026-10-10T08:00:00+00:00"}
                for k in claves
            }
            completo = len(modulo_memoria._serializar(vistas))
            for cuantas in range(1, len(claves)):
                resto = dict(vistas)
                for k in claves[:cuantas]:
                    del resto[k]
                estimado = completo - sum(modulo_memoria._peso(k, vistas[k]) for k in claves[:cuantas])
                self.assertEqual(estimado, len(modulo_memoria._serializar(resto)), (claves, cuantas))

    def test_se_podan_las_mas_viejas_hasta_que_cabe_y_se_dice_cuantas(self):
        completa = self.memoria_de_tres_dias()
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("d")], HOY)
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(completa)):  # con "d" ya no cabe
                podadas = m.guardar()
            self.assertEqual((podadas, m.podadas_por_edad, m.podadas_por_tamano), (1, 0, 1))
        self.assertEqual(self.claves(), ["b", "c", "d"])  # se fue la más vieja
        self.assertLessEqual(len(self.ruta.read_bytes()), len(completa))
        with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(completa)):
            with Memoria.abrir(self.ruta) as m:  # y lo guardado se vuelve a abrir
                self.assertEqual(len(m), 3)

    def test_se_podan_las_que_hagan_falta(self):
        completa = self.memoria_de_tres_dias()
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("d"), nov("e"), nov("f")], HOY)
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(completa)):
                m.guardar()
            self.assertEqual(m.podadas_por_tamano, 3)
        self.assertEqual(self.claves(), ["d", "e", "f"])

    def test_la_poda_por_edad_va_primero_y_cada_contador_cuenta_lo_suyo(self):
        completa = self.memoria_de_tres_dias()
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("vieja")], HOY - timedelta(days=500))
            m.marcar([nov("d")], HOY)
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(completa)):
                total = m.guardar()
            self.assertEqual((m.podadas_por_edad, m.podadas_por_tamano, total), (1, 1, 2))
        self.assertEqual(self.claves(), ["b", "c", "d"])

    def test_si_cabe_no_se_poda_nada_por_tamano(self):
        self.memoria_de_tres_dias()
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("d")], HOY)
            self.assertEqual(m.guardar(), 0)
            self.assertEqual(m.podadas_por_tamano, 0)
        self.assertEqual(self.claves(), ["a", "b", "c", "d"])

    def test_los_contadores_se_reinician_en_cada_guardado(self):
        completa = self.memoria_de_tres_dias()
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("d")], HOY)
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(completa)):
                m.guardar()
            self.assertEqual(m.podadas_por_tamano, 1)
            m.guardar()
            self.assertEqual((m.podadas_por_edad, m.podadas_por_tamano), (0, 0))

    def test_el_borde_es_estrictamente_mayor_que(self):
        completa = self.memoria_de_tres_dias()
        with Memoria.abrir(self.ruta) as m:
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(completa)):
                self.assertEqual(m.guardar(), 0)  # exacto: cabe, no se poda
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(completa) - 1):
                self.assertEqual(m.guardar(), 1)  # un byte menos: se va una

    def test_con_empate_de_fechas_el_orden_es_estable_por_clave(self):
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("x"), nov("y"), nov("z")], CUANDO)
            m.guardar()
            chico = len(self.ruta.read_bytes()) - 1
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", chico):
                m.guardar()
        self.assertEqual(self.claves(), ["y", "z"])

    def test_muchas_entradas_se_podan_sin_reserializar_cientos_de_veces(self):
        with Memoria.abrir(self.ruta) as m:
            for i in range(300):
                m.marcar([nov(f"k{i:03d}")], HOY - timedelta(days=300 - i))
            m.guardar()
            completa = len(self.ruta.read_bytes())
            llamadas = []
            real = modulo_memoria._serializar
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", completa // 2), mock.patch.object(
                modulo_memoria, "_serializar", lambda v: (llamadas.append(1), real(v))[1]
            ):
                podadas = m.guardar()
        self.assertGreaterEqual(podadas, 140)
        self.assertLessEqual(len(llamadas), 6)  # una estimación y a lo sumo un par de ajustes
        restantes = self.claves()
        self.assertEqual(restantes, sorted(restantes))
        self.assertIn("k299", restantes)  # la más nueva sigue
        self.assertNotIn("k000", restantes)  # la más vieja se fue

    def test_solo_si_ni_vacia_cabe_levanta_y_no_toca_nada(self):
        original = self.sembrar("a", "b")
        with Memoria.abrir(self.ruta) as m:
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", 10):  # una memoria vacía ya pesa más
                with self.assertRaises(MemoriaDemasiadoGrande) as ctx:
                    m.guardar()
            self.assertIn("No se escribió nada", str(ctx.exception))
            self.assertEqual(len(m), 2)  # la memoria en uso quedó como estaba
            self.assertEqual(self.ruta.read_bytes(), original)  # y el archivo, intacto
            self.assertEqual(self.carpeta(), ["memoria.json", "memoria.json.lock"])  # sin temporales
        with Memoria.abrir(self.ruta) as m:
            self.assertEqual(len(m), 2)

    def test_una_falla_de_escritura_despues_de_podar_no_cambia_la_memoria_en_uso(self):
        completa = self.memoria_de_tres_dias()
        with Memoria.abrir(self.ruta) as m:
            m.marcar([nov("d")], HOY)
            with mock.patch.object(modulo_memoria, "TOPE_ARCHIVO", len(completa)), mock.patch(
                "os.replace", side_effect=OSError(errno.ENOSPC, "lleno")
            ):
                with self.assertRaises(OSError):
                    m.guardar()
            self.assertEqual(len(m), 4)  # nada se podó en memoria: no se guardó
            self.assertEqual(self.ruta.read_bytes(), completa)


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


if __name__ == "__main__":
    unittest.main()
