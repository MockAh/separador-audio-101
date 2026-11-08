# Incremento I2 — Calidad de voz

Estado: Aprobado v0.2 (2026-11-07)
Depende de: `product.md` §3, `constitution.md` (P4, P6, P9, §4), `acceptance.md` §4, ADR-002 (v0.5) y T-15.

## 1. Objetivo

Elegir con datos propios, no con cifras publicadas, qué modelos ocupan el catálogo de voz e instrumental. Para eso se construye `scripts/evaluate.py` y se aplica el protocolo de `acceptance.md` §4. El resultado es un ADR nuevo (ADR-007) y, si los datos lo piden, un cambio de catálogo.

I2 no cambia la CLI ni `audio-separator` y no añade dependencias.

**Requisito previo:** T-15, pasos 1 y 2. **Cumplido el 2026-11-05**: el reloj está sano, también tras `wsl --shutdown`.

## 2. Candidatos

Datos del 2026-11-03. SDR de `models-scores.json` (`audio-separator` 0.47.0): no medido por nosotros y sin audio del usuario. La columna "Δ" es la mediana de la diferencia de SDR de voz frente a `UVR-MDX-NET-Voc_FT.onnx`, sobre las 11 pistas que todos los modelos de esta tabla tienen en común. Las medianas publicadas no se comparan directamente, porque unos modelos se puntuaron con 40 pistas y otros con 11-13.

| Rol | `engine_filename` | Familia | Δ voz | Descarga | Licencia de los pesos |
|-----|-------------------|---------|-------|----------|-----------------------|
| Referencia (`vocals`) | `UVR-MDX-NET-Voc_FT.onnx` | MDX-Net | 0 | 66,8 MB | `MIT (UVR), con atribución` |
| Referencia (`vocals-max`) | `model_bs_roformer_ep_317_sdr_12.9755.ckpt` | BS-Roformer | +1,48 | 639,3 MB | `Sin licencia declarada` |
| **Candidato** | `vocals_mel_band_roformer.ckpt` | Mel-Band Roformer (Kim) | +1,48 | 913,1 MB | MIT declarada en `KimberleyJSN/melbandroformer` (Hugging Face) |

**Por qué interesa el candidato:** en estas 11 pistas iguala a `vocals-max` en SDR y, a diferencia de él, tiene licencia declarada. El archivo que descarga `audio-separator` mide lo mismo que el publicado con licencia MIT (913 106 900 bytes). Coincidir en tamaño no prueba que sea el mismo archivo: la primera tarea de I2 comparará su SHA-256 con `87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e`, y si no coincide, la licencia pasa a "sin declarar".

**Descartados sin medir:**

| Modelo | Motivo |
|--------|--------|
| `UVR-MDX-NET_Main_406.onnx` | Mejor MDX (Δ +0,27, poca diferencia), pero UVR lo clasifica como VIP y se descarga de otro repositorio (`Anjok0109/ai_magic`). No se ha revisado su licencia. |
| `Kim_Vocal_2.onnx` | Δ −0,02 y el mismo tamaño que el actual: nada que ganar en SDR. Su coste en CPU no se ha medido. |
| `model_bs_roformer_ep_368_sdr_12.9628.ckpt` | Δ +1,55, solo 0,07 dB más que `vocals-max`, con su mismo tamaño. No se ha revisado su licencia; se supone el mismo caso que `vocals-max`, sin confirmar. |
| `MelBandRoformerBigSYHFTV1.ckpt` | MIT, pero Δ +1,06 con 1,48 GB: inferior al candidato en SDR y tamaño. |
| `melband_roformer_big_beta4.ckpt`, `mel_band_roformer_kim_ft_unwa.ckpt` | Δ +1,28 y +1,32, sin licencia declarada en sus repositorios de Hugging Face. |
| `htdemucs_ft.yaml` | Δ −0,62 en voz: no mejora al actual. Se evalúa en I3, donde importan sus otros stems. Sus pesos se descargan de `dl.fbaipublicfiles.com`, un host que I3 tendrá que añadir a la red permitida. |

## 3. Requisitos funcionales

**RF-14. Herramienta de evaluación.** `scripts/evaluate.py` separa cada fragmento de `eval/fragments/` con cada candidato. No se instala con el paquete.
- **Candidatos:** son una constante del script con objetos `ModelSpec`, sin tocar `domain/catalog.py`, porque un candidato no es un modelo del catálogo (`domain.md` §3). Los campos que aún no se conocen llevan valores que no se pueden confundir con una medición: `cpu_rtf` = 0.0, `download_size_bytes` = 0 y `description` = "candidato de I2".
- **Adaptadores:** usa los reales (`FFmpegAudioNormalizer`, `AudioSeparatorEngine`, `FFmpegStemExporter`), así que `audio-separator` y FFmpeg se siguen invocando solo desde donde manda `CLAUDE.md`. No pasa por `SeparationService`, porque el servicio resuelve el modelo por id en el catálogo. La ganancia común se calcula con `output_gain()` de `application/service.py`, sin copiar la fórmula.
- **Salida:** todo lo que escribe queda bajo `eval/`, que ya está en `.gitignore`.

