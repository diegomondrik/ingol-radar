"""La numeración (la misma en la página y en el JSON y la del orden en pantalla) y lo que la página dice de cada cosa."""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone

from radar.analisis.modelos import InfoArticulo, Resultado
from radar.analisis.prompt import CLAVE_IDEA
from radar.lectura.modelos import Cobertura
from radar.pagina import Corrida, construir

from .ayudas import AHORA, analisis, corrida_de, novedad, revisar


def _fecha(dia: int) -> datetime:
    return datetime(2026, 10, dia, tzinfo=timezone.utc)


# (n, veredicto, confianza, fecha). El orden esperado se escribió a mano (ver ESPERADO), no se calcula.
DATOS = [
    (1, "tal_vez", "alta", _fecha(5)),
    (2, "si", "media", _fecha(2)),
    (3, "no", "alta", _fecha(9)),
    (4, "si", "alta", _fecha(1)),
    (5, "quizás", "media", _fecha(3)),  # veredicto fuera de lo esperado
    (6, "si", "alta", _fecha(7)),
    (7, "tal_vez", "baja", None),
    (8, "si", "alta", None),
    (9, "no", "baja", _fecha(4)),
    (10, "si", "media", _fecha(2)),  # empata con la 2 en veredicto, confianza y fecha: desempata la clave
]
# sirven (confianza alta: más nueva primero, sin fecha al final; después media) -> tal vez -> otras -> ruido
ESPERADO = [6, 4, 8, 10, 2, 1, 7, 5, 3, 9]


def _pares():
    return [(novedad(n, fecha=fecha), analisis(f"f:{n}", sirve=sirve, confianza=conf)) for n, sirve, conf, fecha in DATOS]


def _posicion(html: str, k: int) -> int:
    """Dónde aparece TITULO_<k> (sin confundirlo con TITULO_<k>0)."""
    return re.search(rf"TITULO_{k}(?!\d)", html).start()


def _numeros_en_la_pagina(html: str) -> list[tuple[int, int]]:
    """(número visible, k de TITULO_k) en el orden en que aparecen en pantalla: tarjetas y después las líneas de ruido."""
    tarjetas = re.findall(r'<article class="card[^"]*" id="n(\d+)">.*?<h3>(?:<a [^>]*>)?TITULO_(\d+)', html, re.DOTALL)
    lineas = re.findall(r'<span class="n">(\d+)</span><span>(?:<a [^>]*>)?TITULO_(\d+)', html)
    visibles = [(int(a), int(b)) for a, b in tarjetas + lineas]
    # El orden de aparición real, no el de las dos búsquedas por separado:
    posiciones = {k: _posicion(html, k) for _n, k in visibles}
    return sorted(visibles, key=lambda par: posiciones[par[1]])


