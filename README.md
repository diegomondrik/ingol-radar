# Radar de novedades para INGOL

El radar lee cada tanto una lista fija de sitios de inteligencia artificial (Anthropic, OpenAI, GitHub, Google y otros),
se queda con lo que es nuevo y le pide a Claude que opine, novedad por novedad, si le sirve a INGOL o no, por qué, en qué
consiste el cambio y qué implicaría para el desarrollo. Con eso arma una página que se abre en el navegador. El radar
aconseja; no cambia nada de INGOL.

## Cómo correr el primer resumen

Desde la carpeta del proyecto (`ingol-radar`), en una terminal:

```
python -m radar
```

- Mira las últimas cuatro semanas.
- **Puede tardar una o dos horas** y gastar buena parte del límite de uso de tu suscripción de Claude. Conviene dejarlo
  corriendo y no usar Claude para otra cosa mientras tanto.
- Necesita que Claude ya esté instalado y con tu sesión iniciada en esta máquina. Si hay una clave de API en la
  computadora, el radar se detiene antes de empezar (para no gastar plata).
- Si lo cortás con Ctrl+C, no se pierde nada: lo que no se alcanzó a analizar vuelve a aparecer la próxima vez.

## Dónde queda la página

Al terminar, el radar escribe en pantalla dónde dejó los archivos. Quedan en la carpeta `generated/resumenes/`:

- `resumen-AAAA-MM-DD_HHMMSS.html`: la página. Hacé doble clic para abrirla. La hora del nombre es UTC.
- `resumen-AAAA-MM-DD_HHMMSS.json`: la misma lista, numerada igual, para que la lea la sesión de INGOL. No hace falta abrirlo.

Nunca se pisa una página anterior. Las novedades que ya viste no se repiten en la corrida siguiente.

## Cómo aprobar

Cada novedad que sirve (o que quizás sirve) tiene un número. Para aprobar, decíselo a la sesión de INGOL:

> sí a la 2 y la 5

La sesión busca esos números en el archivo `.json` de la última página y sigue el camino de siempre de INGOL.

## Si una fuente dice «no trajo nada»

En la parte de arriba de la página aparece un aviso rojo y, abajo, la tabla «Cobertura por fuente». Una fuente que «no
trajo nada» o «falló» casi siempre significa que ese sitio cambió su diseño o estuvo caído. No es que no haya
novedades. Probá correr el radar de nuevo más tarde; si la misma fuente sigue muda, avisale a la sesión de INGOL para que
arregle su lectura.

## Opciones

| Opción | Para qué sirve |
|---|---|
| `--desde 2026-10-01` | Primer día que se mira (por defecto, hace 28 días). |
| `--hasta 2026-10-31` | Último día que se mira (por defecto, hoy). |
| `--sin-articulos` | No baja los artículos: es más rápido y gasta menos, pero el análisis es menos fino. |
| `--idea "texto"` | Analiza una idea tuya en vez de leer los sitios. Escribe su propia página y no toca lo ya visto. No se puede combinar con las tres opciones de arriba. |
| `--carpeta <carpeta>` | Deja la página en otra carpeta. |

Ejemplo: `python -m radar --idea "Que INGOL avise cuando un proveedor cambie sus precios"`.

## Para la sesión de INGOL

Pruebas: `python -m unittest discover -s tests/pagina -t .` (también `tests/analisis` y `tests/lectura`). Ninguna sale a
internet ni usa Claude.
