"""La validación propia de la respuesta: la respuesta del modelo es una entrada no confiable."""

import copy
import unittest

from radar.analisis import esquema
from radar.analisis.errores import ErrorRespuesta
from radar.analisis.modelos import Analisis

from .ayudas import item_valido

SIN_FRASE = ("titulo",)
AMBAS = ("titulo", "titulo_y_frase")


def validar(items, claves=("a",), bases=AMBAS):
    return esquema.validar_respuesta({"analisis": items}, {c: bases for c in claves})


class TestEsquema(unittest.TestCase):
    def recorrer(self, nodo, ruta="raiz"):
        if isinstance(nodo, dict):
            if nodo.get("type") == "object":
                self.assertIs(nodo.get("additionalProperties"), False, f"additionalProperties en {ruta}")
                self.assertEqual(sorted(nodo["required"]), sorted(nodo["properties"]), f"required en {ruta}")
            for k, v in nodo.items():
                self.recorrer(v, f"{ruta}.{k}")
        elif isinstance(nodo, list):
            for i, v in enumerate(nodo):
                self.recorrer(v, f"{ruta}[{i}]")

    def test_additional_properties_false_y_todo_requerido_en_todos_los_niveles(self):
        self.recorrer(esquema.esquema_respuesta())

    def test_el_esquema_nombra_los_mismos_campos_que_la_validacion(self):
        item = esquema.esquema_respuesta()["properties"]["analisis"]["items"]
        self.assertEqual(set(item["properties"]), set(esquema.CAMPOS_ITEM))

    def test_un_item_valido_cumple_el_esquema_de_campos(self):
        self.assertEqual(set(item_valido("a")), set(esquema.CAMPOS_ITEM))

    def test_cada_llamada_devuelve_un_objeto_nuevo(self):
        a = esquema.esquema_respuesta()
        a["properties"]["analisis"]["items"]["properties"]["sirve"]["enum"].append("contaminado")
        b = esquema.esquema_respuesta()
        self.assertNotIn("contaminado", b["properties"]["analisis"]["items"]["properties"]["sirve"]["enum"])
        self.assertNotEqual(a, b)


