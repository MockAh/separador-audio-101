# StemSeparator

StemSeparator es una herramienta de línea de comandos que separa un archivo de audio o de video
en pistas (*stems*): en esta versión, la **voz** (`vocals`) y el **instrumental** (`instrumental`).
Sirve, por ejemplo, para sacar el instrumental de una canción para practicar o hacer karaoke,
aislar la voz para transcribir la letra, o extraer la voz de un video sin convertirlo antes a mano.

- Funciona **en local**: el audio no sale de tu ordenador y no hay telemetría. Solo se conecta a
  internet para descargar los pesos de los modelos la primera vez que se usan.
- Funciona **solo en CPU**: no necesita tarjeta gráfica.
- Acepta `.wav`, `.mp3`, `.flac`, `.m4a`, `.mp4` y `.mkv`, y exporta en WAV (24 bits),
  FLAC (24 bits) o MP3 (320 kbps).

## Instalación

Hacen falta tres cosas: FFmpeg, `uv` y el propio StemSeparator.

### 1. FFmpeg

StemSeparator usa los programas `ffmpeg` y `ffprobe` del sistema para leer la entrada y escribir
los stems. Se instalan juntos:

| Sistema | Comando |
|---------|---------|
| Windows | `winget install Gyan.FFmpeg` |
| macOS | `brew install ffmpeg` (con [Homebrew](https://brew.sh)) |
| Linux (Debian/Ubuntu) | `sudo apt install ffmpeg` |

Después, abre una terminal nueva y comprueba con `ffmpeg -version`. Si falta FFmpeg,
StemSeparator lo dice y repite estas mismas instrucciones.

### 2. `uv`

StemSeparator se instala y se ejecuta con [`uv`](https://docs.astral.sh/uv/), que se encarga
también de descargar una versión de Python compatible (3.11 o 3.12) si no la tienes:

- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS y Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

### 3. StemSeparator

```bash
git clone https://github.com/MockAh/separador-audio-101.git
cd separador-audio-101
uv sync
uv run stemseparator doctor
```

`uv sync` instala las dependencias en un entorno propio del proyecto (unos 1,3 GB, sobre todo
PyTorch en su versión para CPU). `doctor` comprueba que todo está en su sitio: si termina con
el código 0, ya puedes separar.

En macOS solo funcionan los Mac con Apple Silicon y macOS 14 o posterior (ver
[Limitaciones conocidas](#limitaciones-conocidas)).

## Uso

Todos los comandos se ejecutan desde la carpeta del proyecto con `uv run stemseparator …`.
`uv run stemseparator --help` y `uv run stemseparator <comando> --help` muestran la ayuda.

### `separate`: separar un archivo

```bash
uv run stemseparator separate cancion.wav
```

Crea `cancion_vocals.wav` y `cancion_instrumental.wav` en la carpeta actual. Cada stem se llama
`<nombre de la entrada sin extensión>_<stem>.<formato>`. Así se ve una ejecución con un clip
de 10 s:

```
✓ Validación (0,1 s)
✓ Preparación (0,2 s)
✓ Carga del modelo (5,7 s)
✓ Separación (23,9 s)
✓ Exportación (2,3 s)
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┓
┃ Stem         ┃ Ruta                           ┃ Duración ┃ Tamaño ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━┩
│ vocals       │ stems/cancion_vocals.wav       │  10,00 s │ 2,6 MB │
│ instrumental │ stems/cancion_instrumental.wav │  10,00 s │ 2,6 MB │
└──────────────┴────────────────────────────────┴──────────┴────────┘
Tiempo total: 32,5 s
Ganancia de salida: 1,00 (0,0 dB)
La suma de los stems es la mezcla multiplicada por la ganancia de salida.
```

Más ejemplos:

```bash
# Desde un video, con los stems en ./stems, en FLAC y con el modelo de más calidad
uv run stemseparator separate concierto.mp4 -o stems -m vocals-max -f flac

# Solo la voz, en MP3
uv run stemseparator separate cancion.mp3 -s vocals -f mp3

# Repetir sobre una salida que ya existe, reemplazando los archivos
uv run stemseparator separate cancion.wav -o stems --overwrite
```

| Opción | Qué hace |
|--------|----------|
| `-o`, `--output DIR` | Carpeta de salida (por defecto, la actual). Se crea si no existe. |
| `-m`, `--model ID` | Modelo del catálogo (por defecto, `vocals`). La lista sale de `models`. |
| `-s`, `--stems LISTA` | Stems separados por comas, p. ej. `vocals,instrumental` (por defecto, todos). |
| `-f`, `--format wav\|flac\|mp3` | Formato de salida (por defecto, `wav`). |
| `--overwrite` | Reemplaza los archivos de salida que ya existan (ver más abajo). |
| `-v`, `--verbose` | Muestra los logs del motor y de FFmpeg, y el detalle técnico de los errores. |

Si algún archivo de salida ya existe y no usas `--overwrite`, StemSeparator no separa nada:
lista los archivos en conflicto y termina.

**Ganancia de salida.** Para que ningún stem sature, todos se exportan con una misma ganancia,
la mínima reducción necesaria (1,00 si no hace falta reducir). Por eso los stems pueden sonar
algo más bajos que la mezcla original. Como la ganancia es común, al sumar la voz y el
instrumental se recupera la mezcla multiplicada por esa ganancia, lo que hace los stems útiles
para remezclar.

**Ctrl+C** cancela la separación: StemSeparator borra lo que llevaba hecho, deja la carpeta de
salida como estaba y termina con el código 130.

### `models`: ver los modelos disponibles

```bash
uv run stemseparator models
```

```
vocals (por defecto)
Separador de voz MDX-Net del proyecto UVR. Buena calidad con un coste en CPU
moderado; opción práctica para uso habitual.
  Stems:        vocals, instrumental
  Coste en CPU: ≈2,4× (unos 8 min)
  Licencia:     MIT (UVR), con atribución
  Descargado:   sí

vocals-max
Separador de voz BS-Roformer (viperx). El SDR de referencia más alto del
catálogo, con un coste en CPU mucho mayor.
  Stems:        vocals, instrumental
  Coste en CPU: ≈37,1× (unas 2 h 10 min)
  Licencia:     Sin licencia declarada
  Descargado:   no

Coste medido en el equipo de referencia (Intel Core i7-8565U, 4 núcleos / 8
hilos, WSL2, ADR-002) para una canción de 3:30.
```

El coste en CPU es cuántas veces la duración del audio tarda la separación en el equipo de
referencia, y entre paréntesis lo que supone para una canción de 3:30. En otro ordenador
será distinto.

Los pesos de cada modelo se descargan la primera vez que se usa: unos 67 MB `vocals` y unos
640 MB `vocals-max`. Se guardan en la caché de modelos del usuario y no se vuelven a descargar.
Para guardarlos en otra carpeta, define la variable de entorno `STEMSEPARATOR_MODEL_DIR`.

### `doctor`: comprobar la instalación

```bash
uv run stemseparator doctor
```

```
Python:           3.12.3
StemSeparator:    0.1.0
audio-separator:  0.47.0
ffmpeg:           6.1.1-3ubuntu5
ffprobe:          6.1.1-3ubuntu5
Hilos de CPU:     8
Caché de modelos: /home/usuario/.cache/stemseparator/models
Dispositivo:      cpu
```

Termina con el código 0 si está todo, y con el 4 si falta FFmpeg, FFprobe o `audio-separator`,
explicando cómo arreglarlo. En los Mac con Apple Silicon, el dispositivo puede ser `mps` en
lugar de `cpu`.

## `--overwrite` y los archivos `.stemseparator-tmp-*`

StemSeparator nunca deja archivos a medias en tu carpeta de salida: separa y exporta todo en una
carpeta temporal, comprueba cada stem y solo al final los traslada a la carpeta de salida. Si
algo falla antes, la carpeta de salida queda como estaba.

Con `--overwrite`, durante ese traslado final cada archivo que se va a reemplazar se renombra
primero a una copia de seguridad con el prefijo `.stemseparator-tmp-`:

```
.stemseparator-tmp-<id>-cancion_vocals.wav.bak
```

`<id>` es un código aleatorio de 8 caracteres, distinto en cada ejecución. Si el traslado sale
bien, las copias `.bak` se borran al terminar; si falla, StemSeparator devuelve cada original a
su nombre. Si la salida está en otro disco que la carpeta temporal, cada stem se copia primero a
`.stemseparator-tmp-<id>-<nombre>` (sin `.bak`) y después se renombra a su nombre definitivo.

Si pulsas Ctrl+C durante ese traslado, StemSeparator no lo corta a medias: termina el traslado,
avisa de que la interrupción llegó cuando el trabajo ya se estaba guardando y sale con el
código 0, con los stems completos.

**Si te encuentras un archivo `.stemseparator-tmp-*`** en la carpeta de salida, una ejecución
terminó de forma brusca justo durante el traslado (un corte de luz, el proceso matado a la
fuerza) o falló al restaurar. StemSeparator no los borra solo. En Windows no están ocultos,
aunque empiecen por punto.

- **Si termina en `.bak`**, contiene **tu archivo original**. Para recuperarlo, quítale el
  prefijo `.stemseparator-tmp-<id>-` y la extensión `.bak`: por ejemplo,
  `.stemseparator-tmp-3f9a1c2e-cancion_vocals.wav.bak` → `cancion_vocals.wav`. Si ya existe un
  archivo con ese nombre, es el stem nuevo; decide cuál conservas antes de renombrar.
- **Si no termina en `.bak`**, es una copia a medias de un stem nuevo. Se puede borrar.

**Código de salida 6.** Es el único caso en que hay que intervenir a mano: el trabajo falló, y
al devolver los originales a su sitio falló también algún renombrado. Ningún archivo tuyo se ha
perdido: sigue en su `.bak`. El mensaje de error dice, archivo por archivo, qué `.bak` hay que
renombrar y a qué nombre. Un script no debe reintentar ante un 6: primero hay que hacer esos
renombrados.

## Códigos de salida

| Código | Significado |
|--------|-------------|
| 0 | Éxito. |
| 1 | Error inesperado (repite con `--verbose` para ver el detalle). |
| 2 | Uso incorrecto: una opción o un valor que no existe. |
| 3 | Problema con la entrada o la petición: archivo inexistente o sin audio, formato, modelo o stem no admitido, o archivos de salida que ya existen. |
| 4 | Falta algo en el sistema: FFmpeg, FFprobe, `audio-separator` o espacio en disco. |
| 5 | Falló la separación o la exportación. La carpeta de salida está intacta. |
| 6 | Falló la restauración tras un error: hay que renombrar copias `.bak` a mano (ver arriba). |
| 130 | Cancelado con Ctrl+C. |

## Limitaciones conocidas

- **Tarda.** En CPU, separar lleva tiempo, y los modelos de más calidad pueden tardar varias
  veces la duración de la canción. StemSeparator mide el tiempo y lo muestra; `models` da una
  estimación por modelo.
- **`vocals-max` es mucho más lento que `vocals`.** En el equipo de referencia, `vocals-max`
  (BS-Roformer) tarda unas 37 veces la duración del audio, y `vocals`, el predeterminado,
  unas 2,4 veces.
- **La separación nunca es perfecta.** Pueden quedar restos de instrumentos en la voz, o de voz
  en el instrumental, y artefactos (sonido "acuoso" o metálico). La calidad depende mucho de la
  mezcla original.
- **Los derechos son cosa tuya.** Eres responsable de tener derechos sobre el material que
  procesas.
- **Pérdida de calidad con MP3 y M4A.** Toda entrada se convierte primero a WAV. Si la entrada es
  `.mp3` o `.m4a`, ya venía comprimida con pérdida y se vuelve a comprimir al exportar; con
  `-f mp3` hay una segunda generación con pérdida.
- **Siempre en estéreo.** Una entrada mono se exporta en estéreo, y una con más de dos canales
  se mezcla a estéreo.
- **Siempre a 44 100 Hz.** Una entrada de 48 kHz o 96 kHz se reamuestrea a 44 100 Hz, que es
  la frecuencia a la que trabaja el motor.
- **Los pesos de `vocals-max` no declaran licencia.** StemSeparator no los redistribuye (los
  descargas tú desde el repositorio de UVR), pero **para uso comercial tienes que comprobar por
  tu cuenta si puedes usarlos**. `models` lo indica como "Sin licencia declarada".
- **Los stems suenan algo más bajos.** Llevan una ganancia común de 1 o menos para que ninguno
  sature (ver "Ganancia de salida"). La ganancia se muestra al terminar.
- **En Mac con Apple Silicon, el motor puede usar MPS** (la GPU integrada) en lugar de la CPU.
  Es correcto, y `doctor` lo muestra.
- **macOS: solo Apple Silicon y macOS 14 o posterior.** PyTorch y ONNX Runtime solo publican
  paquetes para esos Mac, así que en un Mac Intel o con un macOS anterior a la 14 la
  instalación con `uv sync` falla.

## Desarrollo

Las especificaciones del proyecto están en [`specs/`](specs/), que es la única fuente de verdad.

```bash
uv run pytest                 # tests rápidos: sin red, sin modelos, sin FFmpeg
uv run pytest -m ffmpeg       # integración con FFmpeg real
uv run pytest -m slow         # integración con el modelo real (lentos; descargan los pesos)
uv run mypy src
uv run ruff check . && uv run ruff format --check .
```
