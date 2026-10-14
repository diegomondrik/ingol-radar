# Propuesta: radar de novedades para INGOL

Fuentes: `[pedido]` (`material/pedido.md`: la decisión D-248, el punto 5 y los riesgos del contrato de D4, textuales),
`[P1]` a `[P4]` (entrevista, abajo), `[archivo: nombre — "frase corta"]`. Lo que no tiene fuente dice `[supuesto]`. El
material crudo está fuera de git, en `material/` [archivo: .gitignore — "material/"].

## 1. El pedido

El owner de INGOL [pedido: "El owner"] pide un programa para estar "atentos de informacion de IA que pueda ser
relevante para mejorar ingol" [P1]: lee una lista fija de fuentes públicas, descarta lo ya visto y el ruido, y entrega
una página con las novedades que le importan a INGOL [pedido]. El análisis principal lo hace la IA: "el analisis
principal lo tenes que hacer vos" [P3].

## 2. La solución

Un programa que corre en la máquina del owner [pedido: "el programa corre en tu máquina"], escrito en Python con su
biblioteca estándar [supuesto], en cuatro partes:

1. **Lectura**: baja los feeds y páginas de la lista (parte 4) [pedido] y se queda, de cada novedad, con su enlace y
   una frase de la fuente [pedido: "cada una con enlace, una frase de la fuente"], más título y fecha [supuesto]. Dice
   por fuente desde qué fecha cubre [pedido: "el resumen dice por fuente desde qué fecha cubre"] y cuándo una fuente no
   trajo nada [pedido: "salvo que el programa diga cuándo una fuente no trajo nada"].
