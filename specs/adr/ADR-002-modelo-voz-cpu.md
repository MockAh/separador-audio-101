# ADR-002 — Modelos de voz/instrumental para CPU y modelo predeterminado

Estado: Aceptado (2026-09-25, con los datos de T-01; v0.5) · Fecha: 2026-09-24

Modificado: 2026-10-09 (T-11) — pico de RAM con una canción completa; el factor de tiempo real medido en T-11 no es utilizable por el reloj de este entorno (T-15).

Modificado: 2026-11-07 (T-15) — `cpu_rtf` medido con una canción completa y el reloj comprobado; cambia el método y las dos cifras.

## Contexto
El usuario ejecutará StemSeparator solo en CPU y prioriza la calidad de la separación de voz. Los modelos Roformer ofrecen la mejor calidad en voz/instrumental, pero son los más costosos en CPU; los MDX-Net en ONNX son bastante más rápidos con calidad algo menor.

## Propuesta inicial (sustituida por la Decisión)
- Catálogo con dos entradas: un Roformer de voz y un MDX-Net de voz.
- Predeterminado: el Roformer, porque el objetivo del producto es la calidad. La elección definitiva se tomaría con los datos medidos.

## Decisión
- **Catálogo**: `vocals` = `UVR-MDX-NET-Voc_FT.onnx` (**predeterminado**) y `vocals-max` = `model_bs_roformer_ep_317_sdr_12.9755.ckpt`. En T-01 se midieron como `vocals-fast` y `vocals-hq`; se renombran porque, con el MDX-Net como predeterminado, los nombres anteriores engañaban.
- **Motivo**: `vocals-max` tarda 48× el tiempo real en el equipo de referencia (unas 2 h 47 min para una canción de 3:30) a cambio de 1,6 dB de SDR mediano en voz. No es lento, es inutilizable como predeterminado. Se queda en el catálogo para quien acepte ese coste.
- **Coste en CPU**: el catálogo registra el factor de tiempo real medido y el equipo de referencia en lugar de una etiqueta cualitativa (`ModelSpec.cpu_rtf`, `ModelSpec.cpu_reference`). `models` lo muestra traducido a tiempo, por ejemplo "≈1,5× (unos 5 min para una canción de 3:30)". Desde v0.5, `cpu_rtf` es (carga del modelo + separación) / duración del audio, medido con la CLI sobre una canción completa (ver "Medición de T-15").
- **Licencia de `vocals-max`**: se queda en el catálogo, registrada como "Sin licencia declarada", y `models` lo muestra. StemSeparator no redistribuye los pesos: los descarga el usuario desde el repositorio de UVR. La cuestión afecta al uso que haga el usuario, no a nuestra distribución. RNF-10 se cumple registrando y mostrando el estado real (`product.md` §7 lo advierte para uso comercial).
- **Margen para la reconstrucción** (opción b): el adaptador atenúa la entrada del motor −6 dB y deshace esa atenuación sobre los stems en coma flotante. Si algún stem sale del motor con un pico ≥ 0,9999, lanza `EngineError`, porque eso indica que el motor lo reescaló. Al exportar, el servicio aplica a todos los stems una ganancia común `output_gain` ≤ 1 para que ninguno sature, la registra en `SeparationResult` y la CLI la muestra.

## Datos de T-01

Versión del motor: `audio-separator==0.47.0` (Python 3.12, entorno solo CPU; ver Anexo A).

| id | engine_filename | Tiempo CPU por minuto de audio | Licencia de pesos | Notas de escucha |
|----|-----------------|-------------------------------|-------------------|------------------|
| vocals-max (medido como vocals-hq) | `model_bs_roformer_ep_317_sdr_12.9755.ckpt` (BS-Roformer ViperX 1297; el modelo predeterminado del propio `audio-separator`) | **37,1 min** (37,1× el tiempo real) | Sin licencia declarada (ver abajo) | _pendiente: escucha del usuario_ |
| vocals (medido como vocals-fast; predeterminado) | `UVR-MDX-NET-Voc_FT.onnx` (MDX-Net, ONNX) | **2,4 min** (2,4× el tiempo real) | MIT (UVR), con atribución | _pendiente: escucha del usuario_ |

