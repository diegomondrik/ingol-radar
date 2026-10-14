---
name: hallazgos
description: El formato común de los hallazgos de los kits, los revisores y el juez externo, y el registro de hallazgos de un proyecto que lee su tablero. Usala cada vez que reportes un hallazgo o actualices su estado.
---

# Hallazgos: un solo formato

Todos los que encuentran defectos (el arquitecto, el probador, los revisores,
el juez externo) los dejan con la misma forma. Así se pueden sumar, comparar
entre corridas y mostrar en el tablero del proyecto sin rehacer nada. El
esquema es `hallazgos.schema.json`, el mismo archivo en dos lugares: en un
proyecto hecho con `ingol init`, al lado de esta habilidad
(`.claude/skills/hallazgos/hallazgos.schema.json`); en INGOL,
`kits/hallazgos.schema.json`. La carpeta de herramientas de los kits trae un
validador que aplica las mismas reglas.

## Un informe: lo que deja una corrida

Una lista de hallazgos. Cada uno tiene, como mínimo:

| Campo | Qué es |
|---|---|
| `id` | Identificador único dentro del proyecto |
| `severidad` | `P0` pérdida o corrupción de datos, o agujero de seguridad explotable; `P1` falla que un usuario normal va a encontrar, o defecto de diseño que impide vender o mantener; `P2` problema real que no bloquea; `P3` mejora |
| `título` | Una línea, en lenguaje llano |
| `evidencia` | Qué se ejecutó y qué se vio, o qué se leyó |
| `ejecutado_o_leido` | Empieza por `ejecutado`, `leído` o `inferido` |

Y, cuando corresponde: `área`, `archivo`, `línea`, `commit`, `descripción`,
qué capacidad lo habría evitado, el arreglo sugerido y el límite de la
evidencia. Campos de más se aceptan.

## El registro: el archivo vivo del proyecto

Un objeto con `proyecto`, `corridas` y `hallazgos`, en
`docs/hallazgos/registro.yaml` (o `.json`) del repositorio del proyecto.
**Nunca dentro de la carpeta `.ingol`**: ahí INGOL sólo acepta sus registros
canónicos y rechaza cualquier otro archivo nuevo (control TP-12). Las zonas
sensibles del proyecto van al lado, en `docs/hallazgos/zonas-sensibles.txt`.

Es un único archivo vivo: si dos trabajos en paralelo lo editan, chocan al
integrarse, como cualquier archivo compartido. Mientras eso no se resuelva
(se decide con el tablero, en D2), una corrida agrega sus hallazgos al final
y un cambio de estado toca sólo la línea de ese hallazgo.

**Cada corrida**: `id`, `tipo` (`arquitectura`, `probador`, `juez-externo`,
`revisor`, `otro`), `fecha` (AAAA-MM-DD), `commit` revisado; y, si se sabe,
`duracion_min`, `modelo`, `esfuerzo` y `cuenta`. Es lo que el tablero usa
para mostrar cuándo se revisó y cuánto costó, y lo que usa el script de
"toca revisión" para saber cuándo fue la última de arquitectura.

**Cada hallazgo** tiene lo del informe, más:

| Campo | Qué es |
|---|---|
| `estado` | `abierto`, `cerrado` o `aceptado` |
| `capacidad` | Qué lo encontró: `arquitectura`, `fallas`, `concurrencia`, `entradas`, `resultado` u `otra` |
| `corrida` | El `id` de la corrida que lo encontró (tiene que existir) |
| `fecha` | Cuándo se encontró |
| `prueba` | La prueba que lo reproduce, si la hay |
| `cerrado_por` | Obligatorio si está cerrado: el cambio que lo cerró (PR o commit) |
| `decision` | Obligatorio si está aceptado: quién lo aceptó y dónde quedó escrito |

## Reglas

- **Se reproduce o se dice.** Un P0 o P1 sin reproducción ejecutada va como
  `leído` o `inferido` y no cuenta como encontrado.
- **No se infla.** Diez hallazgos fundados valen más que cincuenta
  genéricos. Si algo está bien, se dice con la misma claridad.
- **Un hallazgo no se borra.** Se cierra (con el cambio que lo cerró) o se
  acepta (con la decisión de quien puede aceptarlo). Así el tablero muestra
  la historia, no sólo el presente.
- **Aceptar un P0 o P1 es decisión del owner del proyecto**, no del que lo
  encontró ni del que lo tiene que arreglar.
- Validá el archivo antes de dejarlo: un registro que no pasa el validador no
  lo puede leer el tablero.