class TestEsquemaContraLaValidacion(unittest.TestCase):
    """Lo que se le manda a claude (el esquema) y lo que se valida acá tienen que decir lo mismo."""

    def setUp(self):
        self.esq = esquema.esquema_respuesta()
        self.item = self.esq["properties"]["analisis"]["items"]
        self.props = self.item["properties"]

    def test_el_nivel_de_arriba(self):
        self.assertEqual(self.esq["type"], "object")
        self.assertEqual(self.esq["required"], ["analisis"])
        self.assertEqual(self.esq["properties"]["analisis"]["type"], "array")
        self.assertEqual(self.item["type"], "object")

    def test_los_enums_son_los_de_la_validacion(self):
        self.assertEqual(self.props["sirve"]["enum"], list(esquema.SIRVE))
        self.assertEqual(self.props["adaptador_o_core"]["enum"], list(esquema.ADAPTADOR_O_CORE))
        self.assertEqual(self.props["base"]["enum"], list(esquema.BASES))
        self.assertEqual(self.props["confianza"]["enum"], list(esquema.CONFIANZAS))
        for campo in ("mejora_control", "mejora_software", "lo_regala_el_proveedor"):
            self.assertEqual(self.props[campo]["properties"]["respuesta"]["enum"], list(esquema.TRES), campo)

    def test_las_constantes_son_las_pedidas(self):
        # Literales a propósito: si alguien cambia una constante, el esquema y la validación cambian juntos
        # y nada más lo notaría.
        self.assertEqual(esquema.SIRVE, ("si", "no", "tal_vez"))
        self.assertEqual(esquema.TRES, ("si", "no", "no_se"))
        self.assertEqual(esquema.ADAPTADOR_O_CORE, ("adaptador", "core", "ninguno", "no_se"))
        self.assertEqual(esquema.BASES, ("titulo", "titulo_y_frase", "articulo", "idea_del_owner"))
        self.assertEqual(esquema.CONFIANZAS, ("alta", "media", "baja"))
        self.assertEqual(esquema.MAX_TEXTO, 1500)

    def test_los_tipos(self):
        for campo in ("ruido", "sospecha_de_instrucciones"):
            self.assertEqual(self.props[campo], {"type": "boolean"}, campo)
        self.assertEqual(self.props["clave"]["type"], "string")
        for campo in ("sirve", "adaptador_o_core", "base", "confianza"):
            self.assertEqual(self.props[campo]["type"], "string", campo)
        for campo in ("mejora_control", "mejora_software", "lo_regala_el_proveedor"):
            self.assertEqual(self.props[campo]["type"], "object", campo)
            self.assertEqual(self.props[campo]["properties"]["por_que"]["type"], "string", campo)

    def test_los_textos_tienen_los_mismos_topes_que_la_validacion(self):
        textos = [self.props[c] for c in esquema.CAMPOS_TEXTO]
        textos += [self.props[c]["properties"]["por_que"] for c in ("mejora_control", "mejora_software", "lo_regala_el_proveedor")]
        for t in textos:
            self.assertEqual((t["type"], t["minLength"], t["maxLength"]), ("string", 1, esquema.MAX_TEXTO))

    def test_el_tope_del_esquema_es_el_que_la_validacion_aplica(self):
        justo = item_valido("a", por_que_sirve_o_no="a" * esquema.MAX_TEXTO)
        pasado = item_valido("a", por_que_sirve_o_no="a" * (esquema.MAX_TEXTO + 1))
        self.assertIn("a", validar([justo]).analizadas)
        self.assertIn("a", validar([pasado]).fallas)
        for campo in ("por_que_sirve_o_no",):
            self.assertEqual(self.props[campo]["maxLength"], esquema.MAX_TEXTO)

    def test_todo_enum_del_esquema_es_aceptado_y_nada_mas(self):
        for campo, valores in (
            ("sirve", esquema.SIRVE),
            ("adaptador_o_core", esquema.ADAPTADOR_O_CORE),
            ("confianza", esquema.CONFIANZAS),
        ):
            for v in self.props[campo]["enum"]:
                extra = {"ruido": False} if campo != "sirve" else {"ruido": False}
                v_ok = validar([item_valido("a", **{campo: v}, **extra)], bases=esquema.BASES)
                self.assertIn("a", v_ok.analizadas, (campo, v))
            self.assertEqual(set(self.props[campo]["enum"]), set(valores))


class TestCaminoFeliz(unittest.TestCase):
    def test_item_valido_se_vuelve_analisis_inmutable(self):
        v = validar([item_valido("a")])
        self.assertEqual(v.fallas, {})
        self.assertEqual(v.ignoradas, 0)
        a = v.analizadas["a"]
        self.assertIsInstance(a, Analisis)
        self.assertEqual((a.sirve, a.ruido, a.base, a.confianza), ("si", False, "titulo", "media"))
        self.assertEqual(a.mejora_control.respuesta, "si")
        with self.assertRaises(Exception):
            a.sirve = "no"
        with self.assertRaises(Exception):
            a.mejora_control.respuesta = "no"

    def test_a_dict_vuelve_a_validar_igual(self):
        a = validar([item_valido("a")]).analizadas["a"]
        crudo = a.a_dict()
        self.assertEqual((crudo["pasada"], crudo["articulo"]), (1, None))  # los anota el analizador, no el modelo
        del crudo["pasada"], crudo["articulo"], crudo["sospecha_mecanica"]
        v2 = validar([crudo])
        self.assertEqual(v2.analizadas["a"], a)

    def test_el_ruido_con_sirve_no_valida(self):
        v = validar([item_valido("a", sirve="no", ruido=True)])
        self.assertTrue(v.analizadas["a"].ruido)

    def test_sospecha_de_instrucciones_se_conserva(self):
        v = validar([item_valido("a", sospecha_de_instrucciones=True)])
        self.assertTrue(v.analizadas["a"].sospecha_de_instrucciones)

    def test_base_idea_solo_si_se_permite(self):
        v = validar([item_valido("a", base="idea_del_owner")], bases=("idea_del_owner",))
        self.assertIn("a", v.analizadas)
        v = validar([item_valido("a", base="idea_del_owner")])
        self.assertIn("a", v.fallas)