2026-11-07 (T-15): estas cifras son del fragmento de 30 s. El catálogo usa desde v0.5 las de una canción completa: 1,5× y 47,7× (ver "Medición de T-15").

Mediciones sobre el fragmento de 30 s (una ejecución por modelo, cada una en un proceso aparte):

| Medida | vocals-max | vocals |
|--------|-----------|-------------|
| Descarga de pesos, primera vez (depende de la red) | 33,0 s | 11,6 s |
| Tamaño en disco | 639,3 MB (`.ckpt`) + `.yaml` de 2 KB | 66,8 MB (`.onnx`) + `mdx_model_data.json` y `vr_model_data.json` |
| Carga del modelo (`load_model()`, con los pesos ya en disco) | 4,3 s | 8,9 s |
| Inferencia (`separate()`) | 1 109,7 s (18 min 30 s) | 63,9 s (56,7 s en una segunda ejecución) |
| Factor de tiempo real, solo inferencia | 37,0× | 2,1× |
| Factor de tiempo real, carga + inferencia | 37,1× | 2,4× |
| Pico de RAM (RSS máximo del proceso, incluye importar `torch`) | 2 356 MB | 3 196 MB |
| Dispositivo elegido por el motor | `cpu` / `CPUExecutionProvider` | `cpu` / `CPUExecutionProvider` |
| Hilos de `torch` | 4 (de 8 lógicos) | 4 (de 8 lógicos) |
| Etiquetas del motor | `Vocals`, `Instrumental` | `Vocals`, `Instrumental` |
| Salidas con `custom_output_names` | `vocals.wav`, `instrumental.wav` (WAV `FLOAT`, misma longitud que la entrada) | igual |

Nota sobre el pico de RAM: el proceso de `vocals` alcanzó 3 196 MB, más que el Roformer, a pesar de que el modelo pesa 10 veces menos; lo atribuimos a los búferes de ONNX Runtime, pero no lo hemos investigado.

SDR mediano de referencia, tomado de `models-scores.json` incluido en el paquete (40 pistas, no medido por nosotros): `vocals-max` voz 11,77 dB / instrumental 16,45 dB; `vocals` voz 10,15 dB / instrumental 15,43 dB.

Stems para escuchar: `~/audio-pruebas/salida-t01/<id>/{vocals,instrumental}.wav` (fuera del repositorio).

### Reconstrucción (voz + instrumental frente al audio de trabajo)

Configuración aprobada: `use_soundfile=True`, `normalization_threshold=1.0` y audio de trabajo `pcm_f32le`.

| | vocals-max | vocals |
|--|-----------|-------------|
| SNR de reconstrucción con esa configuración | 16,7 dB (error de pico 0,22) | 19,4 dB (error de pico 0,15) |
| Pico del instrumental | exactamente 1,0 (reescalado) | exactamente 1,0 (reescalado) |
| Pico real del instrumental antes del reescalado | 1,22 | 1,15 |
| SNR tras deshacer el reescalado | 150,6 dB (el instrumental es exactamente la mezcla menos la voz) | 39,1 dB |
| Con −6 dB de margen en la entrada (solo medido en `vocals`) | — | 45,1 dB; picos 0,22 / 0,52 |

Conclusión: `normalization_threshold=1.0` **no basta**. El instrumental real supera 1,0 aunque la mezcla no llegue (pico de la mezcla: 0,98), y `audio-separator` reescala por separado cualquier stem que pase del umbral. El umbral no admite valores mayores que 1 (`ValueError` en el constructor). El margen se decide en "Decisión" (opción b).