2. **Memoria**: guarda lo ya visto y no lo repite [pedido: "descarta lo ya visto"].
3. **Análisis**: por cada novedad, Claude responde las preguntas que INGOL le hace a una novedad [pedido: "¿mejora el
   control?, ¿mejora el software que sale?, ¿es un dato de adaptador o toca el Core?, ¿lo regala el proveedor?"] y da su opinión: si le sirve a INGOL o no, por qué, en qué consiste el cambio
   y qué implica para el desarrollo [P3, P4]. El ruido se descarta con esa misma opinión [supuesto]. Si el owner trae
   una idea propia, se analiza igual [P3: "si te lo digo lo debes analizar"]. Lo que propone es consejo [pedido: "lo
   que propone es consejo, como un análisis externo"].
4. **Página**: un archivo HTML [supuesto] con las novedades numeradas [P3: "sí a la 2 y la 5"], las que sirven primero
   [supuesto], cada una con enlace, frase, análisis y acción propuesta [pedido, P4], y la cobertura por fuente.

El owner marca diciéndoselo a la sesión de INGOL [P3: "la opcion C"]; lo aprobado entra a INGOL por el camino de
siempre [pedido: "lo aprobado entra a INGOL por el camino de siempre"].

## 3. Entregables y plazos

| Entregable | Para cuándo (estimado) | Fuente |
|---|---|---|
| Primer resumen: las últimas cuatro semanas, corrido de una vez | Antes del 2026-11-03 [supuesto] | [pedido: "las últimas cuatro semanas"] |
| El programa, en este repositorio, con sus pruebas | Igual [supuesto] | [pedido] |

Para D4, no para el programa: la medida M1 cuenta desde el 2026-10-13 [pedido: "M1 cuenta desde el 2026-10-13"] y los
resúmenes se cuentan en `d4-results.md` de INGOL [pedido: "AC08 los cuenta en `d4-results.md`"].

## 4. Herramientas y datos

- Python y Claude Code (`claude`) en la máquina del owner [supuesto], con su suscripción de Claude [P2: "quiero B."].
- Sin datos de clientes [pedido: "no prueba datos ni archivos de un cliente"].
- Resúmenes y memoria fuera de git [pedido: "quedan fuera de git"], en `generated/` [archivo: .gitignore —
  "generated/"]. Las pruebas usan recortes mínimos [pedido: "título, fecha, enlace y una frase"].
- Lo que viene de internet no es confiable [pedido: "no es confiable"]: el modelo corre sin herramientas y con respuesta de forma fija [pedido:
  "sin herramientas y con una respuesta de forma fija"]; una respuesta que no tiene esa forma deja la novedad como
  «sin analizar» [supuesto].
- INGOL no se copia al proyecto: la CI lo baja con la llave de solo lectura [pedido: "cada verificación lo baja con
  la llave de solo lectura"], el secreto `INGOL_TRUSTED_READ_TOKEN` [archivo: .github/workflows/ingol-bootstrap.yml —
  "INGOL_TRUSTED_READ_TOKEN"]. El repositorio es público [pedido: "Repositorio público, con INGOL afuera"]; D-248 decía
  privado y lo cambió D-249 [pedido: "un proyecto nuevo y privado"].

| Efecto | Detalle | Fuente |
|---|---|---|
| Lectura por la red | Anthropic noticias e ingeniería (página), versiones de Claude Code (feed), OpenAI (feed), Google para desarrolladores (feed), novedades de GitHub (feed), IBM Technology en YouTube (feed), METR (página), Simon Willison (feed), martinfowler.com (feed). A verificar en el trabajo 2: notas de la plataforma Claude, versiones de Codex, OWASP GenAI y OpenSSF; la que no se pueda leer pasa a «para después» | [archivo: fuentes-2026-10-13.md — "Anillo 1", "Anillo 2", "Anillo 3"]; lo de verificar, [supuesto] |
| Proveedor de modelo | Claude, por la suscripción del owner, con `claude -p` | [P2]; `claude -p`, [supuesto] |
| Clave | Ninguna nueva: usa la sesión de Claude ya iniciada en la máquina | [P2: "sin clave nueva"]; la sesión, [supuesto] |
| Tope por corrida | Sin gasto en dinero; consume cuota. Hasta 300 novedades analizadas por corrida, contando un reintento de las que fallen; si la ventana trae más, la página dice cuántas quedaron sin analizar | [P2: "consume tu cuota"]; el número y lo que pasa si se supera, [supuesto] |
| GitHub del proyecto | Ramas, subida, PR e integración en `diegomondrik/ingol-radar` con la CI en verde; lo que pida firma, la firma el owner | [supuesto: lo autoriza el «sí» a esta propuesta] |

## 5. Plan

Cada trabajo con su contrato, sus pruebas y la CI [pedido: "Se recorre entero"], controlado contra esta propuesta [archivo: entrevista.md —
"confirmar que el trabajo se controla paso a paso contra el contrato"]. Las pruebas corren en local y su salida va en
el PR [pedido: "su salida va en el texto del PR"].

| # | Trabajo | Superficies (rutas concretas) | Lo que dice `ingol work lane` (2026-10-14) |
|---|---|---|---|
| 0 | Esta propuesta | `docs/propuesta.md` | critical: no; lane: standard; approvals: none; fast: no |
| 1 | Rutas sensibles de la parte 9 | `.ingol/policies/fast-lane.yaml`, `docs/propuesta.md`, `docs/propuesta.html` | critical: yes; lane: controlled; approvals: owner_approval; fast: no |
| 2 | Lectura y memoria, con pruebas sobre recortes, dos ventanas seguidas y entradas armadas | `radar/lectura/**`, `radar/memoria/**`, `tests/lectura/**` | critical: no; lane: standard; approvals: none; fast: no |
| 3 | Análisis con Claude, con pruebas de entradas armadas | `radar/analisis/**`, `tests/analisis/**` | critical: no; lane: standard; approvals: none; fast: no |
| 4 | Página, comando y primer resumen | `radar/pagina/**`, `radar/__main__.py`, `tests/pagina/**`, `README.md` | critical: no; lane: standard; approvals: none; fast: no |

Las pruebas de entradas armadas siguen la habilidad `entradas-adversariales` [pedido: "aplica la habilidad de
entradas adversariales"]. Ninguna superficie es rápida: el camino rápido está cerrado [archivo: CLAUDE.md — "El camino
rápido está cerrado"]. En el trabajo 4, el primer resumen se contrasta con novedades que ya se sabía que importaban
[pedido: "contra novedades que ya se sabe que importaban"]: las que INGOL ya registró en su `EXTERNAL_LANDSCAPE.md` con
fecha dentro de esas cuatro semanas, no una lista del owner [supuesto].

## 6. Riesgos y supuestos

- Que `claude -p` corra sin herramientas [supuesto; se verifica en el trabajo 3; si no se puede, se vuelve a elegir
  entre las opciones de P2].
- Una fuente leída desde su página puede quedar muda si cambia el diseño [pedido: "un cambio de diseño las deja
  mudas"]; la página lo dice (parte 2).
- Cuatro feeds no llegan a cuatro semanas [pedido: "cuatro feeds no llegan a cuatro semanas"]: no se paginan y el
  resumen dice desde cuándo cubre cada uno [supuesto: alcanza para el primer resumen].
- La salida de `ingol work lane` de los trabajos 2 y 3 es de hoy: cuando el trabajo 1 marque esas carpetas como rutas
  sensibles, pasan a crítico [archivo: .ingol/policies/fast-lane.yaml — "un cambio que las toca es crítico"] y piden la
  firma del dueño [archivo: CLAUDE.md — "firma del dueño"]; se vuelve a correr antes de cada trabajo [supuesto].
- El resumen puede traer ruido o perder lo importante [pedido: "El resumen puede traer ruido o perder lo importante"];
  por eso el contraste del trabajo 4.
- El repositorio público deja ver el método de INGOL, y ante un defecto del verificador la llave podría leer su código
  [pedido: "deja ver el método de INGOL"]; la llave vence y sólo lee [pedido: "vence, sólo lee contenido"].
- El análisis puede sonar bien y estar mal [supuesto]; el owner aprueba o no cada cambio antes de que toque INGOL
  [pedido: "Vos marcás sí o no"].

## 7. Fuera de alcance

- Programar la corrida semanal [pedido: "programarla es uso, no desarrollo"].
- Que el programa escriba en INGOL [pedido: "no tiene acceso de escritura a INGOL"].
- Correr en GitHub Actions [pedido: "Nada de eso corre en GitHub Actions"]; sumar las pruebas del radar a la CI, que
  gasta minutos [pedido: "que gasta minutos"; supuesto: no se suman].
- Botones en la página o un archivo para marcar [P3: "la opcion C"]. Una API paga o una clave nueva [P2: "quiero B."].
- Lo que este caso no prueba: datos privados y archivos de un cliente, una reunión con un tercero y la aprobación de
  alguien que no sea el owner, que prueba D5 [pedido: "Lo que no prueba"].
- Entregar INGOL como paquete publicado, con su licencia [pedido: "La forma «paquete publicado» (B) queda para antes de
  vender"].
- Fuentes: IBM Think y el blog de IBM Research [archivo: fuentes-2026-10-13.md — "Descartadas de IBM"]; prensa
  general, redes sociales, video que no sea el aviso de IBM y newsletters pagas [archivo: fuentes-2026-10-13.md —
  "Fuera: prensa general"]; Cursor y Cognition [archivo: fuentes-2026-10-13.md — "Para después, si hace falta"];
  prensa de IBM, Agentic AI Foundation, DORA, Thoughtworks, Latent Space y Hamel Husain, sin verificar o de baja
  frecuencia [supuesto: quedan para después].

## 8. Qué falta del cliente

- Ninguna: con la entrevista se pueden llenar las nueve partes [archivo: entrevista.md — "se pueden llenar las nueve
  partes"].

## 9. Nivel y zonas

- Nivel 1, personal [archivo: CLAUDE.md — "Nivel 1: personal"]: una herramienta del owner, en su máquina [pedido],
  sin datos de terceros [pedido]; el repositorio público no cambia el nivel [supuesto].
- Zonas: ninguna. Una zona de nivel 2 subiría el piso del camino crítico de todo el proyecto (se midió con el
  verificador el 2026-10-14); el owner eligió no ponerlas (ver «Aprobación»).
- Rutas sensibles: `radar/lectura/**` y `radar/analisis/**` [pedido: "son las rutas sensibles que escribe el primer
  trabajo"].

## Preguntas y respuestas

| # | Pregunta | Respuesta, con sus palabras |
|---|---|---|
| P1 | ¿Qué problema concreto te resuelve el radar: qué te pasó, o qué te preocupa que pase, si no lo tenés? | "con el radar estamos atentos de informacion de IA que pueda ser relevante para mejorar ingol. lo que no entiendo es por que me haces la pregunta, si todo es fue parte de la informacion con la que se genero el contrato, es decir si no sabes para que hacemos la aplicacion, por que la podes empezar a hacer y como esta seguro de hacer lo que hace falta para cumplir el objetivo" |
| P2 | Para decidir qué novedades le importan a INGOL hace falta criterio. ¿Cuál de estas tres formas querés? (A) sin IA: el programa filtra por palabras clave; costo cero, más ruido para leer. (B) con IA usando tu suscripción de Claude desde tu máquina: sin clave nueva ni gasto extra, consume tu cuota. (C) con IA por API paga, con una clave nueva y un tope de gasto por corrida. | "quiero B." |
| P3 | Cuando el radar te muestre las novedades, ¿cómo querés marcar sí o no? (A) botones en la página; (B) un archivo de texto con una cruz; (C) diciéndoselo a la sesión de INGOL ("sí a la 2 y la 5"). | "si, elijo la opcion C, pero lo mas importante es que vos puedas analizar las noticias y decirme tu opinion de si le sirve a ingol o no. No se puede depender qeu un humano pueda tener el conocimiento y la imaginacion para confirmar como se puede mejorar. Si puede ser que ocurra y si te lo digo lo debes analizar para decirme si sirve o no, pero el analisis principal lo tenes que hacer vos" |
| P4 | Cuando veas el primer resumen (las últimas cuatro semanas), ¿qué tendría que tener para que digas «esto sirve»? | "la respuesta es lo mismo que dije antes. yo no soy el que analiza. Vos analizas, me explicas por que  consideras que serviria la mejora, en que consiste el cambio y que implica para el desarrollo." |

## Aprobación

- Quién: Carlos Diego Mondrik, owner de INGOL
- Fecha: 2026-10-14
- Sus palabras: "aprobada la propuesta para el radar."
- Cambio aprobado el 2026-10-14, antes del trabajo 1: sin zonas, sólo las dos rutas sensibles (parte 9). Sus palabras:
  "ok con la opcion de la decision"
