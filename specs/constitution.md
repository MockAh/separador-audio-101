# Constitución del proyecto StemSeparator

Estado: Aprobado v0.1 (2026-09-24)

Este documento fija los principios que no cambian de una tarea a otra. Cualquier excepción requiere un ADR aprobado por el usuario.

## 1. Principios

**P1. Spec primero.** Ninguna funcionalidad se implementa sin una spec aprobada que la describa (RF en `product.md`) y un criterio de aceptación que la verifique (CA en `acceptance.md`). Si al implementar se descubre que la spec es incorrecta o incompleta, se detiene el trabajo y se propone el cambio de spec.

**P2. Trazabilidad.** Cada RF se cubre con al menos un CA; cada CA se verifica con al menos un test o un procedimiento manual documentado; cada tarea (T-xx) declara los RF/CA que cubre. Un RF sin CA o un CA sin verificación es un defecto de la spec.

**P3. Incremento pequeño y completo.** Se avanza por incrementos que funcionan de extremo a extremo (entrada → separación → archivos exportados). No se construye infraestructura para incrementos futuros que no estén especificados.

**P4. Calidad antes que amplitud.** Un stem nuevo solo entra al producto cuando su calidad se ha medido con el protocolo de `acceptance.md` §4 y se ha documentado en un ADR. Preferimos dos stems excelentes a seis mediocres.

**P5. El motor es un detalle.** El núcleo de la aplicación no conoce `audio-separator`. Depende de la interfaz `SeparationEngine`. Cambiar o añadir un motor no debe tocar la CLI ni el dominio.

**P6. Honestidad con el hardware.** El producto se diseña para CPU. Los tiempos de proceso se miden, se muestran al usuario y se documentan; no se prometen tiempos que no se han medido.

**P7. Errores accionables.** Todo error visible al usuario dice qué pasó, por qué, y qué puede hacer. Nunca se muestra un traceback al usuario salvo con `--verbose`.

**P8. Nada de archivos corruptos.** Si una ejecución falla, el directorio de salida queda como estaba antes.

**P9. Simplicidad.** Ante dos diseños que cumplen la spec, se elige el más simple. Las abstracciones se introducen cuando hay un segundo caso real que las necesita.

## 2. Convenciones

- **Idiomas**: código, identificadores, commits y logs técnicos en inglés; specs, documentación y mensajes al usuario en español.
- **Identificadores**: `RF-xx`, `RNF-xx`, `CA-xx`, `T-xx`, `ADR-xxx`. Los números no se reutilizan; un requisito eliminado se marca como `[RETIRADO]`.
- **Estados de un documento**: Borrador → Aprobado → (Modificado, con fecha y motivo en un registro de cambios al final del documento).
- **Estados de un ADR**: Propuesto → Aceptado / Rechazado → (Sustituido por ADR-yyy).
- **Estilo**: `ruff` (lint + format), `mypy --strict`, docstrings breves en funciones públicas.

## 3. Definición de Hecho (DoD) de una tarea

Una tarea está hecha cuando se cumplen todas estas condiciones:
1. Los tests que la cubren existen y pasan (`uv run pytest`).
2. `uv run mypy src` y `uv run ruff check .` pasan sin errores.
3. El comportamiento coincide con la spec; si no, la spec se actualizó y se aprobó.
4. La tarea está marcada en `tasks.md` con los RF/CA cubiertos.
5. Se entregó el reporte al usuario y este autorizó continuar.

## 4. Gobierno de cambios

- Cambiar un RF, un CA o una interfaz pública requiere aprobación explícita del usuario.
- Añadir una dependencia requiere justificarla (en la tarea o en un ADR si es estructural).
- Añadir un modelo al catálogo requiere: medición de calidad y tiempo en CPU, licencia de los pesos revisada, y ADR.