Equipo de medición: Intel Core i7-8565U, 4 núcleos / 8 hilos, 7,8 GB de RAM asignados a WSL2, Ubuntu sobre Windows (kernel 6.6.87.2-microsoft-standard-WSL2), Python 3.12.3, FFmpeg 6.1.1.

2026-09-30 (T-04): este es el registro completo del equipo de medición. El catálogo (`cpu_reference`) usa la forma corta, idéntica en ambos modelos, que está en `domain.md` §3: `Intel Core i7-8565U, 4 núcleos / 8 hilos, WSL2`. No se copia esta línea larga al catálogo.

Fragmento de prueba: 30 s desde un tercio de la duración (69,581 s de 208,74 s) de un MP3 de 44,1 kHz estéreo del usuario (no versionado), normalizado a WAV `pcm_f32le`, 44 100 Hz, 2 canales (la configuración aprobada tras T-01). Solo se mide la separación, no la decodificación. El pico de la mezcla es 0,98.

## Verificación de T-11 con una canción completa (2026-10-09)

Entrada: la canción completa de la que salió el fragmento de T-01 (MP3 de 44,1 kHz estéreo, 208,7 s). Modelo `vocals`, a través de `SeparationService` con los mismos adaptadores que montará la CLI. Se mide con `/usr/bin/time -v` sobre `.venv/bin/python`, es decir, el proceso que separa, como en T-01. Mismo equipo de medición que T-01.

| Medida | T-01 (fragmento de 30 s) | T-11 (canción completa, 208,7 s) |
|--------|--------------------------|----------------------------------|
| Pico de RAM (RSS máximo del proceso) | 3 196 MiB | 3 745 592 KiB = 3 658 MiB (≈3,7 GiB) |

- La tabla de T-01 dice "MB", pero son MiB: el spike divide `ru_maxrss` (KiB en Linux) entre 1024. `/usr/bin/time` también da KiB. Comparadas en la misma unidad, la diferencia es de +14 %.
- La primera ejecución de T-11, que además descargó los pesos, dio 3 744 308 KiB: prácticamente lo mismo.
- Con un clip de 10 s, el mismo script dio 3 270 044 KiB (2026-10-06, durante T-11).
- **Conclusión:** la RAM no escala con la duración. Con siete veces más audio que T-01, el pico sube un 14 %. La base la marcan las importaciones (`torch`, `onnxruntime`) y los búferes, no el audio.
- **En la práctica:** hacen falta unos **4 GB de RAM libres** para separar con `vocals`.

**Factor de tiempo real: la medición de T-11 no es utilizable, y no se anota ninguna cifra.**
- En este entorno (WSL2), el reloj `CLOCK_MONOTONIC`, que usan `time.monotonic()` y `time.perf_counter()`, se adelanta hasta un 9 %. Además, la desviación no es constante.
- Medido el 2026-10-09 contra el reloj de Windows, con un script de comprobación que se guarda con T-15:
  - `monotonic`: 150,7 s;
  - Windows: 138,6 s, en el mismo intervalo.
- `tick` del kernel: oscila entre 10780 y 11000, cuando el valor nominal es 10000.
- Las cifras de T-01 (el 2,4× y el 37,1× de este ADR y de `domain.md` §3) se midieron con `perf_counter` y también están afectadas. Su corrección es la tarea T-15.
- 2026-11-07 (T-15): con el reloj ya comprobado, el spike de T-01 da las mismas cifras dentro del ruido, así que no se confirma que estuvieran afectadas. El factor de tiempo real de la canción completa está en "Medición de T-15".

## Medición de T-15 con el reloj comprobado (2026-11-05 y 2026-11-07)

Reloj: `systemd-timesyncd` desactivado. `scripts/clockcheck.py` da menos de un 0,5 % de diferencia con Windows en 150 s, también tras `wsl --shutdown` (T-15, pasos 1 y 2). Durante todas las mediciones se leyó `tick` del kernel cada minuto, y estuvo en 10000 en todas las lecturas. Mismo equipo de medición que T-01.