**RF-15. Una ejecución por par.** Cada par fragmento y modelo se ejecuta en un proceso propio y se registra en `eval/out/results.json`:
- tiempo de carga y de inferencia, con las etapas LOADING_MODEL y SEPARATING;
- factor de tiempo real sobre el fragmento (carga + separación, el mismo método que ADR-002 v0.5), pico de RAM (`ru_maxrss`), `output_gain` e hilos;
- SNR de reconstrucción, con la definición de CA-25;
- `tick` del kernel antes y después del par (en Linux, con `adjtimex`; en otro sistema, `null`). Si no es 10000, el par se marca como "reloj no fiable" y sus tiempos no cuentan para §4.

**El orden de los modelos rota por fragmento:** en el fragmento *i*, la lista de modelos empieza por el modelo *i* mod *n*. Motivo: en T-15, el Roformer fue más lento por segundo de audio en una pasada larga (ADR-002 v0.5). Con un orden fijo, el último modelo de cada tanda saldría perjudicado.

Un par correcto no se repite, porque las ejecuciones duran horas y una descarga cortada no debe obligar a empezar de cero (se vio en T-14). Un par que falla se registra con su error, el resto continúa y se reintenta en la siguiente ejecución.

**RF-16. Escucha a ciegas.** Los stems se exportan como `eval/out/<fragmento>/<letra>_<stem>.wav`, con la ganancia común de RF-06 para que suenen al nivel que entregaría el producto. La letra se sortea por fragmento, y su clave solo está en `results.json`. El script genera `eval/out/scores.csv` con una fila por fragmento, letra y stem, y las columnas del checklist de `acceptance.md` §4. Coros solo se puntúa en `vocals`.

**RF-17. [RETIRADO] SDR con referencias.** Retirado en v0.2: el usuario no tiene pistas aisladas (§7, decisión 4).

**RF-18. Informe y regla de decisión.** `scripts/evaluate.py --report` une `results.json` y `scores.csv`, destapa las letras e imprime una tabla por modelo con la media por criterio y stem, tiempos, RAM y SNR, junto con el veredicto de §4. Si falta alguna puntuación, termina con error y lista las que faltan, para no decidir con datos parciales.

**RF-19. Decisión registrada.** La tabla y el veredicto van a ADR-007. Si cambia el catálogo:
- el `cpu_rtf` del modelo que entra se mide con una canción completa, por el método de ADR-002 v0.5, antes de escribirlo en el catálogo;
- se actualizan en la misma tarea `domain.md` §3, `domain/catalog.py` y sus tests, y los textos que citan los modelos.

Rellena además las "Notas de escucha" que ADR-002 dejó pendientes.

## 4. Regla de decisión

Se fija antes de ver los resultados. "Nota" es la media de todos los criterios y stems sobre todos los fragmentos; escala de 1 a 5.

**Tiempos.** El factor sobre un fragmento no es comparable con el `cpu_rtf` del catálogo, que se mide con una canción completa: en T-15, `vocals` dio 2,4× con 30 s y 1,5× con la canción, y `vocals-max`, 37× y 48× (ADR-002 v0.5). Por eso §4 compara los modelos entre sí, con los factores de los mismos fragmentos en la misma ejecución. "Cociente de tiempo" de A frente a B es la suma de (carga + separación) de A en todos los fragmentos dividida entre la de B. Solo cuentan los pares con reloj fiable (RF-15).

**Condición para cualquier modelo.** Ningún modelo entra en el catálogo con un SNR de reconstrucción menor de 30 dB en algún fragmento: incumpliría CA-25, que se exige a cada modelo del catálogo.

**Predeterminado.** Un modelo sustituye a `vocals` solo si cumple todo esto:
1. tiene licencia declarada;
2. su nota supera a la de `vocals` en al menos 0,5;
3. en ningún fragmento queda más de 0,5 por debajo de `vocals`;
4. su `cpu_rtf`, medido con una canción completa (RF-19), es ≤ 10× (el criterio de ADR-002). Esta medición solo se hace si cumple las tres condiciones anteriores.

**Modelo de máxima calidad.** El candidato sustituye a `vocals-max` si tiene licencia declarada, su nota no es inferior en más de 0,25 y su cociente de tiempo frente a `vocals-max` es ≤ 1,25. Se reutiliza el id `vocals-max`: es estable por `domain.md` §3, aunque cambie el archivo del motor.

