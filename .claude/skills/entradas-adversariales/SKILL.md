---
name: entradas-adversariales
description: Pruebas con entradas raras o armadas a propósito, y la regla de las formas equivalentes. Usala en cada cambio que lee algo que trae el usuario o un tercero (archivos de texto, documentos Office, XML, JSON, planillas, imágenes, nombres de archivo, plantillas) o que aplica un filtro de seguridad sobre una entrada.
---

# Entradas adversariales

El ejemplo con el que se programó casi nunca es lo que llega. Un texto con
otra codificación pierde la primera línea sin avisar; un documento con la
misma instrucción escrita de otra forma pasa un filtro; un archivo chico que
se infla al descomprimir llena la memoria.

## Las dos reglas que se prueban

1. **Formas equivalentes, mismo resultado.** Si dos entradas significan lo
   mismo para el usuario o para el programa que las va a abrir, el resultado
   tiene que ser el mismo. Una diferencia es un hallazgo, aunque ninguna de
   las dos "falle".
2. **Una entrada hostil se procesa o se rechaza con un error claro.** Nunca
   rompe con otro error, ni se cuelga, ni consume sin límite. "Error claro"
   es el error propio del proyecto para una entrada inválida, con un mensaje
   para el usuario; un error genérico de decodificación o de lectura que sube
   hasta arriba no lo es.

Y una regla de diseño para los filtros de seguridad: **se aplican sobre el
contenido ya interpretado**, igual a como lo va a interpretar el programa que
lo abre, nunca sobre los bytes. Un filtro de bytes se evade con una
representación equivalente.

## Dónde: todos los puntos de entrada

El catálogo se aplica a **cada** punto por donde entra algo de afuera, no
sólo a los archivos que sube el usuario: cuerpos y parámetros de pedidos web
(un cuerpo JSON también es texto: con BOM, en UTF-16), argumentos de la
línea de comandos, variables de entorno, archivos de configuración,
respuestas de servicios externos y del modelo de IA. Empezá por hacer la
lista de esos puntos; cada uno se prueba o queda escrito por qué no.

## Valores chicos: validación completa

Horas, fechas, números, identificadores, códigos y nombres se validan
**enteros**: un valor que empieza bien y termina mal se rechaza. Una
validación que sólo mira el principio (`12:30` acepta `12:30abc` o `12:30`
con un salto de línea después) deja pasar basura que después se interpreta
de otra forma. Probá cada intérprete de valores chicos con: basura al final,
salto de línea al final y en el medio, espacios alrededor, signo, ceros a la
izquierda, el valor vacío y el máximo.

## El catálogo, por tipo de entrada

**Texto.** Con y sin marca de orden de bytes (BOM); UTF-16 en los dos
órdenes; la codificación occidental de Windows; saltos de línea de Windows
(CRLF) y de Mac viejo (CR); sin salto final; acentos compuestos de otra forma
(normalización NFD). Hostiles: vacío, sólo una marca de orden, sólo espacios,
una línea de varios megabytes, bytes nulos, bytes inválidos, binario,
secuencias de escape de terminal, caracteres de dirección invertida.

**XML y documentos Office (que son zips con XML adentro).** La misma palabra
o instrucción con entidades numéricas (decimales, hexadecimales, con ceros a
la izquierda, sólo en una letra); otro prefijo para el mismo espacio de
nombres; la instrucción partida en varios fragmentos; en encabezados, pies,
notas y comentarios además del cuerpo; entidades que se expanden; un paquete
que pesa poco y descomprime cientos de megabytes. Un límite de tamaño mira el
tamaño descomprimido, no el del archivo subido.

**Nombres y rutas.** Subir de carpeta (`../`), rutas absolutas, nombres
reservados de Windows (`CON`, `NUL`, `COM1`, con o sin extensión), nombre que
termina en punto o espacio, dos puntos (flujos alternativos), nombres de más
de 255 caracteres, saltos de línea y nulos en el nombre, caracteres de
dirección invertida que disfrazan la extensión.

**Números y fechas.** Separadores de miles y decimales de los dos estilos,
negativos, cero, enormes, fechas sin año, años de dos cifras, día y mes
ambiguos.

**Estructuras (JSON, planillas).** Campos faltantes, de otro tipo, repetidos,
anidamiento profundo, celdas que empiezan con `=`, `+`, `-` o `@` (fórmulas
al exportar a planilla).

## Cómo se prueba

1. Para cada lector o filtro que el cambio toca, armá la entrada del ejemplo
   y generá sus formas equivalentes y sus hostiles con el catálogo.
2. Formas equivalentes: el resultado de cada una tiene que ser igual al de la
   forma base. Si el lector recibe un archivo, escribí cada forma en un
   archivo temporal.
3. Hostiles: cada una tiene que procesarse o rechazarse con el error propio
   del proyecto, dentro de un tiempo máximo.
4. Filtros: la entrada prohibida, escrita de todas las formas equivalentes,
   tiene que rechazarse siempre; y el paquete final que se entrega se vuelve
   a validar (lo que sale también es una entrada para el que lo abre).

## Herramientas

Si existe `kits/<lenguaje>/` para el lenguaje del proyecto, trae el catálogo
de formas y hostiles, el armado de paquetes Office mínimos y de zips que se
inflan, y las dos afirmaciones; su README dice cómo usarlos.

## Qué se reporta

Cada problema como hallazgo en el formato común (habilidad `hallazgos`), con
capacidad `entradas`, la forma exacta que lo muestra y la prueba que lo
reproduce. Una evasión de un filtro de seguridad es como mínimo P1.