**Fragmento de 30 s: repetición de T-01.** Entorno del Anexo A con las mismas versiones resueltas, script del Anexo B sin cambios, el mismo fragmento y `ORT_DISABLE_TELEMETRY=1` (RNF-12; no cambia los tiempos).

| Medida | vocals-max | vocals |
|--------|-----------|--------|
| Inferencia (`separate()`) | 1 110,02 s (T-01: 1 109,72 s) | 63,88 / 68,15 / 66,47 s en tres ejecuciones (T-01: 63,89 s) |
| Factor de tiempo real, carga + inferencia | 37,21× (T-01: 37,13×) | 2,46 / 2,33 / 2,30× (T-01: 2,43×) |
| Pico de RAM | 2 367 MiB | 3 194 MiB |
| SNR de reconstrucción | 16,66 dB | 19,36 dB |

- Las cifras de T-01 se reproducen dentro del ruido entre ejecuciones (en torno a ±3,5 % en la inferencia de `vocals`). No se confirma que el reloj las inflara, como suponía la sección de T-11. La contradicción aritmética que lo sugería (`tasks.md`, T-15) queda sin explicar.
- En `vocals`, la carga baja de 9,8 s a unos 2 s con los pesos en la caché de disco, por eso la primera ejecución sale más alta.

**Canción completa (208,70 s)**, la de T-11, con `stemseparator separate` (tiempos de las etapas que muestra la CLI) y `/usr/bin/time -v`:

| Medida | vocals | vocals-max |
|--------|--------|-----------|
| Carga del modelo | 9,1 s | 14,5 s |
| Separación | 304,0 s | 9 935,2 s (2 h 45 min) |
| Exportación | 32,1 s | 35,2 s |
| Tiempo total de la CLI | 347,4 s | 9 988,1 s |
| **Factor de tiempo real, carga + separación (`cpu_rtf`)** | **1,50×** | **47,7×** |
| Pico de RAM (`/usr/bin/time`) | 3 774 564 KiB = 3 686 MiB | 2 654 220 KiB = 2 592 MiB |
| `output_gain` | 0,93 (−0,6 dB) | 0,76 (−2,4 dB) |

- **El factor depende de la duración, y no igual en los dos modelos.**
  - `vocals`: 2,4× con 30 s y 1,5× con la canción completa. Cada pasada tiene una parte fija, que pesa más en un clip corto. Con el fragmento, una canción de 3:30 salía en unos 8 min; en realidad son unos 5 (un 38 % menos).
  - `vocals-max`: 37× con 30 s y 48× con la canción completa. Con el fragmento, el tiempo prometido era un 22 % menor que el real, es decir, en la dirección peligrosa. No se ha investigado por qué el Roformer va más lento por segundo de audio en una pasada larga. Una hipótesis, sin comprobar, es que la CPU baja de frecuencia tras horas al 100 %.
- **Se reproduce:** en los tests `slow` con esta misma canción (`STEMSEPARATOR_SAMPLE_AUDIO`), la separación con `vocals-max` tardó 9 842,9 s (el test entero, con normalización y exportación), es decir, unos 47×. Pasan los 6 tests. La reconstrucción de CA-25 da 44,3 dB con `output_gain` 0,9299 (`vocals`) y 125,2 dB con 0,7585 (`vocals-max`), por encima del mínimo de 30 dB.
- **Método de `cpu_rtf` desde v0.5:** (carga del modelo + separación) / duración del audio, con la CLI y una canción completa. Sustituye al fragmento de 30 s de T-01, porque la cifra que importa al usuario es la de una canción entera. Queda fuera la descarga, que depende de la red, y también la preparación y la exportación, que dependen del formato de entrada y de salida.
- La ejecución de T-11 con `vocals` (6:44,49 de reloj y 1 306 s de CPU) tardó un 16 % más que esta (5:48,89 y 1 125,7 s). No se sabe si incluía la descarga ni con qué script exacto: se pasó por stdin y no se guardó.

