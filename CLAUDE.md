# ingol-radar

@AGENT_BASE.md

Proyecto construido con INGOL. **Nivel 1: personal.** Revisión de seguridad por nivel: **encendida**. Qué es y para quién lo dice la propuesta aprobada (`docs/propuesta.md`).

## Zonas

Ninguna todavía: las escribe el primer trabajo después de la propuesta aprobada, cada una como zona y como ruta sensible.

## Comandos

- `ingol doctor`: el proyecto está sano.
- `ingol work lane <superficies>`: camino y firmas, antes de construir.
- `ingol work new`: contrato, plan y texto del PR del cambio hecho.
- `ingol verify run --work-item <id> --criterion <id> -- <comando>`: corre un control y guarda su salida.
- Las pruebas del proyecto las fija la propuesta.

## Qué pide aprobación

Lo que exige el verificador, para un cambio fuera de toda zona:

| Camino | Qué pide antes de integrar |
|---|---|
| Rápido | Nada más que la CI en verde |
| Normal | Nada más que la CI en verde |
| Crítico | firma del dueño (`owner_approval`) |

El camino rápido está cerrado hasta que el dueño del proyecto lo abra en `.ingol/policies/fast-lane.yaml`, con un cambio crítico. Un cambio dentro de una zona se juzga al nivel de la zona.
