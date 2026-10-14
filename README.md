# Generador de prompts por terminal

Python 3.10+. Un modelo compatible con OpenAI entrevista al usuario en español y devuelve campos JSON. **La IA no redacta el prompt final:** `plantilla.py` utiliza una estructura fija y el rol elegido de `roles.json`.

## Instalar y configurar (Windows / PowerShell)

```powershell
cd "ruta\al\proyecto"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

El catálogo detectado incluye GLM-5.3 Flash, Nemotron-3.5 Lightning, Nemotron-3 Super, Nemotron-3 Ultra y Kimi K3. El enrutamiento deja Super y Kimi sin activar como reservas manuales; para complejidad alta elegí Nemotron 3 Ultra, cuya ficha NVIDIA lo orienta a razonamiento complejo y análisis de documentos largos. GLM-5.3 Flash es el modelo de uso habitual; Lightning queda como alternativa de Flash cuando el endpoint rechaza el modelo principal. [GLM-5.3 Flash](https://build.nvidia.com/z-ai/glm-5-3-flash/modelcard) · [Nemotron-3 Ultra](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b/modelcard) · [Nemotron-3.5 Lightning](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/build).

Completa `api_key` con una clave NVIDIA vigente y revisa los tres modelos de `.env`:

```dotenv
base_url=https://integrate.api.nvidia.com/v1
api_key=tu-clave-nvidia-nueva
modelo_flash=z-ai/glm-5.3-flash
modelo_flash_alternativo=nvidia/nemotron-3.5-lightning-30b-a3b
modelo_complejo=nvidia/nemotron-3-ultra-550b-a55b
```

Las tres selecciones de modelo están en `.env`; puedes cambiar allí los identificadores del catálogo. La aplicación primero intenta GLM-5.3 Flash y prueba Lightning si NVIDIA devuelve un error de modelo inexistente. Las solicitudes habituales usan Flash. Una solicitud que se declara muy compleja, pide un examen exhaustivo o abarca muchas partes del proyecto usa Nemotron-3 Ultra.

La selección de complejidad es una regla local, no otra consulta de IA: reconoce frases como «muy complejo», «exhaustivo», «integral», «de extremo a extremo» y peticiones largas o que combinan varias áreas de proyecto. Si tu pedido queda clasificado incorrectamente, puedes volver a indicar su alcance con claridad o ajustar los términos de la regla en `main.py`.

El endpoint NVIDIA ofrece estos modelos como API OpenAI compatible; la disponibilidad, cuotas y condiciones del trial pueden cambiar. Consulta las fichas y condiciones vigentes en NVIDIA Build antes de usarlos.

La configuración se lee exclusivamente del `.env` junto a `main.py`; no se toma de variables globales ni se incorpora al código. Usa una clave NVIDIA nueva, revoca cualquier clave previamente escrita dentro de un `.py` y guárdala únicamente en `.env`. No hace falta activar el entorno virtual. `.gitignore` excluye `.env`, el entorno y los prompts guardados.

## Usar

```powershell
.\.venv\Scripts\python.exe main.py
```

Escribe el pedido inicial. El modelo puede hacer hasta cuatro preguntas por ronda. Responde en una línea, separando las respuestas por número si hay varias. Puedes escribir `no sé` o `da igual` para que el modelo registre una decisión razonable como supuesto. `Ctrl+C` cancela la sesión.

La entrevista termina cuando llegan campos válidos o tras cuatro rondas de preguntas respondidas. En este último caso se realiza una llamada de cierre para completar lo pendiente con supuestos. Normalmente usa entre **una y cinco llamadas**, más hasta dos correcciones si el modelo incumple el JSON. GLM Flash usa razonamiento `low` y oculta el contenido interno de razonamiento. Cada llamada puede tardar hasta **60 segundos** y reintentarse dos veces si hay fallos de transporte o límites temporales.

Se muestra el prompt final, se intenta copiar al portapapeles y se pregunta si quieres guardarlo. Responde `s` para crear un Markdown en `prompts_generados/`, junto al programa:

```text
prompt_2026-10-14_15-30-00_123456.md
```

El nombre usa la fecha y hora local del equipo, con microsegundos; no se sobrescriben archivos existentes. La ruta funciona incluso si ejecutas `main.py` desde otra carpeta.

El portapapeles usa `clip.exe` en Windows, `pbcopy` en macOS o, si están disponibles, `wl-copy`, `xclip` o `xsel` en Linux. Si la herramienta falta o falla, puedes copiar el resultado de la pantalla y guardarlo igualmente.

## Ejemplo de ejecución

Ejemplo ilustrativo: las preguntas y los campos dependen del modelo y sus respuestas.

```text
Generador de prompts — la IA entrevista; una plantilla fija redacta.

¿Qué necesitas? analiza mi proyecto como abogado cyber

Preguntas (1/4):
1. ¿En qué país se usará el proyecto?
2. ¿Qué material analizarás: código, sitio web o documentación?
3. ¿Qué tipo de proyecto es y qué datos maneja?
4. ¿Quieres una revisión breve o detallada?
Tu respuesta (puedes responder por número): 1 Chile. 2 Repositorio Python con README. 3 Proyecto académico para adultos; guarda correos. 4 Detallada, centrada en privacidad.

