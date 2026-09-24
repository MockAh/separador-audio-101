# ADR-004 — Escritura atómica de salidas

Estado: Propuesto · Fecha: 2026-09-24

## Contexto
Una separación en CPU puede durar minutos y fallar a mitad (falta de memoria, interrupción, error del motor). El usuario no debe encontrarse stems parciales o corruptos en su carpeta de salida (principio P8).

## Decisión
- Todo el trabajo intermedio (audio extraído, salidas del motor, stems exportados) se escribe en un `tempfile.TemporaryDirectory` propio del trabajo.
- Solo cuando **todos** los stems pedidos se han exportado y verificado con ffprobe, se trasladan al directorio de salida.
- El traslado usa `os.replace` cuando temporal y salida están en el mismo volumen; si no, copia a un nombre temporal oculto en el directorio de salida y después `os.replace`. Si el traslado de un stem falla, se eliminan los ya trasladados en esta ejecución.
- La comprobación de conflictos (`--overwrite`) se hace antes de separar, no al final.
- `Ctrl+C` se trata como fallo: se limpia y se sale con código 130.

## Consecuencias
- El directorio de salida queda siempre en un estado coherente.
- Se necesita espacio temporal adicional (contemplado en la estimación de `domain.md` §5).