**Si no se cumple:** el catálogo no cambia, y ADR-007 lo registra como resultado válido de I2.

## 5. Criterios de aceptación

Salvo CA-33, todos son tests U: sin red, sin modelos y con motor y FFmpeg simulados (RNF-07).

- **CA-27 (RF-14, RF-15).** Con 2 fragmentos y 2 candidatos falsos, `results.json` contiene los 4 pares con todos sus campos, y se lanza un proceso por par (ejecutor de procesos simulado). El orden de los modelos rota por fragmento. Un par con `tick` distinto de 10000 queda marcado como "reloj no fiable".
- **CA-28 (RF-15).** Al repetir la ejecución, los pares correctos no se vuelven a separar y los fallidos sí. Si un par falla, queda registrado su error y los demás se ejecutan.
- **CA-29 (RF-14, RF-16).** Ningún nombre de archivo ni fila de `scores.csv` contiene el id ni el archivo de un modelo, y todo lo escrito queda bajo `eval/`. El catálogo no cambia. Los stems exportados llevan la ganancia de `output_gain()`.
- **CA-30 [RETIRADO] (antiguo RF-17).** SDR sobre señales sintéticas. Retirado en v0.2 con RF-17.
- **CA-31 (RF-18).** Con datos fijados, el veredicto es correcto en cada borde de §4: SNR de 30 dB, licencia, +0,5, −0,5, 0,25, cociente de tiempo de 1,25 y `cpu_rtf` de 10× con la canción completa. Los pares con reloj no fiable no cuentan en el cociente. Con una puntuación ausente, hay error y se lista.
- **CA-32 (RF-16).** Coros queda vacío en las filas de `instrumental` y es obligatorio en las de `vocals`.
- **CA-33 (M; RF-14 a RF-19).** Ejecución real con 3 a 5 fragmentos del usuario y ADR-007 aceptado por el usuario.

## 6. Tiempo estimado

- **Máquina**, con 5 fragmentos de 30 s: unas 3,5–4 h, casi todo de los dos Roformer (unos 20 min por fragmento cada uno; `vocals`, poco más de 1 min). La primera vez hay que sumar la descarga del candidato (913 MB). Si un Roformer entra en el catálogo, medir su `cpu_rtf` con la canción completa son unas 3 h más (RF-19). Se lanza desatendida, con el equipo enchufado y sin suspenderse, como en T-15.
- **Escucha del usuario:** 5 fragmentos × 3 modelos × 2 stems = 30 clips de 30 s. Con las repeticiones y la puntuación, unos 45–60 min. Se reparte en bloques de 15 min como mucho, de un fragmento cada uno (6 clips).

## 7. Decisiones tomadas

Por el usuario, el 2026-11-07:
1. **Ensemble:** fuera de I2. Exige cambiar el contrato del motor, que hoy ejecuta un modelo, y sumaría el coste de ambos modelos. Solo se reabre si ADR-007 deja un motivo.
2. **Escucha a ciegas:** sí, con las letras sorteadas por fragmento. No añade tiempo y evita favorecer al modelo "caro".
3. **Umbrales de §4:** 0,5 como mejora audible y 0,25 como empate. Sin datos previos no hay forma mejor de calibrarlos, y quedan fijados antes de ver los resultados.
4. **SDR con referencias:** fuera de I2 (P9). El usuario no tiene pistas aisladas de sus canciones ni puede conseguirlas fácilmente. Los conjuntos de investigación (MUSDB18) no sirven: solo se permiten para investigación, y es probable que los modelos se entrenaran con ellos. RF-17 y CA-30 quedan marcados como `[RETIRADO]` (`constitution.md` §2: los números no se reutilizan).
5. **Tiempos frente al catálogo** (opción a): los modelos se comparan por cociente de tiempo sobre los mismos fragmentos, y el `cpu_rtf` con la canción completa solo se mide para el modelo que entra. Descartada: evaluar con canciones completas (unas 3 h por canción y Roformer, 20–30 h en total).

## Registro de cambios
- v0.1 (2026-11-03) — Borrador.
- v0.2 (2026-11-07) — Revisión tras T-15:
  - §1: requisito previo cumplido.
  - §3, RF-14: valores provisionales de los candidatos; `output_gain()` reutilizada; sin `SeparationService`.
  - §3, RF-15: `tick` por par y orden rotado por fragmento.
  - §3: RF-17 (SDR con referencias) `[RETIRADO]`. RF-19 mide el `cpu_rtf` con canción completa del modelo que entra.
  - §4: tiempos por cociente sobre los mismos fragmentos; el 10× del predeterminado, con canción completa.
  - §5: CA-30 `[RETIRADO]`; bordes nuevos en CA-31.
  - Aprobado por el usuario (2026-11-07).
  - §6: tiempo estimado.
  - §7: decisiones de §6 tomadas por el usuario.