--- PROMPT FINAL ---

## Rol
Actúa como abogado de derecho informático y ciberseguridad.

## Tarea
Revisar los riesgos legales de privacidad de un proyecto académico que almacena correos.

## Contexto
Chile; proyecto académico para adultos; revisión detallada.

## Criterios
- Analiza privacidad, tratamiento de datos, propiedad intelectual, contratos y obligaciones de ciberseguridad según el alcance del material.
- Determina las jurisdicciones pertinentes según el contexto; aplica las leyes chilenas y las normas internacionales cuando correspondan.
- Verifica la vigencia y fecha de aplicación de las normas en fuentes oficiales, cita los artículos pertinentes y no inventes obligaciones ni referencias.
- Distingue riesgos confirmados, hipótesis y cuestiones que requieren documentación adicional.
- Dar prioridad a la privacidad en el almacenamiento de correos.

## Formato de respuesta
Entrega un resumen, una tabla de riesgos con evidencia, fundamento normativo y medida recomendada, y una lista de información pendiente.

## Material a analizar
Repositorio Python con README que el usuario proporcionará para la revisión.

## Supuestos asumidos
- El repositorio se adjuntará al utilizar el prompt final.

Prompt copiado al portapapeles.

¿Guardar en prompts_generados/ como .md? [s/N]: s
Guardado en: prompts_generados\prompt_2026-10-14_15-30-00_123456.md
```

## Editar o agregar roles

`roles.json` contiene una lista. Cada objeto tiene cuatro campos:

```json
{
  "id": "editor_tecnico",
  "descripcion": "editor de documentación técnica",
  "criterios": ["Evalúa claridad y consistencia.", "Propón ejemplos concretos."],
  "formato_salida": "Lista priorizada de correcciones."
}
```

Agrega el objeto a la lista existente, con coma entre objetos y un `id` único. No hace falta modificar Python. El modelo recibe únicamente el id y la descripción de cada rol; los criterios y el formato predeterminado se aplican localmente. Si el modelo devuelve un id desconocido, la terminal muestra el catálogo y solicita uno válido sin gastar otra llamada.

Los cinco roles iniciales son `evaluador_software`, `abogado_informatico`, `auditor_seguridad`, `revisor_tesis` y `profesor_tutor`.

## Validación y errores

- Se extrae el objeto JSON aunque venga entre backticks o con texto alrededor. Se validan campos, tipos y textos obligatorios; no se aceptan campos adicionales como `prompt_final`.
- `rol_id`, `tarea`, `material` y `contexto` son textos obligatorios. `criterios_extra` y `supuestos` son listas de textos y pueden omitirse; `formato_salida` puede omitirse o ser una cadena vacía para usar el formato del rol.
- Ante JSON o campos inválidos se pide al modelo responder solo con JSON válido, hasta dos correcciones. Si sigue fallando, termina con un mensaje claro.
- La plantilla combina criterios del catálogo y criterios extra, y usa un formato alternativo únicamente cuando el modelo registra uno solicitado por el usuario. Agrega `Supuestos asumidos` solo si hay supuestos.
- Los errores transitorios de red, HTTP 408/409/429 y fallos del servidor tienen dos reintentos, con espera de 2 y 4 segundos. Un `Retry-After` numérico puede ampliar la espera hasta 30 segundos. Cada solicitud tiene timeout de 60 segundos; los reintentos internos del SDK están desactivados.
- Una clave rechazada, un modelo inexistente o una solicitud incompatible producen mensajes de configuración sin mostrar credenciales ni cuerpos de error del proveedor.
- Si el pedido no es complejo, el modelo seleccionado es GLM-5.3 Flash; si la API no lo encuentra, se prueba Nemotron-3.5 Lightning. Un pedido complejo se envía a Nemotron-3 Ultra sin degradar silenciosamente a un modelo ligero.
- No se abren automáticamente repositorios, archivos ni URLs mencionados por el usuario. El material se describe en el prompt; tendrás que adjuntarlo al utilizarlo. El pedido y tus respuestas sí se envían al endpoint que configures.
- La forma del JSON se valida por código; la exactitud de los campos y la identificación de todos los supuestos dependen del modelo. Revisa el prompt antes de utilizarlo.

## Pruebas locales

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

Las pruebas no necesitan `.env`, no usan un proveedor real y no modifican el portapapeles. Comprueban la plantilla, el catálogo editable, la configuración, la entrevista, los límites, las correcciones, los errores de red y el guardado.

## Archivos

| Archivo | Responsabilidad |
| --- | --- |
| `main.py` | Configuración, entrevista, validación, portapapeles y guardado. |
| `roles.json` | Roles, criterios y formatos editables. |
| `plantilla.py` | Redacción fija del prompt final, sin llamadas de IA. |
| `.env.example` | Valores de configuración que debes completar en `.env`. |
| `requirements.txt` | SDK OpenAI y lector `.env`. |
| `test_generador.py` | Comprobaciones ejecutables con `unittest`. |

La integración utiliza Chat Completions del [SDK oficial de OpenAI](https://developers.openai.com/api/reference/python) para facilitar el uso de endpoints compatibles.