## Licencias de los pesos (RNF-10), revisadas el 2026-09-25

Ambos pesos se descargan de la release `all_public_uvr_models` del repositorio `TRvlvr/model_repo` en GitHub, que **no declara ninguna licencia** (ni archivo `LICENSE` ni licencia en la página del repositorio).

- `vocals` (`UVR-MDX-NET-Voc_FT.onnx`): modelo entrenado por el proyecto UVR (Ultimate Vocal Remover, `Anjok07/ultimatevocalremovergui`), cuyo repositorio declara **MIT**. El README de `audio-separator` (MIT) indica que los modelos entrenados por UVR se usan bajo esa licencia, dando crédito a UVR y sus desarrolladores. Registro: `MIT (UVR), con atribución`.
- `vocals-max` (`model_bs_roformer_ep_317_sdr_12.9755.ckpt`): BS-Roformer entrenado por *viperx*, **no** por UVR; UVR solo lo redistribuye. No se ha encontrado ninguna licencia declarada: ni en `TRvlvr/model_repo`, ni en `audio-separator`, ni en una ficha de modelo en Hugging Face. Registro: `Sin licencia declarada`. Sin licencia explícita, el uso no está concedido formalmente; se muestra en `models` y se advierte en `product.md` §7 (ver Decisión).

2026-09-30 (T-04): las cadenas literales de licencia que muestra `models` son las de `domain.md` §3.

## Criterio de decisión
Si el Roformer (`vocals-max`, entonces `vocals-hq`) tarda más de ~10× la duración del audio en el equipo del usuario, se reconsidera el predeterminado con el usuario antes de aceptar este ADR.

**Resultado: se cumple.** El Roformer tarda 37,1× con el fragmento de T-01 y 47,7× con una canción completa (T-15): una canción de 3:30 son unas 2 h 47 min de CPU en este equipo. El MDX-Net tarda 1,5× con una canción completa, unos 5 min. El usuario reconsideró el predeterminado; ver Decisión. Con las cifras de T-15, la conclusión no cambia: el Roformer sigue muy por encima de ~10×.

## Consecuencias
- El usuario sabrá de antemano cuánto tarda cada modelo: `models` muestra el factor de tiempo real medido traducido a minutos.
- Los tiempos son del equipo de referencia; en otro equipo pueden variar, y `models` lo indica.
- El pico de RAM se midió sobre 30 s, y T-11 lo verificó con una canción completa y el modelo predeterminado: 3 658 MiB, un 14 % más con siete veces más audio. Hacen falta unos 4 GB de RAM libres (ver "Verificación de T-11").
- Los stems exportados reconstruyen el audio de trabajo multiplicado por `output_gain`, que se registra y se muestra, así que el escalado se puede deshacer.
- I2 puede sustituir el predeterminado con evaluación completa (§4 de acceptance.md).

