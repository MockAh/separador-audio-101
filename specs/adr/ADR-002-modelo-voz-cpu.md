# ADR-002 — Modelos de voz/instrumental para CPU y modelo predeterminado

Estado: Propuesto (pendiente de datos del spike T-01) · Fecha: 2026-09-24

## Contexto
El usuario ejecutará StemSeparator solo en CPU y prioriza la calidad de la separación de voz. Los modelos Roformer ofrecen la mejor calidad en voz/instrumental, pero son los más costosos en CPU; los MDX-Net en ONNX son bastante más rápidos con calidad algo menor.

## Propuesta
- Catálogo inicial con dos entradas: `vocals-hq` (Roformer de voz) y `vocals-fast` (MDX-Net de voz).
- Predeterminado: `vocals-hq`, porque el objetivo del producto es la calidad. `vocals-fast` queda para pruebas rápidas o equipos lentos.
- La elección definitiva se toma con los datos medidos, no con esta propuesta.

## Datos a completar en T-01

| id | engine_filename | Tiempo CPU por minuto de audio | Licencia de pesos | Notas de escucha |
|----|-----------------|-------------------------------|-------------------|------------------|
| vocals-hq | _por confirmar_ | _por medir_ | _por confirmar_ | |
| vocals-fast | _por confirmar_ | _por medir_ | _por confirmar_ | |

Equipo de medición: _CPU, núcleos/hilos, RAM, SO_.

## Criterio de decisión
Si `vocals-hq` tarda más de ~10× la duración del audio en el equipo del usuario, se reconsidera el predeterminado con el usuario antes de aceptar este ADR.

## Consecuencias
- El usuario sabrá de antemano cuánto tarda cada modelo (se mostrará en `models`).
- I2 puede sustituir el predeterminado con evaluación completa (§4 de acceptance.md).
