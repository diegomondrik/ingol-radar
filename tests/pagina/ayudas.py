"""Piezas comunes: análisis y novedades de ejemplo, corridas armadas con texto hostil y un revisor de HTML estricto."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from radar.analisis.modelos import Analisis, InfoArticulo, Opinion, Resultado
from radar.analisis.prompt import CLAVE_IDEA
from radar.lectura.modelos import Cobertura, Novedad
from radar.pagina import Corrida
from radar.pagina.estilo import ESTILO

AHORA = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
ENLACE_BUENO = "https://www.anthropic.com/news/x"

# Texto armado a propósito: todo lo que llega de internet o del modelo puede ser esto.
HOSTILES = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "</style><script>alert(1)</script>",
    "</title><script>alert(1)</script>",
    "<!-- comentario sin cerrar",
    "texto ]]> con cierre de CDATA",
    "&lt;script&gt;alert(1)&lt;/script&gt; ya escapado",
    "‮‭texto con dirección invertida‬⁦",
    "an​cho‌ cero‍⁠﻿",
    "con\x00nulo y \x1b[31m escape de terminal",
    "sustituto suelto \ud800 y otro \udfff al final",
    "<svg onload=alert(1)> <a href=javascript:alert(1)>x</a>",
    "' onmouseover='alert(1)' style='x:expression(alert(1))",
    "línea uno\nlínea dos\r\n\ttabulada fin",
    "x" * 100_000,
    "<" * 5000,
]

ETIQUETAS = frozenset(
    "html head meta title style body header main footer section article div span p h1 h2 h3 a b strong small code br "
    "details summary dl dt dd ul li table thead tbody tr th td svg g path rect".split()
)
ATRIBUTOS = frozenset(
    "class id href target rel referrerpolicy aria-label role viewbox fill stroke stroke-width stroke-linejoin opacity "
    "d x y width height rx data-label lang charset name content http-equiv open".split()
)
VACIAS = frozenset({"meta", "br"})
ETIQUETAS_DE_FORMA = frozenset({"svg", "g", "path", "rect"})  # el logo: texto fijo, se compara aparte
ROTULOS_DE_COLUMNA = frozenset({"Estado", "Cubre desde", "En la ventana", "Sin fecha", "Descartadas", "Recortadas"})
CLASES_DEL_ESTILO = frozenset(re.findall(r"\.([a-z][a-z0-9_-]*)", ESTILO))


class Revisor(HTMLParser):
    """Lee la página como la lee un navegador y anota todo lo que una página sin datos activos no debería tener."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problemas: list[str] = []
        self.pila: list[str] = []
        self.etiquetas: Counter = Counter()
        self.hrefs: list[str] = []
        self.estilo = ""
        self.metas: list[dict] = []
        self.texto = ""

    def _mal(self, mensaje: str) -> None:
        self.problemas.append(mensaje)

    def handle_starttag(self, tag, attrs):
        self.etiquetas[tag] += 1
        if tag not in ETIQUETAS:
            self._mal(f"etiqueta que no está en la lista blanca: <{tag}>")
        for nombre, valor in attrs:
            self._atributo(tag, nombre, valor if valor is not None else "")
        if tag == "meta":
            self.metas.append({n: v for n, v in attrs})
        if tag not in VACIAS:
            self.pila.append(tag)

    def _atributo(self, tag: str, nombre: str, valor: str) -> None:
        if nombre not in ATRIBUTOS:
            self._mal(f"atributo que no está en la lista blanca: {tag}.{nombre}")
        if nombre.startswith("on") or nombre == "style":
            self._mal(f"atributo activo: {tag}.{nombre}")
        if nombre == "href":
            if tag != "a":
                self._mal(f"href fuera de <a>: <{tag}>")
            self.hrefs.append(valor)
        elif tag in ETIQUETAS_DE_FORMA:
            return
        elif nombre == "class":
            for clase in valor.split():
                if clase not in CLASES_DEL_ESTILO:
                    self._mal(f"clase que no es del estilo: {clase!r}")
        elif nombre == "id":
            if not re.fullmatch(r"n\d+", valor):
                self._mal(f"id raro: {valor!r}")
        elif nombre == "aria-label":
            if not re.fullmatch(r"Novedad \d+|INGOL", valor):
                self._mal(f"aria-label raro: {valor!r}")
        elif nombre == "data-label":
            if valor not in ROTULOS_DE_COLUMNA:
                self._mal(f"data-label raro: {valor!r}")
        elif nombre == "target" and valor != "_blank":
            self._mal(f"target raro: {valor!r}")
        elif nombre == "rel" and valor != "noopener noreferrer nofollow":
            self._mal(f"rel raro: {valor!r}")
        elif nombre == "referrerpolicy" and valor != "no-referrer":
            self._mal(f"referrerpolicy raro: {valor!r}")
        elif nombre in ("open", "lang", "charset", "name", "content", "http-equiv") and tag not in ("details", "html", "meta"):
            self._mal(f"{nombre} fuera de su etiqueta: <{tag}>")

    def handle_endtag(self, tag):
        if tag in VACIAS:
            return
        if not self.pila or self.pila[-1] != tag:
            self._mal(f"cierre </{tag}> que no corresponde (pila: {self.pila[-3:]})")
            if tag in self.pila:
                while self.pila and self.pila.pop() != tag:
                    pass
            return
        self.pila.pop()

    def handle_data(self, data):
        if self.pila and self.pila[-1] == "style":
            self.estilo += data
        else:
            self.texto += data

    def handle_comment(self, data):
        self._mal(f"comentario HTML: {data[:40]!r}")

    def handle_decl(self, decl):
        if decl.lower() != "doctype html":
            self._mal(f"declaración rara: {decl[:40]!r}")

    def handle_pi(self, data):
        self._mal(f"instrucción de proceso: {data[:40]!r}")

    def unknown_decl(self, data):
        self._mal(f"declaración desconocida (CDATA): {data[:40]!r}")