class TestFormaDeLaRespuesta(unittest.TestCase):
    def test_respuestas_que_no_tienen_la_forma(self):
        esperadas = {"a": AMBAS}
        for mala in (None, [], "texto", 3, {}, {"analisis": "x"}, {"analisis": None}, {"analisis": {}}):
            with self.subTest(mala=mala), self.assertRaises(ErrorRespuesta):
                esquema.validar_respuesta(mala, esperadas)

    def test_propiedad_extra_en_la_raiz_rechaza_todo(self):
        with self.assertRaises(ErrorRespuesta):
            esquema.validar_respuesta({"analisis": [item_valido("a")], "extra": 1}, {"a": AMBAS})

    def test_lista_vacia_deja_todas_las_claves_como_fallas(self):
        v = validar([], claves=("a", "b"))
        self.assertEqual(set(v.fallas), {"a", "b"})
        self.assertEqual(v.analizadas, {})


class TestClaves(unittest.TestCase):
    def test_clave_de_mas_desconocida_se_ignora_y_se_cuenta(self):
        v = validar([item_valido("a"), item_valido("zzz")])
        self.assertEqual(set(v.analizadas), {"a"})
        self.assertEqual(v.ignoradas, 1)
        self.assertEqual(v.fallas, {})

    def test_clave_repetida_se_ignora_y_se_cuenta_y_queda_la_primera(self):
        v = validar([item_valido("a", sirve="si"), item_valido("a", sirve="no")])
        self.assertEqual(v.analizadas["a"].sirve, "si")
        self.assertEqual(v.ignoradas, 1)

    def test_clave_repetida_despues_de_un_item_invalido_no_lo_rescata(self):
        v = validar([item_valido("a", sirve="quizas"), item_valido("a")])
        self.assertIn("a", v.fallas)
        self.assertNotIn("a", v.analizadas)
        self.assertEqual(v.ignoradas, 1)

    def test_clave_que_falta_es_una_falla_de_esa_novedad_y_las_demas_siguen(self):
        v = validar([item_valido("a")], claves=("a", "b"))
        self.assertEqual(set(v.analizadas), {"a"})
        self.assertIn("no trajo", v.fallas["b"])

    def test_items_sin_clave_o_que_no_son_objetos_se_ignoran(self):
        sin = item_valido("a")
        del sin["clave"]
        v = validar([sin, "texto", 5, None, [1], item_valido(7)], claves=("a",))
        self.assertEqual(v.ignoradas, 6)
        self.assertIn("a", v.fallas)

    def test_la_clave_se_compara_exacta(self):
        v = validar([item_valido("A"), item_valido("a "), item_valido("a​")], claves=("a",))
        self.assertEqual(v.ignoradas, 3)
        self.assertIn("a", v.fallas)


