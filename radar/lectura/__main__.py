"""`python -m radar.lectura [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--json]`

Lee las fuentes reales y muestra la cobertura por fuente y los títulos. No toca la memoria
ni escribe archivos.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone

from .descarga import descargar
from .fuentes import FUENTES
from .lector import DIAS_POR_DEFECTO, leer, ventana

_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def _fecha(valor: str) -> date:
    if not _FECHA.fullmatch(valor):
        raise argparse.ArgumentTypeError(f"{valor!r} no es una fecha AAAA-MM-DD")
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{valor!r} no es una fecha válida") from exc


def _dia(fecha: datetime | None) -> str:
    return fecha.date().isoformat() if fecha else "-"


def main(argv=None, fetcher=descargar, ahora=None, salida=None) -> int:
    salida = salida or sys.stdout
    parser = argparse.ArgumentParser(
        prog="python -m radar.lectura",
        description="Lee las fuentes públicas y muestra la cobertura y los títulos. No guarda nada.",
    )
    parser.add_argument("--desde", type=_fecha, help=f"primer día de la ventana (por defecto, hace {DIAS_POR_DEFECTO} días)")
    parser.add_argument("--hasta", type=_fecha, help="último día de la ventana, inclusive (por defecto, ahora)")
    parser.add_argument("--json", action="store_true", help="salida en JSON")
    args = parser.parse_args(argv)

    ahora = ahora or datetime.now(timezone.utc)
    try:
        desde, hasta = ventana(args.desde, args.hasta, ahora)
    except ValueError:
        parser.error("--desde es posterior a --hasta")

    novedades, cobertura = leer(FUENTES, desde, hasta, fetcher, ahora=ahora)

    if args.json:
        datos = {
            "ventana": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
            "cobertura": {fid: c.a_dict() for fid, c in cobertura.items()},
            "novedades": [n.a_dict() for n in novedades],
        }
        salida.write(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
    else:
        salida.write(f"Ventana: {_dia(desde)} a {_dia(hasta)} (UTC)\n\nCobertura por fuente\n")
        for fuente in FUENTES:
            c = cobertura[fuente.id]
            linea = (
                f"  {fuente.id:<22} {c.estado:<6} en_fuente={c.en_fuente} en_ventana={c.en_ventana} "
                f"sin_fecha={c.sin_fecha} descartadas={c.descartadas} recortadas={c.recortadas} "
                f"cubre_desde={_dia(c.cubre_desde)}"
            )
            salida.write(linea + "\n")
            if c.motivo:
                salida.write(f"    motivo: {c.motivo}\n")
        salida.write(f"\nNovedades en la ventana: {len(novedades)}\n")
        for n in novedades:
            salida.write(f"  [{n.fuente}] {_dia(n.fecha)}  {n.titulo}\n    {n.enlace}\n")
    return 1 if all(c.estado == "error" for c in cobertura.values()) else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
