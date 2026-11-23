# StemSeparator

*[Versión en español](README.md)*

StemSeparator is a command-line tool that splits an audio or video file into tracks (*stems*):
in this version, the **vocals** (`vocals`) and the **instrumental** (`instrumental`). You can
use it, for example, to get the instrumental of a song for practice or karaoke, to isolate the
vocals to transcribe the lyrics, or to extract the vocals from a video without converting it by
hand first.

- It runs **locally**: your audio never leaves your computer and there is no telemetry. It only
  goes online to download a model's weights the first time that model is used.
- It runs **on the CPU only**: no graphics card needed.
- It accepts `.wav`, `.mp3`, `.flac`, `.m4a`, `.mp4` and `.mkv`, and exports WAV (24-bit),
  FLAC (24-bit) or MP3 (320 kbps).

The command-line interface is in Spanish. The examples below show its real output, with
explanations in English.

## Installation

You need three things: FFmpeg, `uv` and StemSeparator itself.

### 1. FFmpeg

StemSeparator uses the system's `ffmpeg` and `ffprobe` programs to read the input and write the
stems. They are installed together:

| System | Command |
|--------|---------|
| Windows | `winget install Gyan.FFmpeg` |
| macOS | `brew install ffmpeg` (with [Homebrew](https://brew.sh)) |
| Linux (Debian/Ubuntu) | `sudo apt install ffmpeg` |

Then open a new terminal and check with `ffmpeg -version`. If FFmpeg is missing, StemSeparator
says so and repeats these same instructions.

### 2. `uv`

StemSeparator is installed and run with [`uv`](https://docs.astral.sh/uv/), which also
downloads a compatible Python version (3.11 or 3.12) if you don't have one:

- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS and Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

### 3. StemSeparator

```bash
git clone https://github.com/MockAh/separador-audio-101.git
cd separador-audio-101
uv sync
uv run stemseparator doctor
```

`uv sync` installs the dependencies into an environment of the project's own (about 1.3 GB,
mostly the CPU build of PyTorch). `doctor` checks that everything is in place: if it exits with
code 0, you are ready to separate.

On macOS, only Apple Silicon Macs running macOS 14 or later are supported (see
[Known limitations](#known-limitations)).

## Usage

All commands are run from the project folder with `uv run stemseparator …`.
`uv run stemseparator --help` and `uv run stemseparator <command> --help` show the help.

### `separate`: split a file

```bash
uv run stemseparator separate cancion.wav
```

This creates `cancion_vocals.wav` and `cancion_instrumental.wav` in the current folder. Each
stem is named `<input name without extension>_<stem>.<format>`. This is what a run on a 10 s
clip looks like (the stages are validation, preparation, model loading, separation and export;
the table lists stem, path, duration and size):

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

More examples:

```bash
# From a video, with the stems in ./stems, as FLAC and with the highest-quality model
uv run stemseparator separate concierto.mp4 -o stems -m vocals-max -f flac

# Vocals only, as MP3
uv run stemseparator separate cancion.mp3 -s vocals -f mp3

# Run again over existing output, replacing the files
uv run stemseparator separate cancion.wav -o stems --overwrite
```

| Option | What it does |
|--------|--------------|
| `-o`, `--output DIR` | Output folder (default: the current one). Created if it doesn't exist. |
| `-m`, `--model ID` | Model from the catalogue (default: `vocals`). `models` lists them. |
| `-s`, `--stems LIST` | Comma-separated stems, e.g. `vocals,instrumental` (default: all). |
| `-f`, `--format wav\|flac\|mp3` | Output format (default: `wav`). |
| `--overwrite` | Replaces output files that already exist (see below). |
| `-v`, `--verbose` | Shows the engine and FFmpeg logs, and the technical details of errors. |

If any output file already exists and you don't pass `--overwrite`, StemSeparator separates
nothing: it lists the conflicting files and exits.

**Output gain.** So that no stem clips, all stems are exported with one shared gain, the
smallest reduction needed (1.00 if none is needed). That is why the stems may sound somewhat
quieter than the original mix. Because the gain is shared, adding the vocals and the
instrumental gives back the mix multiplied by that gain, which makes the stems useful for
remixing.

**Ctrl+C** cancels the separation: StemSeparator deletes the work in progress, leaves the
output folder as it was and exits with code 130.

### `models`: list the available models

```bash
uv run stemseparator models
```

```
vocals (por defecto)
Separador de voz MDX-Net del proyecto UVR. Buena calidad con un coste en CPU
moderado; opción práctica para uso habitual.
  Stems:        vocals, instrumental
  Coste en CPU: ≈1,5× (unos 5 min)
  Licencia:     MIT (UVR), con atribución
  Descargado:   sí

vocals-max
Separador de voz BS-Roformer (viperx). El SDR de referencia más alto del
catálogo, con un coste en CPU mucho mayor.
  Stems:        vocals, instrumental
  Coste en CPU: ≈47,7× (unas 2 h 47 min)
  Licencia:     Sin licencia declarada
  Descargado:   no

Coste medido en el equipo de referencia (Intel Core i7-8565U, 4 núcleos / 8
hilos, WSL2, ADR-002) para una canción de 3:30.
```

There are two models:

- `vocals` (the default) is the UVR project's MDX-Net vocal separator: good quality at a
  moderate CPU cost, the practical choice for everyday use. MIT licence (UVR), with attribution.
- `vocals-max` is the BS-Roformer vocal separator (viperx): the highest reference SDR in the
  catalogue, at a much higher CPU cost. Its weights declare no licence ("Sin licencia
  declarada").

The CPU cost ("Coste en CPU") is how many times the audio's duration the separation takes on
the reference machine (Intel Core i7-8565U, 4 cores / 8 threads, WSL2), and in brackets what
that means for a 3:30 song. It will differ on another computer. "Descargado" says whether the
model's weights are already downloaded.

Each model's weights are downloaded the first time it is used: about 67 MB for `vocals` and
about 640 MB for `vocals-max`. They are kept in the user's model cache and never downloaded
again. To keep them in another folder, set the `STEMSEPARATOR_MODEL_DIR` environment variable.

### `doctor`: check the installation

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

It shows the versions, the CPU threads, the model cache and the device. It exits with code 0
if everything is there, and with 4 if FFmpeg, FFprobe or `audio-separator` is missing,
explaining how to fix it. On Apple Silicon Macs, the device may be `mps` instead of `cpu`.

## `--overwrite` and the `.stemseparator-tmp-*` files

StemSeparator never leaves half-written files in your output folder: it separates and exports
everything in a temporary folder, checks each stem, and only at the end moves them to the
output folder. If anything fails before that, the output folder is left as it was.

With `--overwrite`, during that final move each file about to be replaced is first renamed to a
backup with the `.stemseparator-tmp-` prefix:

```
.stemseparator-tmp-<id>-cancion_vocals.wav.bak
```

`<id>` is a random 8-character code, different on each run. If the move succeeds, the `.bak`
copies are deleted at the end; if it fails, StemSeparator puts each original back under its
name. If the output is on a different disk from the temporary folder, each stem is first copied
to `.stemseparator-tmp-<id>-<name>` (without `.bak`) and then renamed to its final name.

If you press Ctrl+C during that move, StemSeparator doesn't cut it short: it finishes the move,
warns that the interruption arrived while the work was already being saved, and exits with
code 0, with the stems complete.

**If you find a `.stemseparator-tmp-*` file** in the output folder, a run ended abruptly right
during the move (a power cut, the process killed by force) or failed while restoring.
StemSeparator doesn't delete them by itself. On Windows they are not hidden, even though they
start with a dot.

- **If it ends in `.bak`**, it holds **your original file**. To recover it, remove the
  `.stemseparator-tmp-<id>-` prefix and the `.bak` extension: for example,
  `.stemseparator-tmp-3f9a1c2e-cancion_vocals.wav.bak` → `cancion_vocals.wav`. If a file with
  that name already exists, it is the new stem; decide which one to keep before renaming.
- **If it doesn't end in `.bak`**, it is a half-copied new stem. You can delete it.

**Exit code 6.** This is the only case where you have to step in by hand: the job failed, and
while putting the originals back some rename failed too. None of your files is lost: it is
still in its `.bak`. The error message says, file by file, which `.bak` to rename and to what
name. A script should not retry on a 6: those renames have to be done first.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success. |
| 1 | Unexpected error (run again with `--verbose` to see the details). |
| 2 | Incorrect usage: an option or value that doesn't exist. |
| 3 | Problem with the input or the request: missing file or file without audio, unsupported format, model or stem, or output files that already exist. |
| 4 | Something is missing from the system: FFmpeg, FFprobe, `audio-separator` or disk space. |
| 5 | The separation or the export failed. The output folder is untouched. |
| 6 | Restoring after an error failed: `.bak` copies must be renamed by hand (see above). |
| 130 | Cancelled with Ctrl+C. |

## Known limitations

- **It takes time.** On the CPU, separating is slow, and the higher-quality models can take
  several times the song's duration. StemSeparator measures and shows the time; `models` gives
  an estimate per model.
- **`vocals-max` is much slower than `vocals`.** On the reference machine, `vocals-max`
  (BS-Roformer) takes about 48 times the audio's duration, and `vocals`, the default, about
  1.5 times.
- **Separation is never perfect.** Traces of instruments may remain in the vocals, or of vocals
  in the instrumental, along with artefacts ("watery" or metallic sound). Quality depends
  heavily on the original mix.
- **Rights are your responsibility.** You are responsible for holding the rights to the
  material you process.
- **Quality loss with MP3 and M4A.** Every input is first converted to WAV. If the input is
  `.mp3` or `.m4a`, it was already lossy and is compressed again on export; with `-f mp3`
  there is a second lossy generation.
- **Always stereo.** A mono input is exported in stereo, and one with more than two channels is
  downmixed to stereo.
- **Always 44,100 Hz.** A 48 kHz or 96 kHz input is resampled to 44,100 Hz, the rate the
  engine works at.
- **The `vocals-max` weights declare no licence.** StemSeparator doesn't redistribute them (you
  download them yourself from the UVR repository), but **for commercial use you must check on
  your own whether you may use them**. `models` shows this as "Sin licencia declarada".
- **The stems sound somewhat quieter.** They carry a shared gain of 1 or less so that none of
  them clips (see "Output gain"). The gain is shown at the end.
- **On Apple Silicon Macs, the engine may use MPS** (the integrated GPU) instead of the CPU.
  This is expected, and `doctor` shows it.
- **macOS: Apple Silicon and macOS 14 or later only.** PyTorch and ONNX Runtime only publish
  packages for those Macs, so on an Intel Mac or on macOS earlier than 14, installing with
  `uv sync` fails.

## Development

The project's specifications are in [`specs/`](specs/), the single source of truth. They are
written in Spanish.

```bash
uv run pytest                 # fast tests: no network, no models, no FFmpeg
uv run pytest -m ffmpeg       # integration with real FFmpeg
uv run pytest -m slow         # integration with the real model (slow; downloads the weights)
uv run mypy src scripts
uv run ruff check . && uv run ruff format --check .
```