## Registro de cambios
Hasta v0.3, las versiones están reconstruidas a partir del historial de git: el ADR no llevaba numeración.
- v0.1 (2026-09-24) — Borrador: propuesta inicial con el Roformer como predeterminado y el criterio de los ~10×.
- v0.2 (2026-09-25) — T-01: datos medidos, catálogo `vocals` (predeterminado) / `vocals-max`, licencias de los pesos y margen de −6 dB. Aceptado.
- v0.3 (2026-09-30) — T-04: licencia de `vocals` como `MIT (UVR), con atribución`; nota sobre las cadenas literales de `domain.md` §3 y la forma corta de `cpu_reference`.
- v0.4 (2026-10-09) — T-11: sección "Verificación de T-11" con el pico de RAM de una canción completa (3 658 MiB frente a 3 196 MiB con 30 s) y la necesidad de unos 4 GB libres. El factor de tiempo real de T-11 no se anota, porque `CLOCK_MONOTONIC` se adelanta hasta un 9 % en este entorno; la cifra queda pendiente de T-15, que también revisa las de T-01. Aprobado por el usuario; el ADR sigue Aceptado.
- v0.5 (2026-11-07) — T-15: sección "Medición de T-15" con el reloj comprobado. El spike de T-01 se reproduce dentro del ruido. Con una canción completa, `vocals` da 1,50× y `vocals-max` 47,7×. `cpu_rtf` pasa a medirse con una canción completa (carga + separación con la CLI), y la Decisión, el Criterio de decisión y el ejemplo de `models` usan esas cifras (1,5×, unos 5 min; 48×, unas 2 h 47 min). Motivo: con el fragmento, `vocals` prometía un 60 % más de lo real y `vocals-max` un 22 % menos. Aprobado por el usuario; el ADR sigue Aceptado.

## Anexo A — Entorno del spike (fuera del repositorio)

```bash
SPIKE=$(mktemp -d "$TMPDIR/t01-spike.XXXXXX"); cd "$SPIKE"
export UV_CACHE_DIR=$PWD/uv-cache
uv venv --python 3.12 .venv && export VIRTUAL_ENV=$PWD/.venv
# torch desde el índice CPU de PyTorch: desde PyPI, en Linux, se instala la build CUDA
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
uv pip install "audio-separator[cpu]==0.47.0"
# audio-separator 0.47.0 importa audioread sin declararlo; librosa 1.0 dejó de traerlo
uv pip install "librosa<1"
# red permitida: github.com, raw.githubusercontent.com, objects.githubusercontent.com,
# release-assets.githubusercontent.com (a donde redirigen las descargas de releases), huggingface.co

SRC="$HOME/audio-pruebas/<archivo>.mp3"
ffmpeg -hide_banner -loglevel error -ss 69.581 -t 30 -i "$SRC" \
  -map 0:a:0 -vn -c:a pcm_f32le -ar 44100 -ac 2 clip30.wav

.venv/bin/python spike_t01.py clip30.wav models ~/audio-pruebas/salida-t01
```

Comprobación de telemetría: en un sandbox que bloquea la red salvo los hosts de arriba, se construye `Separator`, se carga `UVR-MDX-NET-Voc_FT.onnx` y se llama a `separate()`. Se observaron conexiones a `mobile.events.data.microsoft.com:443` durante `separate()` con `onnxruntime.disable_telemetry_events()` llamado antes de importar `audio_separator` (reproducido 2 veces). Con la variable de entorno `ORT_DISABLE_TELEMETRY=1` no hubo ninguna (2 ejecuciones). Importar `onnxruntime`, crear una sesión o solo cargar el modelo no produjo conexiones.

Versiones resueltas: `audio-separator 0.47.0`, `torch 2.14.0+cpu`, `torchvision 0.29.0+cpu`, `onnxruntime 1.30.0`, `librosa 0.11.0`, `audioread 3.1.0`, `numpy 2.5.2`, `onnx-weekly 1.24.0.dev20260921`.

## Anexo B — Script del spike (`spike_t01.py`)

Se conserva tal como se ejecutó, con los ids provisionales `vocals-hq`/`vocals-fast`.

