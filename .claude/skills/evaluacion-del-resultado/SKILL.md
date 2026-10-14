---
name: evaluacion-del-resultado
description: Evaluación de si el resultado le sirve al usuario, sobre todo en apps que usan IA o que transforman contenido (resúmenes, informes, extracciones, selección de imágenes o fragmentos). Usala en cada cambio que toca qué se le pide al modelo, cómo se valida su respuesta, o qué parte de la entrada llega a la salida; y al cerrar cada etapa.
---

# Evaluación del resultado

Una validación que mira la forma (los títulos están, en orden) acepta un
resultado vacío. Una optimización que corta la entrada pierde contenido
válido sin que nada falle. Ninguna prueba de "que no explote" lo ve: hay que
mirar el resultado contra la fuente.

## Tres capas, de la más barata a la más cara

### 1. Controles mecánicos, en cada cambio, sin IA y sin costo

Se comprueban sobre cada resultado, en la suite del proyecto:

- **Contenido por sección.** Cada sección obligatoria tiene algo escrito.
  "No hubo decisiones" es contenido (decir que no hubo es distinto de dejarlo
  en blanco); una viñeta sin texto no lo es.
- **Trazabilidad.** Cada cifra y cada fecha del resultado está en la fuente.
  La fecha con su año: si la fuente dice un año y el resultado otro, está
  mal; si la fuente no dice año y el resultado lo agrega, es inventado. Las
  cifras se comparan normalizadas (separadores de miles y decimales de los
  dos estilos).
- **Cobertura.** Lo que entra está en lo que sale: ninguna intervención,
  diapositiva, fila o parte de la fuente se pierde, y en especial **el
  final**, que es lo primero que corta un límite mal puesto. Un recorte
  basado en una marca de inicio ("la última intervención empieza acá") no
  sabe dónde termina la fuente.

- **Fidelidad en cada transformación.** Entre la respuesta del modelo (o la
  fuente) y lo que se entrega hay pasos: leer la respuesta, juntar sus
  partes, armar el documento, escribirlo. Cada paso conserva **todo** el
  contenido (todas las partes de una respuesta que llega en varias, no sólo
  la primera) y **el texto literal**: espacios, saltos de línea, símbolos,
  mayúsculas. Se prueba con ida y vuelta: generar el documento final,
  volver a leerlo y comparar con el texto que entró, con casos que tengan
  espacios dobles, saltos, tabulaciones, símbolos y una respuesta en varias
  partes.

Si una validación del proyecto deja pasar algo que estos controles
rechazan, eso es un hallazgo de la validación.

### 2. Un corpus de casos, en cada cambio

Una carpeta de casos, uno por archivo, cada uno con la fuente, una
respuesta, si se espera **aceptar** o **rechazar**, el motivo, y lo que la
respuesta tiene que contener. El corpus no fija el texto exacto (la IA
cambia de redacción): fija **propiedades**.

Tiene que tener, como mínimo:

- respuestas buenas, incluida una que dice explícitamente que algo no hubo;
- respuestas malas armadas a propósito, una por clase: una sección vacía, un
  año cambiado, una cifra inventada, el final perdido, una respuesta cortada
  a la mitad.

La validación del proyecto corre sobre todo el corpus: tiene que rechazar
todas las malas y aceptar todas las buenas. Las respuestas del corpus están
guardadas o armadas a mano: **el corpus no llama a ningún modelo** y no
cuesta nada. Si hace falta generar respuestas reales, eso es aparte y con el
gasto autorizado del proyecto.

Con datos de clientes: el corpus usa casos sintéticos o anonimizados. Una
fuente real de un cliente no se guarda en el repositorio.

### 3. Evaluación humana, al cerrar una etapa (opcional)

La planilla `planilla-humana.md` de esta carpeta: no más de diez minutos por
caso, sobre tres a cinco casos representativos, para alguien que conoce el
negocio y no necesita saber programar. Es la única capa que mide si el
resultado es **bueno**, no sólo si no está roto. Su resultado se guarda como
una corrida más.

## Qué se reporta

Cada problema como hallazgo en el formato común (habilidad `hallazgos`), con
capacidad `resultado`, el caso exacto que lo muestra y la prueba (o el caso
del corpus) que lo reproduce.

## Herramientas

Si existe `kits/<lenguaje>/` para el lenguaje del proyecto, trae los tres
controles mecánicos, el lector del corpus y la afirmación de que todas las
malas se rechazan y todas las buenas se aceptan; su README dice cómo usarlos.