class TestNumeracion(unittest.TestCase):
    def setUp(self):
        self.pagina = construir(corrida_de(_pares()))
        self.datos = json.loads(self.pagina.json)

    def test_el_orden_es_el_esperado_en_la_pagina_y_en_el_json(self):
        en_pagina = _numeros_en_la_pagina(self.pagina.html)
        self.assertEqual([k for _n, k in en_pagina], ESPERADO)
        self.assertEqual([n for n, _k in en_pagina], list(range(1, 11)))
        en_json = [(x["numero"], int(x["titulo"].removeprefix("TITULO_"))) for x in self.datos["novedades"]]
        self.assertEqual(en_json, en_pagina)

    def test_las_secciones_van_en_el_orden_de_la_numeracion(self):
        html = self.pagina.html
        posiciones = [html.index(rotulo) for rotulo in ("<h2>Sirven (", "<h2>Tal vez (", "<h2>Otras (", "Ruido (")]
        self.assertEqual(posiciones, sorted(posiciones))
        # Y cada número cae en su sección: sirven 1-5, tal vez 6-7, otras 8, ruido 9-10.
        corte = [html.index(r) for r in ("<h2>Tal vez (", "<h2>Otras (", "Ruido (")]
        visibles = _numeros_en_la_pagina(html)
        seccion = {n: sum(_posicion(html, k) > c for c in corte) for n, k in visibles}
        self.assertEqual([seccion[n] for n in range(1, 11)], [0] * 5 + [1] * 2 + [2] + [3] * 2)

    def test_el_veredicto_del_json_sigue_a_la_numeracion(self):
        veredictos = [x["veredicto"] for x in self.datos["novedades"]]
        self.assertEqual(veredictos, ["sirve"] * 5 + ["tal_vez"] * 2 + ["quizás"] + ["no"] * 2)

    def test_el_mismo_numero_para_la_misma_clave(self):
        por_numero_json = {x["numero"]: x["clave"] for x in self.datos["novedades"]}
        for numero, k in _numeros_en_la_pagina(self.pagina.html):
            self.assertEqual(por_numero_json[numero], f"f:{k}")
        self.assertEqual([x.numero for x in self.pagina.entradas], list(range(1, 11)))
        self.assertEqual([x.novedad.clave for x in self.pagina.entradas], [f"f:{k}" for k in ESPERADO])

    def test_el_orden_no_depende_del_orden_de_entrada(self):
        pares = _pares()
        for variante in (list(reversed(pares)), pares[3:] + pares[:3]):
            otra = construir(corrida_de(variante))
            self.assertEqual([x.novedad.clave for x in otra.entradas], [f"f:{k}" for k in ESPERADO])
            self.assertEqual(otra.json, self.pagina.json)
            self.assertEqual(otra.html, self.pagina.html)

    def test_el_ruido_va_en_un_details_cerrado(self):
        html = self.pagina.html
        bloque = re.search(r"<details><summary>Ruido \((\d+)\).*?</details>", html, re.DOTALL)
        self.assertIsNotNone(bloque, "el ruido va en un <details> sin atributo open")
        self.assertEqual(bloque.group(1), "2")
        self.assertEqual(len(re.findall(r'class="linea"', bloque.group(0))), 2)
        self.assertEqual(len(re.findall(r'class="linea"', html)), 2)  # y no hay líneas de ruido fuera de él
        self.assertNotIn("<details open><summary>Ruido", html)
        for x in self.pagina.entradas:  # el ruido no tiene tarjeta
            if x.analisis.sirve == "no":
                self.assertNotIn(f'id="n{x.numero}"', html)

    def test_cada_tarjeta_lleva_su_numero_y_su_ancla(self):
        for x in self.pagina.entradas:
            if x.analisis.sirve == "no":
                continue
            self.assertIn(f'id="n{x.numero}"', self.pagina.html)
            self.assertIn(f'aria-label="Novedad {x.numero}">{x.numero}</div>', self.pagina.html)


class TestContenidoDelJson(unittest.TestCase):
    def setUp(self):
        self.corrida = corrida_de(_pares())
        self.pagina = construir(self.corrida)
        self.datos = json.loads(self.pagina.json)

    def test_cada_novedad_trae_todo_lo_que_una_sesion_necesita(self):
        for x, esperada in zip(self.datos["novedades"], self.pagina.entradas):
            self.assertEqual(
                set(x), {"numero", "clave", "titulo", "enlace", "fuente", "fecha", "frase", "veredicto", "sospecha", "analisis"}
            )
            nov = esperada.novedad
            self.assertEqual(x["clave"], nov.clave)
            self.assertEqual(x["titulo"], nov.titulo)
            self.assertEqual(x["enlace"], nov.enlace)
            self.assertEqual(x["fuente"], nov.fuente)
            self.assertEqual(x["fecha"], nov.fecha.isoformat() if nov.fecha else None)
            self.assertEqual(x["frase"], nov.frase)
            self.assertEqual(x["analisis"], esperada.analisis.a_dict())  # el análisis completo, tal cual
            self.assertIsNone(x["sospecha"])

    def test_lo_de_la_corrida(self):
        d = self.datos
        self.assertEqual(d["version"], 1)
        self.assertIn("dato", d["aviso"])
        self.assertEqual(d["generado"], AHORA.isoformat())
        self.assertEqual(d["ventana"], {"desde": (AHORA - timedelta(days=28)).isoformat(), "hasta": AHORA.isoformat()})
        self.assertIsNone(d["idea"])
        self.assertEqual(d["conteos"], self.pagina.conteos)
        self.assertEqual(
            d["conteos"],
            {"leidas": 10, "nuevas": 10, "analizadas": 10, "sirven": 5, "tal_vez": 2, "otras": 1, "ruido": 2, "sin_analizar": 0},
        )
        self.assertEqual(d["contadores"], Resultado().contadores())
        self.assertEqual(d["cobertura"]["fuente-a"], {"nombre": "Fuente A", **self.corrida.cobertura["fuente-a"].a_dict()})

    def test_la_marca_de_sospecha(self):
        fuerte = analisis("f:1", sospecha_de_instrucciones=True)
        del_articulo = analisis(
            "f:2", pasada=2, sospecha_de_instrucciones=True, articulo=InfoArticulo("leido", None, False, False)
        )
        por_senal = analisis(
            "f:3", sospecha_mecanica=True, articulo=InfoArticulo("leido", "la segunda pasada falló (x)", False, True)
        )
        limpia = analisis("f:4")
        pagina = construir(corrida_de([(novedad(i), a) for i, a in enumerate([fuerte, del_articulo, por_senal, limpia], 1)]))
        marcas = {x["clave"]: x["sospecha"] for x in json.loads(pagina.json)["novedades"]}
        self.assertEqual(marcas, {"f:1": "titulo_o_frase", "f:2": "articulo", "f:3": "articulo", "f:4": None})
        self.assertEqual(pagina.html.count("Esta novedad trae instrucciones para un modelo"), 1)
        self.assertEqual(pagina.html.count("El artículo cita instrucciones para un modelo"), 2)

    def test_lo_que_costo_cuota_queda_en_el_json_aunque_no_este_en_la_pagina(self):
        # El ruido sólo es una línea en la página; su análisis completo está en el JSON.
        ruido = next(x for x in self.datos["novedades"] if x["veredicto"] == "no")
        self.assertEqual(ruido["analisis"]["en_que_consiste_el_cambio"], "Un método nuevo de revisión.")


