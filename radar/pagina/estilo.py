"""Lo fijo de la página: la política de contenido, el logo de INGOL y el estilo. Nada de esto trae datos."""

import base64
import hashlib

# El logo de docs/propuesta.html (trazo con currentColor; la chispa lleva el color de marca).
LOGO = (
    '<svg class="logo" viewBox="0 0 1120 300" role="img" aria-label="INGOL">'
    '<g fill="none" stroke="currentColor" stroke-width="15" stroke-linejoin="round">'
    '<path opacity=".36" stroke-width="2" d="M48 150H1062"/>'
    '<path d="M77 72V228"/><path d="M164 228V72L310 228V72"/>'
    '<path d="M544 72H424C355 72 325 105 325 150S355 228 424 228H544V166H464"/>'
    '<rect x="624" y="72" width="190" height="156" rx="75"/><path d="M891 72V228H1045"/>'
    "</g>"
    '<path class="spark" d="M719 96c0 29 15 44 44 44-29 0-44 15-44 44 0-29-15-44-44-44 29 0 44-15 44-44Z"/>'
    "</svg>"
)

ESTILO = """
:root{
  --bg:#f6f8fa;--panel:#ffffff;--ink:#0b1b2b;--muted:#5b6b7b;--line:#dde3ea;
  --brand:#0e8f7f;--brand-soft:#e3f6f3;--spark:#0e8f7f;
  --warn:#9a5b00;--warn-soft:#fff4e0;--crit:#b4232c;--crit-soft:#fdecee;
  --ok:#1f7a3a;--ok-soft:#e7f5ec;--info:#2457a6;--info-soft:#e8effb;
  --shadow:0 1px 2px rgba(11,27,43,.06),0 4px 16px rgba(11,27,43,.05);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0b1520;--panel:#111e2b;--ink:#e6edf3;--muted:#93a4b5;--line:#22334a;
  --brand:#42dac4;--brand-soft:#0f2e2b;--spark:#42dac4;
  --warn:#f0b35a;--warn-soft:#2e2412;--crit:#ff7a83;--crit-soft:#341a1d;
  --ok:#6fd38f;--ok-soft:#13281b;--info:#86b0ff;--info-soft:#16233a;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.25);
}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;overflow-wrap:anywhere}
.wrap{max-width:880px;margin:0 auto;padding:0 16px}
header.top{border-bottom:1px solid var(--line);background:var(--panel)}
header.top .wrap{display:flex;align-items:center;gap:16px;min-height:60px}
.logo{height:28px;width:auto;color:var(--ink);flex:none}
.logo .spark{fill:var(--spark)}
.fecha{color:var(--muted);font-size:.85rem;margin-left:auto;text-align:right}
h1{font-size:clamp(1.6rem,5vw,2.3rem);line-height:1.15;margin:28px 0 6px;letter-spacing:-.01em}
h2{font-size:1.25rem;margin:0 0 4px}
h3{font-size:1.05rem;margin:0;line-height:1.3}
.sub{color:var(--muted);margin:0 0 14px}
section{margin:32px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:10px;margin:18px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px;box-shadow:var(--shadow)}
.kpi b{display:block;font-size:1.7rem;line-height:1.1;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:.82rem}
.kpi.si b{color:var(--ok)}.kpi.tal b{color:var(--warn)}.kpi.sin b{color:var(--crit)}
.marcar{border:2px solid var(--brand);background:var(--brand-soft);border-radius:14px;padding:14px 18px;font-size:1.05rem}
.marcar strong{white-space:normal}
.aviso{border:1px solid var(--line);border-left:5px solid var(--warn);background:var(--warn-soft);border-radius:10px;padding:10px 14px;margin:14px 0}
.aviso.crit{border-left-color:var(--crit);background:var(--crit-soft)}
.aviso p{margin:0}
.card{display:grid;grid-template-columns:48px minmax(0,1fr);gap:14px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin:14px 0;box-shadow:var(--shadow)}
.card.v-si{border-left:5px solid var(--ok)}
.card.v-tal_vez{border-left:5px solid var(--warn)}
.num{width:48px;height:48px;border-radius:12px;background:var(--brand);color:var(--panel);display:grid;place-items:center;font-size:1.5rem;font-weight:800;font-variant-numeric:tabular-nums}
.cabeza{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.badge{display:inline-flex;align-items:center;font-size:.76rem;font-weight:700;padding:2px 10px;border-radius:999px;border:1px solid transparent;white-space:nowrap}
.b-ok{color:var(--ok);background:var(--ok-soft);border-color:var(--ok)}
.b-warn{color:var(--warn);background:var(--warn-soft);border-color:var(--warn)}
.b-crit{color:var(--crit);background:var(--crit-soft);border-color:var(--crit)}
.b-mute{color:var(--muted);background:transparent;border-color:var(--line)}
a{color:var(--brand)}
.meta{color:var(--muted);font-size:.86rem;margin:2px 0 8px}
.frase{margin:0 0 10px;padding:8px 12px;border-left:3px solid var(--line);color:var(--muted);font-style:italic}
.campo{margin:10px 0 0}
.campo b{display:block;font-size:.78rem;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
.campo p{margin:2px 0 0}
.accion{margin:12px 0 0;padding:10px 14px;border-left:4px solid var(--brand);background:var(--brand-soft);border-radius:0 10px 10px 0}
.accion b{display:block;font-size:.78rem;letter-spacing:.05em;text-transform:uppercase;color:var(--brand)}
.accion p{margin:2px 0 0;font-weight:600}
.nota{margin:8px 0 0;padding:6px 12px;border-radius:8px;font-size:.88rem;background:var(--warn-soft);color:var(--warn)}
.nota.fuerte{background:var(--crit-soft);color:var(--crit)}
.nota.gris{background:transparent;border:1px dashed var(--line);color:var(--muted)}
details{background:var(--panel);border:1px solid var(--line);border-radius:12px;margin:10px 0}
summary{cursor:pointer;padding:10px 14px;font-weight:600}
details .in{padding:0 14px 12px}
details.dentro{border-style:dashed;box-shadow:none;margin-top:12px}
dl.pregs{margin:0}
dl.pregs dt{font-weight:600;margin-top:8px}
dl.pregs dd{margin:2px 0 0;color:var(--muted)}
.linea{display:flex;gap:10px;align-items:baseline;padding:6px 0;border-top:1px solid var(--line)}
.linea:first-child{border-top:0}
.linea .n{flex:none;min-width:2ch;font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums}
.linea small{color:var(--muted)}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;font-size:.92rem}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.78rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
tr.fila-vacia td{background:var(--warn-soft)}
tr.fila-error td{background:var(--crit-soft)}
td.nombre{font-weight:600}
td small{display:block;color:var(--muted);font-weight:400}
ul.sin{margin:0;padding-left:1.1em}
ul.sin li{margin:8px 0}
.vacio{padding:18px;border:1px dashed var(--line);border-radius:12px;text-align:center;color:var(--muted)}
.idea{border-left:4px solid var(--info);background:var(--info-soft);padding:10px 14px;border-radius:0 10px 10px 0}
footer{border-top:1px solid var(--line);margin-top:40px;padding:18px 0 36px;color:var(--muted);font-size:.88rem}
@media (max-width:640px){
  .card{grid-template-columns:1fr}
  table,thead,tbody,tr,th,td{display:block}
  thead{display:none}
  tr{border-bottom:2px solid var(--line);padding:6px 0}
  td{border:0;padding:3px 10px}
  td[data-label]::before{content:attr(data-label) ": ";font-weight:600;color:var(--muted)}
  td.nombre::before{content:none}
}
"""


def hash_de_estilo(estilo: str) -> str:
    """La fuente de la política para ese texto exacto de `<style>`: `'sha256-<base64>'` de sus bytes UTF-8."""
    return "'sha256-" + base64.b64encode(hashlib.sha256(estilo.encode("utf-8")).digest()).decode("ascii") + "'"


# Sin JavaScript, sin recursos de afuera ni imágenes (el logo es SVG en línea): lo único permitido es el estilo de
# la propia página, y sólo el bloque cuyo hash coincide. Cambiar `ESTILO` cambia el hash solo.
CSP = f"default-src 'none'; style-src {hash_de_estilo(ESTILO)}; base-uri 'none'; form-action 'none'"
