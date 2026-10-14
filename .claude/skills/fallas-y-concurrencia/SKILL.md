---
name: fallas-y-concurrencia
description: Pruebas con fallas inyectadas y con operaciones simultáneas. Usala en cada cambio que guarda o borra datos, que corre trabajos a la vez (pedidos web, procesos en segundo plano, interfaz y línea de comandos sobre los mismos datos), o que hace algo que cuesta plata o tiempo y no se debe repetir; y para reproducir un hallazgo de la revisión de arquitectura de esas clases.
---

# Pruebas con fallas inyectadas y concurrencia

Una suite que sólo prueba el camino feliz no ve los defectos más caros: un
registro escrito a medias que después impide abrir todo lo demás, dos
guardados que reciben el mismo identificador, un error al final que borra lo
que ya se había pagado. Estas pruebas los encuentran **forzando** la falla y
el orden, sin depender de la suerte.

## Las garantías que se exigen siempre

1. **Nada queda a medias.** Si una operación falla en cualquier punto, lo que
   quedó en disco (o en la base) se puede leer entero: o el estado de antes,
   o el de después. Nunca un registro cortado.
2. **Lo de antes sigue.** Una falla al guardar algo nuevo no daña ni hace
   ilegible lo que ya estaba.
3. **Dos operaciones a la vez no se pisan.** Ningún guardado se pierde,
   ningún identificador se repite, ninguna actualización se come a otra
   (leer, modificar y escribir sin exclusión es el caso típico).
4. **Lo que costó no se tira.** Si una etapa cara (una llamada paga, un
   proceso largo) ya terminó, un error en una etapa posterior no la borra:
   se puede reintentar desde donde falló.
5. **La exclusión está donde están los escritores.** Si los mismos datos los
   tocan la interfaz, la línea de comandos y procesos en segundo plano, la
   garantía tiene que valer entre todos ellos: un candado dentro de un solo
   proceso no alcanza; tiene que estar en el disco o en la base.
6. **Nada de un pedido se filtra a otro.** Lo que vive más que un pedido
   (variables de módulo, cachés, valores por defecto que se modifican,
   objetos reutilizados, nombres de archivos temporales armados con la hora o
   un contador) no puede mezclar datos de dos pedidos ni hacer que dos
   pedidos simultáneos usen el mismo archivo.

## Cómo se prueba la falla

1. Identificá la operación (guardar, actualizar, borrar, procesar) y sus
   **fronteras de escritura**: cada apertura para escribir, cada cierre, cada
   renombre o reemplazo, cada borrado, cada carpeta creada.
2. Corré la operación **una vez por frontera**, desde un estado inicial nuevo
   cada vez, haciendo que falle justo en esa frontera. Dos modos:
   - **error**: la operación recibe un error de disco; el código puede
     atraparlo y deshacer, y se prueba si deshace bien;
   - **corte**: el proceso muere; no corre ningún manejo de errores.
   En la frontera de cierre, el archivo queda escrito a medias.
3. Después de cada corte, verificá las garantías 1, 2 y 4: listar y abrir
   todo funciona, lo de antes está, lo caro sigue disponible.
4. Un corte que deja el estado roto es un hallazgo: la prueba que lo muestra
   queda en la suite, en rojo, hasta que se arregle.

## Cómo se prueba la concurrencia

1. Tomá dos operaciones que pueden ocurrir a la vez sobre los mismos datos
   (dos guardados, guardar y listar, dos cambios de configuración, interfaz y
   línea de comandos).
2. **No las lances "a la vez" esperando que choquen**: eso depende de la
   suerte y casi nunca falla en la máquina del que prueba. Forzá el orden:
   cada operación avanza sólo cuando le toca, y los puntos donde se cambia de
   una a otra son sus accesos a los datos (leer, listar, mirar si existe,
   escribir, renombrar). Probá todos los órdenes con uno y con dos cambios.
3. Para "dos procesos", usá dos instancias independientes del componente,
   sin nada compartido en memoria: la reserva entre ellas tiene que estar en
   los datos.
4. Después de cada orden, verificá la garantía 3: los dos resultados están,
   con identificadores distintos, y nada se perdió.

## Cómo se prueba el estado compartido en memoria

El planificador de órdenes cambia de operación en los accesos a datos; una
mezcla en memoria no pasa por ahí. Para la garantía 6 hay una prueba más
simple y determinista:

1. Llamá a la función con una entrada A y guardá una copia profunda de lo que
   devolvió.
2. Llamala de nuevo con una entrada B distinta.
3. Lo que devolvió la primera llamada **no puede haber cambiado**, y el
   resultado de la segunda **no puede contener nada de A**.

Eso atrapa los valores por defecto que se modifican, las listas o
diccionarios de módulo que se devuelven y se reutilizan, y las cachés sin
clave suficiente. Para los nombres temporales: generá dos nombres "en el
mismo instante" (con el reloj fijado) y verificá que son distintos.

## Lo que suele estar mal, y la forma correcta

| Mal | Bien |
|---|---|
| Escribir directo sobre el archivo final | Escribir a un temporal de nombre único y reemplazar de una vez |
| Elegir el próximo identificador mirando cuáles existen | Reservarlo con una creación exclusiva (falla si ya existe) o en la base |
| Leer la configuración, cambiarla y escribirla sin exclusión | Exclusión entre procesos alrededor de leer-modificar-escribir |
| Al fallar, borrar la carpeta de trabajo entera | Separar lo confirmado de lo que está en curso; reintentar desde la etapa que falló |
| Ignorar registros ilegibles al listar | Reportarlos con nombre: ignorarlos esconde la corrupción |
| Un valor por defecto que se modifica, o una lista de módulo que se devuelve | Un objeto nuevo en cada llamada |
| Nombre temporal armado con la hora o un contador | Nombre aleatorio, o creación exclusiva |

## Herramientas

Si existe `kits/<lenguaje>/` para el lenguaje del proyecto, trae un cortador
de fronteras y un planificador de órdenes listos; su README dice cómo
usarlos. Si no, escribí lo mismo con lo que el lenguaje trae: interceptar las
operaciones de archivos del componente es lo único que hace falta.

## Qué se reporta

Cada problema como hallazgo en el formato común (habilidad `hallazgos`), con
capacidad `fallas` o `concurrencia`, la frontera u orden exactos que lo
muestran, y la prueba que lo reproduce.