```python
"""T-01 spike: measure audio-separator vocal models on CPU.

Usage (from the spike dir, with the CPU-only venv):
    .venv/bin/python spike_t01.py clip30.wav models ~/audio-pruebas/salida-t01

Each model runs in its own child process so that peak RSS (ru_maxrss) is
per model. Download, load and inference are timed separately.
"""

from __future__ import annotations

import json
import logging
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

CANDIDATES = {
    "vocals-hq": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
    "vocals-fast": "UVR-MDX-NET-Voc_FT.onnx",
}


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def run_one(model_id: str, clip: Path, model_dir: Path, out_root: Path) -> dict[str, object]:
    import numpy as np
    import onnxruntime
    import soundfile as sf

    onnxruntime.disable_telemetry_events()
    from audio_separator.separator import Separator  # lazy, as in the product

    filename = CANDIDATES[model_id]
    out_dir = out_root / model_id
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_s = sf.info(str(clip)).duration

    sep = Separator(
        log_level=logging.WARNING,
        model_file_dir=str(model_dir),
        output_dir=str(out_dir),
        output_format="WAV",
        sample_rate=44100,
        use_soundfile=True,
        normalization_threshold=1.0,
    )
    device = {"torch": str(sep.torch_device), "onnx": list(sep.onnx_execution_provider or [])}

    size_before = dir_size(model_dir)
    t0 = time.perf_counter()
    sep.download_model_files(filename)
    t_download = time.perf_counter() - t0
    size_model = dir_size(model_dir) - size_before

    t0 = time.perf_counter()
    sep.load_model(model_filename=filename)
    t_load = time.perf_counter() - t0

    labels = [sep.model_instance.primary_stem_name, sep.model_instance.secondary_stem_name]

    custom_names = {label: label.lower() for label in labels}
    t0 = time.perf_counter()
    written = sep.separate(str(clip), custom_output_names=custom_names)
    t_infer = time.perf_counter() - t0

    # Reconstruction: sum of the two stems vs. the working audio.
    mix, _ = sf.read(str(clip), dtype="float64")
    stems = [sf.read(str(out_dir / Path(w).name), dtype="float64")[0] for w in written]
    n = min(len(mix), *(len(x) for x in stems))
    residual = mix[:n] - sum(x[:n] for x in stems)
    rms = lambda x: float(np.sqrt(np.mean(np.square(x))))
    recon = {
        "max_abs_error": round(float(np.max(np.abs(residual))), 6),
        "rms_error": round(rms(residual), 6),
        "snr_db": round(20 * np.log10(rms(mix[:n]) / max(rms(residual), 1e-12)), 2),
        "stem_formats": [str(sf.info(str(out_dir / Path(w).name)).subtype) for w in written],
        "length_diff_samples": [len(x) - len(mix) for x in stems],
    }

    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux
    return {
        "model_id": model_id,
        "engine_filename": filename,
        "device": device,
        "labels": labels,
        "output_files": written,
        "reconstruction": recon,
        "audio_s": round(audio_s, 3),
        "download_s": round(t_download, 2),
        "model_bytes": size_model,
        "load_s": round(t_load, 2),
        "infer_s": round(t_infer, 2),
        "rtf_infer": round(t_infer / audio_s, 3),
        "rtf_load_plus_infer": round((t_load + t_infer) / audio_s, 3),
        "cpu_min_per_audio_min": round((t_load + t_infer) / audio_s, 2),
        "peak_rss_mb": round(peak_rss_mb, 1),
        "threads": {"torch": __import__("torch").get_num_threads(), "os_cpu_count": os.cpu_count()},
    }


def main() -> None:
    if sys.argv[1] == "--child":
        _, _, model_id, clip, model_dir, out_root = sys.argv
        print(json.dumps(run_one(model_id, Path(clip), Path(model_dir), Path(out_root))))
        return

    clip, model_dir, out_root = (Path(a).expanduser().resolve() for a in sys.argv[1:4])
    model_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for model_id in CANDIDATES:
        proc = subprocess.run(
            [sys.executable, __file__, "--child", model_id, str(clip), str(model_dir), str(out_root)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(proc.stderr[-4000:], file=sys.stderr)
            raise SystemExit(f"{model_id} failed with code {proc.returncode}")
        results.append(json.loads(proc.stdout.strip().splitlines()[-1]))
        print(json.dumps(results[-1], indent=2))
    (out_root / "t01-results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
```