class TestItemsInvalidos(unittest.TestCase):
    def falla(self, **cambios):
        v = validar([item_valido("a", **cambios)])
        self.assertIn("a", v.fallas, cambios)
        self.assertNotIn("a", v.analizadas)
        return v.fallas["a"]

    def test_enums_invalidos(self):
        for campo, malo in (
            ("sirve", "quizas"),
            ("sirve", "SI"),
            ("sirve", "si "),
            ("sirve", True),
            ("sirve", ["si"]),
            ("adaptador_o_core", "otro"),
            ("base", "articulo"),
            ("confianza", "altisima"),
        ):
            with self.subTest(campo=campo, malo=malo):
                self.falla(**{campo: malo})

    def test_enums_de_las_opiniones(self):
        for campo in ("mejora_control", "mejora_software", "lo_regala_el_proveedor"):
            with self.subTest(campo=campo):
                self.falla(**{campo: {"respuesta": "tal_vez", "por_que": "x"}})

    def test_booleanos_estrictos(self):
        for malo in (1, 0, "true", "false", None):
            with self.subTest(malo=malo):
                self.falla(ruido=malo)
                self.falla(sospecha_de_instrucciones=malo)

    def test_propiedades_extra_en_el_item(self):
        self.assertIn("campos de más", self.falla(extra="x"))

    def test_propiedades_extra_en_una_opinion(self):
        self.falla(mejora_control={"respuesta": "si", "por_que": "x", "extra": 1})

    def test_campos_que_faltan(self):
        for campo in esquema.CAMPOS_ITEM - {"clave"}:
            item = item_valido("a")
            del item[campo]
            with self.subTest(campo=campo):
                v = validar([item])
                self.assertIn("a", v.fallas)
                self.assertIn("faltan", v.fallas["a"])

    def test_campos_faltantes_en_una_opinion(self):
        self.falla(mejora_software={"respuesta": "si"})
        self.falla(mejora_software={"por_que": "x"})

    def test_opinion_que_no_es_objeto(self):
        self.falla(mejora_software="si")
        self.falla(mejora_software=None)

    def test_textos_que_no_son_texto(self):
        for campo in esquema.CAMPOS_TEXTO:
            for malo in (None, 5, ["x"], {"a": 1}, True):
                with self.subTest(campo=campo, malo=malo):
                    self.falla(**{campo: malo})

    def test_textos_vacios(self):
        for campo in esquema.CAMPOS_TEXTO:
            for malo in ("", "   ", "\n\t", "​‮"):
                with self.subTest(campo=campo, malo=malo):
                    self.falla(**{campo: malo})

    def test_ruido_con_sirve_distinto_de_no_es_contradictorio(self):
        for sirve in ("si", "tal_vez"):
            with self.subTest(sirve=sirve):
                self.assertIn("contradictorio", self.falla(sirve=sirve, ruido=True))

    def test_base_titulo_y_frase_sin_frase_no_corresponde(self):
        v = validar([item_valido("a", base="titulo_y_frase")], bases=SIN_FRASE)
        self.assertIn("a", v.fallas)

    def test_un_item_invalido_no_tumba_a_los_demas_del_lote(self):
        v = validar([item_valido("a"), item_valido("b", sirve="quizas"), item_valido("c")], claves=("a", "b", "c"))
        self.assertEqual(set(v.analizadas), {"a", "c"})
        self.assertEqual(set(v.fallas), {"b"})

    def test_el_item_no_se_modifica(self):
        item = item_valido("a", por_que_sirve_o_no="<b>hola</b>")
        copia = copy.deepcopy(item)
        validar([item])
        self.assertEqual(item, copia)


class TestTextosHostiles(unittest.TestCase):
    def texto(self, valor, campo="por_que_sirve_o_no"):
        v = validar([item_valido("a", **{campo: valor})])
        return v

    def test_texto_gigante_se_rechaza_sin_recortar_en_silencio(self):
        v = self.texto("a" * (esquema.MAX_TEXTO + 1))
        self.assertIn("a", v.fallas)
        self.assertIn("1500", v.fallas["a"])

    def test_texto_en_el_tope_pasa_entero(self):
        v = self.texto("a" * esquema.MAX_TEXTO)
        self.assertEqual(len(v.analizadas["a"].por_que_sirve_o_no), esquema.MAX_TEXTO)

    def test_texto_de_megabytes_se_rechaza_rapido(self):
        v = self.texto("<" * 5_000_000)
        self.assertIn("a", v.fallas)

    def test_relleno_hostil_que_no_converge_se_rechaza(self):
        v = self.texto("<<<<" * 300)
        self.assertIn("a", v.fallas)

    def test_controles_y_direccion_se_quitan(self):
        v = self.texto("hola\x00\x1b[31m mundo‮​﻿ fin\r\n\tz")
        t = v.analizadas["a"].por_que_sirve_o_no
        for ch in ("\x00", "\x1b", "‮", "​", "﻿", "\r", "\n", "\t"):
            self.assertNotIn(ch, t)
        self.assertTrue(t.startswith("hola"))

    def test_etiquetas_se_quitan(self):
        v = self.texto("Mirar <script>alert(1)</script> esto <img src=x onerror=alert(1)> y <b>esto</b>.")
        t = v.analizadas["a"].por_que_sirve_o_no
        self.assertNotIn("<", t)
        self.assertNotIn("alert", t)
        self.assertIn("esto", t)

    def test_etiquetas_escapadas_varios_niveles_tambien(self):
        v = self.texto("ok &amp;lt;img src=x onerror=alert(1)&amp;gt; fin")
        t = v.analizadas["a"].por_que_sirve_o_no
        self.assertNotIn("<", t)
        self.assertNotIn("onerror", t)

    def test_solo_etiquetas_deja_el_texto_vacio_y_se_rechaza(self):
        self.assertIn("a", self.texto("<script>alert(1)</script>").fallas)

    def test_la_limpieza_vale_tambien_dentro_de_las_opiniones(self):
        v = validar([item_valido("a", mejora_control={"respuesta": "si", "por_que": "<i>x</i>‮ y"})])
        self.assertEqual(v.analizadas["a"].mejora_control.por_que, "x y")

    def test_acentos_y_simbolos_normales_se_conservan(self):
        v = self.texto("Habrá que revisar «esto» — ¿sí? ñandú 100 % ~ a < b y c > d")
        t = v.analizadas["a"].por_que_sirve_o_no
        self.assertIn("«esto»", t)
        self.assertIn("ñandú", t)


