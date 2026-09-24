# ADR-001 — Motor de separación: audio-separator detrás de un puerto propio

Estado: Propuesto · Fecha: 2026-09-24

## Contexto
Necesitamos separar audio con modelos preentrenados de alta calidad, sin entrenar modelos propios, en CPU, y con un camino claro hacia 4, 6 y más stems. Las familias relevantes (Roformer, MDX-Net, Demucs) están disponibles por separado, cada una con su propia API y formato de pesos.

## Opciones
1. **Demucs directamente.** Maduro y sencillo, pero inferior a los Roformer en voz/instrumental, y solo cubre su propia familia.
2. **audio-separator.** Envuelve Roformer, MDX-Net, MDXC, VR y Demucs con una sola API, descarga los pesos y admite CPU vía ONNX Runtime/PyTorch. Depende de un proyecto comunitario y de muchos paquetes.
3. **Integrar cada familia a mano.** Máximo control, coste de mantenimiento muy alto.

## Decisión
Opción 2, con dos salvaguardas: (a) el núcleo solo conoce el protocolo `SeparationEngine`, y `audio-separator` se importa en un único módulo; (b) el usuario elige modelos de un catálogo curado con identificadores propios, no nombres de archivo del motor.

## Consecuencias
- Acceso inmediato a los mejores modelos de voz y a modelos de 4/6 stems para I3–I4.
- Si `audio-separator` cambia su API o deja de mantenerse, el impacto se limita a un adaptador.
- Hay que fijar su versión y revisar la licencia de los pesos de cada modelo del catálogo.