def revisar(pagina: str) -> Revisor:
    """Parsea la página y devuelve el revisor con todo lo anotado; no deja etiquetas sin cerrar."""
    revisor = Revisor()
    revisor.feed(pagina)
    revisor.close()
    if revisor.pila:
        revisor.problemas.append(f"etiquetas sin cerrar: {revisor.pila}")
    return revisor


def opinion(respuesta: str = "no", por_que: str = "Porque sí.") -> Opinion:
    return Opinion(respuesta, por_que)


def analisis(clave: str, sirve: str = "si", confianza: str = "media", texto: str | None = None, **cambios) -> Analisis:
    """Un análisis válido; con `texto`, todos sus campos de texto libre valen eso."""
    t = (lambda normal: texto) if texto is not None else (lambda normal: normal)
    datos = dict(
        clave=clave,
        sirve=sirve,
        ruido=sirve == "no",
        mejora_control=opinion("si", t("Agrega un control.")),
        mejora_software=opinion("no", t("No cambia lo que sale.")),
        adaptador_o_core="adaptador",
        lo_regala_el_proveedor=opinion("no_se", t("No se sabe.")),
        por_que_sirve_o_no=t("Sirve porque refuerza la revisión."),
        en_que_consiste_el_cambio=t("Un método nuevo de revisión."),
        que_implica_para_el_desarrollo=t("Hay que sumar un paso."),
        accion_propuesta=t("Leer la nota y decidir."),
        base="titulo",
        confianza=confianza,
        sospecha_de_instrucciones=False,
    )
    datos.update(cambios)
    return Analisis(**datos)


def novedad(n, **cambios) -> Novedad:
    """Una novedad con el título `TITULO_<n>` (único y sin caracteres raros: las pruebas lo buscan en la página)."""
    datos = dict(
        fuente="fuente-a",
        clave=f"f:{n}",
        titulo=f"TITULO_{n}",
        enlace=f"https://ejemplo.org/nota-{n}",
        fecha=datetime(2026, 10, 1, tzinfo=timezone.utc),
        frase=f"Frase de la novedad {n}.",
    )
    datos.update(cambios)
    return Novedad(**datos)


