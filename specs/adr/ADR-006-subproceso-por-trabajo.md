# ADR-006 — Un subproceso por trabajo en el backend web

Estado: Aceptado (decisión del usuario, 2026-09-25) · Fecha: 2026-09-25
Aplica a: backend web de I8 (`i8-api.md` §7, RFW-02, RFW-04, RFW-06). La CLI no cambia: ejecuta el trabajo en su propio proceso, como hasta ahora.

## Contexto
El backend web tiene que poder cancelar un trabajo en ejecución con la misma limpieza que Ctrl+C en la CLI (RF-13, CA-19, ADR-004). T-01 mostró que eso no se puede hacer dentro del proceso del servidor:

- **No hay forma de cancelar.** `audio-separator` no ofrece ninguna para un `separate()` en curso: bloquea durante minutos (unos 8 con `vocals`, más de 2 horas con `vocals-max`) dentro de código nativo (ONNX Runtime, PyTorch). Un hilo de Python no se puede interrumpir desde fuera.
- **Memoria.** El pico medido es de 3,2 GB (ADR-002). En un proceso de larga vida, el alojador de memoria no siempre devuelve esa RAM al sistema al terminar el trabajo.
- **Fallos.** Un error nativo del motor (segfault, o el OOM killer) mataría el servidor entero, con la cola y los SSE abiertos.
- **Señales.** La sección crítica de ADR-004 difiere `SIGINT`, y las señales en Python solo llegan al hilo principal. Solo funciona de forma limpia si el trabajo es el proceso principal que recibe la señal.

## Opciones
1. **Hilo del servidor.** No se puede cancelar, retiene la memoria y un fallo del motor tumba el servidor. Descartada.
2. **`asyncio` en el proceso del servidor.** El motor bloquea la CPU: acaba siendo la opción 1 con otra sintaxis. Descartada.
3. **Proceso trabajador de larga vida (pool).** Evitaría recargar el modelo en cada trabajo, pero eso son solo 4–9 s (ADR-002) frente a minutos de inferencia. A cambio, mantendría 3,2 GB residentes todo el tiempo y seguiría sin poder cancelar sin matar el proceso. Descartada.
4. **Cola externa (Celery, RQ, Redis).** Añade servicios que instalar y mantener en un producto local de un solo usuario (P9). Descartada.
5. **Un subproceso nuevo por trabajo.** Elegida.

## Decisión
- El proceso servidor (HTTP, cola, persistencia, SSE) lanza, para cada trabajo, un **subproceso nuevo** (`subprocess`, con el intérprete actual y un módulo propio de trabajador; nunca `fork`, que es inseguro con los hilos de PyTorch y ONNX Runtime). El subproceso ejecuta `SeparationService.run()` para ese trabajo y termina. Hay como máximo uno a la vez (cola en serie, `i8-api.md` §1).
- **Progreso entre procesos**: el trabajador usa un `ProgressReporter` que escribe **una línea JSON por evento** en su stdout:
  - `stage_started` y `stage_finished`, con la etapa;
  - `stage_failed`, con la etapa y el error (`code`, `message`, `hint`, `exit_code`);
  - al final, `result`, con el `SeparationResult` serializado.

  El stderr del trabajador va al log del trabajo. El servidor es el único que lee ese canal, actualiza el estado persistido del trabajo (es su único escritor) y reenvía los eventos al SSE. Ese reporter vive en el paquete del backend web, no en `domain`.
- **Cancelación**: el servidor envía la señal de interrupción al subproceso: `SIGINT` en POSIX, y `CTRL_BREAK_EVENT` en Windows (el subproceso se lanza con `CREATE_NEW_PROCESS_GROUP` y trata `SIGBREAK` como `KeyboardInterrupt`). El trabajador limpia según RF-13 y sale con 130. Si no ha salido tras un plazo de gracia (10 s, configurable), el servidor lo mata y borra él mismo el directorio del trabajo. Matarlo es seguro porque el backend web nunca usa `--overwrite`: no hay copias `.bak` que restaurar (`OutputRestoreError` es inalcanzable, ver `i8-api.md` §6).
- **Fallo del trabajador**: si termina con un código distinto de 0 y 130 sin haber emitido `stage_failed`, el trabajo pasa a `failed` con `code: "internal_error"` y `failed_stage` igual a la última etapa iniciada.
- **Muerte del servidor**: el trabajador vigila su stdin. Si se cierra, porque el servidor ha muerto, lo trata como una cancelación. Al arrancar de nuevo, el servidor marca los trabajos que estaban `running` como `failed` (`interrupted_by_restart`) y borra sus archivos parciales (RFW-02).
- El trabajador hereda el entorno del servidor, incluido `ORT_DISABLE_TELEMETRY=1` (RNF-12).

## Consecuencias
- Cancelar funciona igual que Ctrl+C en la CLI (CAW-04), y la memoria del motor vuelve al sistema al acabar cada trabajo.
- Un fallo nativo del motor solo mata su trabajo, no el servidor.
- Cada trabajo paga el arranque de un intérprete, la importación de `torch` y la carga del modelo: unos segundos, frente a minutos de separación.
- El protocolo de líneas JSON es una interfaz interna que hay que especificar con tipos y probar (serialización de cada evento, línea malformada, trabajador que muere a mitad).
- La ruta de Windows (`CTRL_BREAK_EVENT`) hay que verificarla aparte. Entra en el protocolo de portabilidad (CA-20) cuando se implemente I8.