class TestLoQueLaPaginaDice(unittest.TestCase):
    def _corrida_con_problemas(self):
        pares = [(novedad(1), analisis("f:1")), (novedad(2, fuente="fuente-b"), None), (novedad(3), None)]
        cobertura = {
            "fuente-a": Cobertura(estado="ok", en_fuente=5, en_ventana=3, cubre_desde=AHORA - timedelta(days=40)),
            "fuente-b": Cobertura(estado="vacia", motivo="respondió pero no se sacó ninguna novedad"),
            "fuente-c": Cobertura(estado="error", motivo="ErrorDescarga: sin conexión"),
        }
        fuentes = {"fuente-a": "Fuente A", "fuente-b": "Fuente B", "fuente-c": "Fuente C"}
        return corrida_de(pares, cobertura=cobertura, fuentes=fuentes)

    def test_fuente_vacia_o_con_error_destacada(self):
        pagina = construir(self._corrida_con_problemas())
        html = pagina.html
        self.assertEqual(revisar(html).problemas, [])
        self.assertIn('<tr class="fila-vacia">', html)
        self.assertIn('<tr class="fila-error">', html)
        self.assertIn("No trajo nada", html)
        self.assertIn("Atención con las fuentes", html)
        aviso = re.search(r'<div class="aviso crit"><p><strong>Atención con las fuentes.*?</div>', html, re.DOTALL).group(0)
        self.assertIn("Fuente B", aviso)
        self.assertIn("Fuente C", aviso)
        self.assertNotIn("Fuente A", aviso)

    def test_cobertura_por_fuente_con_desde_cuando_cubre(self):
        pagina = construir(self._corrida_con_problemas())
        self.assertIn("Cobertura por fuente", pagina.html)
        self.assertIn("2026-09-04", pagina.html)  # cubre desde (hoy menos 40 días)
        datos = json.loads(pagina.json)["cobertura"]
        self.assertEqual(set(datos), {"fuente-a", "fuente-b", "fuente-c"})
        self.assertEqual(datos["fuente-c"]["estado"], "error")
        self.assertEqual(datos["fuente-a"]["cubre_desde"], (AHORA - timedelta(days=40)).isoformat())

    def test_lo_sin_analizar_se_lista_con_su_motivo(self):
        pagina = construir(self._corrida_con_problemas())
        self.assertIn("Sin analizar", pagina.html)
        self.assertIn("TITULO_2", pagina.html.split("Sin analizar", 1)[1])
        self.assertIn("falló dos veces", pagina.html)
        datos = json.loads(pagina.json)
        self.assertEqual({x["clave"]: x["motivo"] for x in datos["sin_analizar"]}, {"f:2": "falló dos veces", "f:3": "falló dos veces"})
        self.assertEqual(datos["conteos"]["sin_analizar"], 2)

    def test_una_novedad_sin_resultado_tampoco_se_pierde(self):
        # Entró a analizar, no está en analizadas ni en sin_analizar (no debería pasar): sale igual, con motivo.
        corrida = corrida_de([(novedad(1), analisis("f:1"))])
        corrida.nuevas.append(novedad(2))
        pagina = construir(corrida)
        self.assertIn("TITULO_2", pagina.html)
        self.assertEqual([x["clave"] for x in json.loads(pagina.json)["sin_analizar"]], ["f:2"])

    def test_el_tope_se_dice(self):
        corrida = corrida_de([(novedad(1), analisis("f:1")), (novedad(2), None)])
        corrida.resultado.por_tope = 1
        corrida.resultado.sin_analizar["f:2"] = "tope de la corrida"
        self.assertIn("Se alcanzó el tope de la corrida (300 análisis)", construir(corrida).html)

    def test_corrida_cortada_se_avisa(self):
        corrida = corrida_de([(novedad(1), analisis("f:1")), (novedad(2), None)])
        corrida.resultado.abortada = "interrumpida por el usuario (Ctrl+C)"
        pagina = construir(corrida)
        self.assertIn("La corrida se cortó antes de terminar", pagina.html)
        self.assertEqual(json.loads(pagina.json)["contadores"]["abortada"], "interrumpida por el usuario (Ctrl+C)")

    def test_sin_novedades_la_pagina_lo_dice(self):
        nada = construir(corrida_de([], leidas=0))
        self.assertIn("No hay novedades: ninguna fuente trajo algo", nada.html)
        ya_vistas = construir(corrida_de([], leidas=40))
        self.assertIn("ya se había visto", ya_vistas.html)
        self.assertEqual(json.loads(ya_vistas.json)["novedades"], [])
        self.assertNotIn("Para aprobar", ya_vistas.html)

    def test_si_ninguna_fuente_se_pudo_leer_no_dice_que_no_hay_nada_nuevo(self):
        cobertura = {"fuente-a": Cobertura(estado="error", motivo="sin conexión")}
        pagina = construir(corrida_de([], leidas=0, cobertura=cobertura))
        self.assertIn("No se pudo leer ninguna fuente", pagina.html)
        self.assertNotIn("ninguna fuente trajo algo", pagina.html)

    def test_nada_que_sirva_dice_que_nada_sirve(self):
        pagina = construir(corrida_de([(novedad(1), analisis("f:1", sirve="no")), (novedad(2), analisis("f:2", sirve="no"))]))
        self.assertIn("Ninguna novedad de esta corrida le sirve a INGOL", pagina.html)
        self.assertNotIn("Para aprobar", pagina.html)

    def test_como_aprobar(self):
        pagina = construir(corrida_de(_pares()))
        self.assertIn("sí a la 2 y la 5", pagina.html)
        self.assertIn(".json", pagina.html)

    def test_la_pagina_de_una_idea(self):
        resultado = Resultado(analizadas={CLAVE_IDEA: analisis(CLAVE_IDEA, sirve="tal_vez", base="idea_del_owner")})
        pagina = construir(Corrida(generado=AHORA, resultado=resultado, idea="Que INGOL lea los PR de los proveedores."))
        revisor = revisar(pagina.html)
        self.assertEqual(revisor.problemas, [])
        self.assertIn("Que INGOL lea los PR de los proveedores.", revisor.texto)
        self.assertIn("Análisis de una idea", revisor.texto)
        self.assertNotIn("novedades leídas", pagina.html)
        datos = json.loads(pagina.json)
        self.assertEqual(datos["idea"], "Que INGOL lea los PR de los proveedores.")
        self.assertIsNone(datos["ventana"])
        self.assertEqual([x["numero"] for x in datos["novedades"]], [1])
        self.assertEqual(datos["novedades"][0]["analisis"]["base"], "idea_del_owner")

    def test_una_idea_sin_analizar_se_dice(self):
        resultado = Resultado(sin_analizar={CLAVE_IDEA: "claude no respondió"})
        pagina = construir(Corrida(generado=AHORA, resultado=resultado, idea="algo"))
        self.assertIn("claude no respondió", pagina.html)
        self.assertEqual(json.loads(pagina.json)["conteos"]["sin_analizar"], 1)


if __name__ == "__main__":
    unittest.main()