def corrida_de(novedades_y_analisis, **cambios) -> Corrida:
    """Una corrida con esas (novedad, análisis); un análisis None deja la novedad sin analizar."""
    analizadas = {n.clave: a for n, a in novedades_y_analisis if a is not None}
    sin = {n.clave: "falló dos veces" for n, a in novedades_y_analisis if a is None}
    datos = dict(
        generado=AHORA,
        resultado=Resultado(analizadas=analizadas, sin_analizar=sin),
        nuevas=[n for n, _a in novedades_y_analisis],
        desde=AHORA - timedelta(days=28),
        hasta=AHORA,
        leidas=len(novedades_y_analisis),
        cobertura={"fuente-a": Cobertura(estado="ok", en_fuente=3, en_ventana=3, cubre_desde=AHORA - timedelta(days=60))},
        fuentes={"fuente-a": "Fuente A"},
    )
    datos.update(cambios)
    return Corrida(**datos)


def corrida_hostil(p: str, enums_hostiles: bool = False, idea: bool = False) -> Corrida:
    """Una corrida con `p` en TODOS los campos de texto: título, frase, fuente, clave, cada campo del análisis, motivos,
    nombres de fuente, motivo de cobertura, aviso de corrida cortada e idea. Con `enums_hostiles`, también en los
    campos que normalmente son un valor de una lista cerrada (veredicto, confianza, base, estados...)."""
    h = (lambda normal: p) if enums_hostiles else (lambda normal: normal)

    def un_analisis(clave: str, sirve: str) -> Analisis:
        op = Opinion(h("si"), p)
        veredicto = (p if enums_hostiles else "quizás") if sirve == "raro" else sirve
        return Analisis(
            clave=clave,
            sirve=veredicto,
            ruido=False,
            mejora_control=op,
            mejora_software=op,
            adaptador_o_core=h("core"),
            lo_regala_el_proveedor=op,
            por_que_sirve_o_no=p,
            en_que_consiste_el_cambio=p,
            que_implica_para_el_desarrollo=p,
            accion_propuesta=p,
            base=h("articulo"),
            confianza=h("alta"),
            sospecha_de_instrucciones=sirve == "tal_vez",
            pasada=2,
            articulo=InfoArticulo(estado=h("error"), motivo=p, recortado=True, instrucciones_detectadas=sirve == "si"),
            sospecha_mecanica=sirve == "no",
        )

    if idea:
        resultado = Resultado(analizadas={CLAVE_IDEA: un_analisis(CLAVE_IDEA, "si")})
        return Corrida(generado=AHORA, resultado=resultado, idea=p)
    novedades = []
    analizadas = {}
    for i, sirve in enumerate(("si", "tal_vez", "no", "raro", "si")):
        nov = novedad(
            i,
            clave=f"{i}:{p}",
            titulo=p,
            frase=p,
            fuente=p if i == 3 else "f1",
            enlace=ENLACE_BUENO if i != 2 else p,
        )
        novedades.append(nov)
        analizadas[nov.clave] = un_analisis(nov.clave, sirve)
    sin_analizar_nov = novedad(9, clave=f"9:{p}", titulo=p, fuente="f2")
    novedades.append(sin_analizar_nov)
    resultado = Resultado(
        analizadas=analizadas,
        sin_analizar={sin_analizar_nov.clave: p, f"sin-novedad:{p}": p},
        abortada=p,
    )
    cobertura = {
        "f1": Cobertura(estado=h("ok"), motivo=p),
        "f2": Cobertura(estado=h("vacia"), motivo=p),
        p: Cobertura(estado=h("error"), motivo=p, cubre_desde=AHORA),
    }
    return Corrida(
        generado=AHORA,
        resultado=resultado,
        nuevas=novedades,
        desde=AHORA - timedelta(days=28),
        hasta=AHORA,
        leidas=len(novedades),
        cobertura=cobertura,
        fuentes={"f1": p, "f2": p},
        sin_articulos=True,
    )
