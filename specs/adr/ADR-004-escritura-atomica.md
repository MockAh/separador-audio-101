# ADR-004 — Escritura atómica de salidas

Estado: Aceptado (v0.3) · Fecha: 2026-09-24

Modificado: 2026-10-04 (T-10) — fallos de restauración separados en dos casos y precedencia de códigos de salida reunida en un solo punto.

## Contexto
Una separación en CPU puede durar minutos y fallar a mitad (falta de memoria, interrupción, error del motor). El usuario no debe encontrarse stems parciales o corruptos en su carpeta de salida (principio P8).

## Decisión
- Todo el trabajo intermedio (audio de trabajo, salidas del motor, stems exportados) se escribe en un `tempfile.TemporaryDirectory` propio del trabajo.
- Solo cuando **todos** los stems pedidos se han exportado y verificado con ffprobe (incluida la tolerancia de duración), se trasladan al directorio de salida.
- Todo archivo auxiliar que se crea en el directorio de salida lleva el prefijo `.stemseparator-tmp-`, para que sea reconocible y se pueda borrar a mano.
- `<job>` es un identificador único por ejecución: un token aleatorio corto (p. ej., 8 caracteres hexadecimales de `secrets.token_hex(4)`), nunca el PID, que se reutiliza. Así, dos ejecuciones simultáneas sobre el mismo directorio de salida no se pisan las copias de seguridad.
- **Traslado de cada stem**:
  1. Si el destino ya existe (solo posible con `--overwrite`), se renombra con `os.replace` a `.stemseparator-tmp-<job>-<nombre>.bak` en el mismo directorio. Es un renombrado dentro del mismo volumen: atómico y sin coste de espacio.
  2. Si temporal y salida están en el mismo volumen, `os.replace` al destino. Si no, se copia a `.stemseparator-tmp-<job>-<nombre>` en el directorio de salida y después `os.replace` al destino.
- **Éxito**: cuando todos los stems están publicados, se borran las copias `.bak`.
- **Fallo durante el traslado** (incluido Ctrl+C), para cada stem tocado en esta ejecución:
  - Si tiene copia `.bak`, se restaura con `os.replace(.bak, destino)` directamente encima del publicado (o en su lugar, si aún no se había publicado). Es atómico y no hay ningún momento en que el archivo del usuario no exista.
  - Si no tiene `.bak`, el stem publicado se borra (si el borrado falla, ver "Fallo de la restauración").
  - Las copias temporales `.stemseparator-tmp-<job>-*` que queden se borran.
  - El directorio de salida queda como estaba antes (P8).
- **Fallo de la restauración**: hay dos casos distintos, según lo que falle.
  - **Falla `os.replace(.bak, destino)`**: no se ignora. Se lanza `OutputRestoreError` y la CLI termina con **código 6**. El mensaje indica, archivo por archivo, qué `.bak` hay que renombrar y a qué nombre. Es el único caso del diseño en que el usuario puede perder un archivo propio. Por eso tiene un código propio: el 5 significa "tu carpeta está intacta" y el 6 significa "hay que intervenir a mano". Un script no debe reintentar ante un 6.
  - **Falla el borrado de un stem publicado que no tenía `.bak`**: mejor esfuerzo. Se registra en el log y el código de salida no cambia. Si lo que se propaga es un `StemSeparatorError`, su mensaje menciona el archivo que quedó; si es un `KeyboardInterrupt` (interrupción durante una publicación que falló), solo se registra en el log: el usuario cortó a propósito, y el 130 lo decide la CLI, no una excepción del dominio (`interfaces.md` §4). Al usuario le sobra un archivo nuevo, no le falta uno suyo, así que no hay nada que intervenir a mano y no corresponde el 6.
- **Precedencia de códigos de salida** cuando concurren varios: **6 > 130 > 5**. Un fallo de `os.replace(.bak, destino)` da 6 aunque el trabajo se hubiera interrumpido con Ctrl+C o hubiera fallado con un 5; una interrupción da 130 aunque además haya fallado la publicación (5).
- **Sección crítica**: la publicación (traslados, borrado de `.bak`) y la restauración son una sección crítica. Mientras dura, `SIGINT` se difiere: se registra y se atiende al salir de la sección.
  - Si la sección era una publicación que terminó bien, el trabajo se da por completado: código 0, con un aviso explícito de que la interrupción llegó durante la publicación y el trabajo se completó. No se deshace una publicación completa.
  - Si la publicación falló o se estaba restaurando, la restauración se completa y se sale con 130 (o 6 si falló un `os.replace(.bak, destino)`; ver la precedencia).
- La comprobación de conflictos (`--overwrite`) se hace antes de separar, no al final.
- `Ctrl+C` fuera de la sección crítica se trata como fallo: se limpia, se restauran los `.bak` si los hay y se sale con código 130 (CA-19).

## Consecuencias
- El directorio de salida queda siempre en un estado coherente.
- Se necesita espacio temporal adicional (contemplado en la estimación de `domain.md` §5). Las copias `.bak` no añaden espacio porque son renombrados.
- Un segundo Ctrl+C durante la sección crítica tampoco la interrumpe. La sección es corta (renombrados y, como mucho, una copia por stem entre volúmenes), así que la espera es breve.

## Limitación aceptada
Un corte de energía o un `kill -9` durante el traslado puede dejar archivos `.stemseparator-tmp-*` huérfanos en el directorio de salida. Si el huérfano es un `.bak`, contiene el archivo original del usuario, que se recupera renombrándolo. En Windows el prefijo con punto no oculta el archivo, pero sigue siendo reconocible. En I1 no hay limpieza automática; el mensaje de `--help` de `--overwrite` y el README documentan el prefijo.

## Registro de cambios
- v0.2 (2026-09-24) — Copias de seguridad `.bak` con `--overwrite`; restauración con `os.replace` directo sobre el publicado; `OutputRestoreError` (código 6); sección crítica con `SIGINT` diferido; `<job>` como token aleatorio; prefijo `.stemseparator-tmp-`; limitación aceptada de huérfanos por `kill -9`; verificación de duración antes de publicar.
- 2026-09-24 — Aceptado por el usuario.
- v0.3 (2026-10-04) — T-10: "Fallo de la restauración" separado en dos casos: si falla `os.replace(.bak, destino)`, `OutputRestoreError` (código 6); si falla el borrado de un stem publicado sin `.bak`, mejor esfuerzo (se registra y el código no cambia; el archivo que quedó se menciona en el mensaje del error original si es un `StemSeparatorError`, y si se propaga un `KeyboardInterrupt` solo se registra en el log). Precedencia de códigos reunida en un punto: 6 > 130 > 5. Cierra el cabo de la nota de cierre de T-09. Aprobado por el usuario; el ADR sigue Aceptado.