class TestCoherenciaMecanica(unittest.TestCase):
    """Una respuesta que no es «no_se» no puede traer un por_que que empieza diciendo que no se sabe."""

    CAMPOS = ("mejora_control", "mejora_software", "lo_regala_el_proveedor")
    DICEN_QUE_NO_SE_SABE = (
        "No sé si lo ofrece el proveedor.",
        "No se sabe qué hace.",
        "No está claro que mejore algo.",
        "No esta claro que mejore algo.",
        "No se si lo ofrece.",
        "NO SÉ",
        "no sé",
        "  No sé, por el título.",
        "«No sé» si aplica.",
        "- No se sabe.",
        "No se, depende.",
        "No se.",
    )
    NO_LO_DICEN = (
        "Agrega un control que antes no había.",
        "No se agrega ningún control nuevo.",
        "No sembró nada nuevo.",
        "No cambia lo que sale.",
        "Por el título, no sé si lo ofrece.",
        "Sí, y no se sabe todavía el costo.",
        "Nose",
    )

    def con(self, campo, respuesta, por_que):
        return validar([item_valido("a", **{campo: {"respuesta": respuesta, "por_que": por_que}})])

    def test_si_o_no_con_un_por_que_que_dice_que_no_se_sabe_es_invalido(self):
        for campo in self.CAMPOS:
            for respuesta in ("si", "no"):
                for texto in self.DICEN_QUE_NO_SE_SABE:
                    with self.subTest(campo=campo, respuesta=respuesta, texto=texto):
                        v = self.con(campo, respuesta, texto)
                        self.assertIn("a", v.fallas)
                        self.assertNotIn("a", v.analizadas)
                        self.assertIn("contradictorio", v.fallas["a"])
                        self.assertIn(campo, v.fallas["a"])

    def test_con_no_se_el_por_que_puede_decir_que_no_se_sabe(self):
        for campo in self.CAMPOS:
            for texto in self.DICEN_QUE_NO_SE_SABE:
                with self.subTest(campo=campo, texto=texto):
                    self.assertIn("a", self.con(campo, "no_se", texto).analizadas)

    def test_lo_que_no_empieza_asi_es_valido(self):
        for campo in self.CAMPOS:
            for respuesta in ("si", "no", "no_se"):
                for texto in self.NO_LO_DICEN:
                    with self.subTest(campo=campo, respuesta=respuesta, texto=texto):
                        self.assertIn("a", self.con(campo, respuesta, texto).analizadas)

    def test_dice_que_no_se_sabe(self):
        for texto in self.DICEN_QUE_NO_SE_SABE:
            self.assertTrue(esquema.dice_que_no_se_sabe(texto), texto)
        for texto in self.NO_LO_DICEN:
            self.assertFalse(esquema.dice_que_no_se_sabe(texto), texto)

    def test_un_item_incoherente_no_tumba_a_los_demas_del_lote(self):
        malo = item_valido("b", mejora_control={"respuesta": "si", "por_que": "No sé si mejora."})
        v = validar([item_valido("a"), malo, item_valido("c")], claves=("a", "b", "c"))
        self.assertEqual(set(v.analizadas), {"a", "c"})
        self.assertEqual(set(v.fallas), {"b"})


if __name__ == "__main__":
    unittest.main()
