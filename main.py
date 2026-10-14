"""Entrevista por terminal; la IA rellena campos y plantilla.py redacta."""

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from plantilla import generar_prompt


BASE_DIR = Path(__file__).resolve().parent
MAX_TURNOS = 4
MAX_REINTENTOS = 2
INDICADORES_COMPLEJIDAD = (
    r"\bmuy complej[oa]\b", r"\bextremadamente complej[oa]\b",
    r"\bexhaustiv[oa]\b", r"\bauditor[ií]a integral\b",
    r"\ban[aá]lisis integral\b", r"\bde extremo a extremo\b",
    r"\btodo (?:el|mi) (?:proyecto|sistema|repositorio)\b",
    r"\bvarios (?:proyectos|sistemas|pa[ií]ses|repositorios)\b",
)
AREAS_DE_PROYECTO = (
    r"\barquitectura\b", r"\bintegraciones?\b", r"\bbases? de datos\b",
    r"\bm[oó]dulos?\b", r"\bseguridad\b", r"\bpruebas\b",
    r"\bdespliegue\b", r"\bdocumentaci[oó]n\b", r"\bdependencias\b",
)

SYSTEM_PROMPT = """Eres un asistente que ayuda a armar prompts. El usuario suele dar pedidos vagos y le cuesta especificar lo que necesita, así que tu trabajo es entrevistarlo.

Objetivo: obtener rol, tarea, material, contexto, criterios y formato de salida.

Reglas:
- Elige el rol SOLO de la lista de roles disponibles que se te entrega.
- Haz máximo 4 preguntas por turno, cortas, concretas y fáciles de responder (ofrece opciones cuando se pueda).
- No repitas preguntas ya respondidas ni preguntes cosas que se deducen del pedido.
- Si el usuario responde "no sé" o "da igual", asume algo razonable y anótalo en `supuestos`.
- Cuando tengas la información suficiente, responde ÚNICAMENTE con el JSON, sin texto extra ni backticks.
- Si todavía falta información, responde ÚNICAMENTE con las preguntas, numeradas.
- Habla en español neutro y simple, sin jerga innecesaria.
"""

ESQUEMA = {
    "rol_id": "id de uno de los roles disponibles",
    "tarea": "qué debe hacer, concreto",
    "material": "qué se va a analizar y de qué tipo es",
    "contexto": "país, tipo de proyecto, público, nivel de detalle",
    "criterios_extra": [],
    "formato_salida": "",
    "supuestos": [],
}


def cargar_configuracion(ruta: Path = BASE_DIR / ".env") -> dict:
    """Lee solo el archivo .env; no obtiene secretos del entorno global."""
    valores = dotenv_values(ruta, encoding="utf-8-sig", interpolate=False)
    config = {campo: (valores.get(campo) or "").strip()
              for campo in ("base_url", "api_key", "modelo", "modelo_flash",
                            "modelo_flash_alternativo", "modelo_complejo")}
    config["modelo_flash"] = config["modelo_flash"] or config["modelo"]
    config["modelo_complejo"] = config["modelo_complejo"] or config["modelo"] or config["modelo_flash"]
    faltantes = [campo for campo in ("base_url", "api_key", "modelo_flash", "modelo_complejo")
                 if not config[campo]]
    if faltantes:
        raise ValueError("Copia .env.example a .env y completa: " + ", ".join(faltantes))
    url = urlparse(config["base_url"])
    if url.scheme not in ("https", "http") or not url.netloc:
        raise ValueError("base_url debe ser una URL http:// o https:// del endpoint compatible.")
    return config


def es_peticion_compleja(pedido: str) -> bool:
    """Detecta alcance explícitamente amplio sin pagar una llamada de clasificación."""
    if any(re.search(indicador, pedido, re.IGNORECASE) for indicador in INDICADORES_COMPLEJIDAD):
        return True
    if len(pedido.split()) >= 100:
        return True
    areas_mencionadas = sum(
        bool(re.search(area, pedido, re.IGNORECASE)) for area in AREAS_DE_PROYECTO
    )
    return len(pedido.split()) >= 20 and areas_mencionadas >= 4


def seleccionar_modelo(pedido: str, config: dict) -> tuple[str, str, tuple[str, ...]]:
    """Usa el Flash configurado y escala solo los pedidos de alcance complejo."""
    if es_peticion_compleja(pedido):
        return config["modelo_complejo"], "solicitud compleja", ()
    alternativa = config.get("modelo_flash_alternativo", "")
    alternativos = (alternativa,) if alternativa and alternativa != config["modelo_flash"] else ()
    return config["modelo_flash"], "solicitud habitual; prioridad Flash", alternativos


def cargar_roles(ruta: Path = BASE_DIR / "roles.json") -> dict:
    """Valida el catálogo editable y lo indexa por id, sin listas fijas en código."""
    catalogo = json.loads(ruta.read_text(encoding="utf-8-sig"))
    if not isinstance(catalogo, list) or not catalogo:
        raise ValueError("roles.json debe contener una lista no vacía de roles.")
    roles = {}
    for rol in catalogo:
        if not isinstance(rol, dict):
            raise ValueError("Cada rol debe ser un objeto JSON.")
        for campo in ("id", "descripcion", "formato_salida"):
            if not isinstance(rol.get(campo), str) or not rol[campo].strip():
                raise ValueError(f"Cada rol necesita {campo} como texto no vacío.")
        criterios = rol.get("criterios")
        if not isinstance(criterios, list) or any(
            not isinstance(c, str) or not c.strip() for c in criterios
        ):
            raise ValueError("Los criterios de cada rol deben ser una lista de textos no vacíos.")
        if rol["id"] in roles:
            raise ValueError("roles.json contiene ids repetidos.")
        roles[rol["id"]] = rol
    return roles


def extraer_json(texto: str) -> dict:
    """Decodifica el objeto ignorando cercos Markdown y texto alrededor.

    raw_decode respeta llaves y comillas dentro de cadenas; una regex no lo haría.
    """
    decodificador = json.JSONDecoder()
    for posicion, caracter in enumerate(texto):
        if caracter == "{":
            try:
                objeto, _ = decodificador.raw_decode(texto[posicion:])
                return objeto
            except json.JSONDecodeError:
                continue
    raise ValueError("No se encontró un objeto JSON válido.")


def validar_campos(datos: dict) -> dict:
    """Rechaza tipos incorrectos y campos ajenos al contrato del entrevistador."""
    if not isinstance(datos, dict):
        raise ValueError("La respuesta debe ser un objeto JSON.")
    if set(datos) - set(ESQUEMA):
        raise ValueError("Usa únicamente los campos del esquema; no redactes un prompt final.")
    resultado = dict(datos)
    for campo in ("rol_id", "tarea", "material", "contexto"):
        if not isinstance(resultado.get(campo), str) or not resultado[campo].strip():
            raise ValueError(f"{campo} debe ser texto no vacío.")
        resultado[campo] = resultado[campo].strip()
    for campo in ("criterios_extra", "supuestos"):
        valor = resultado.setdefault(campo, [])
        if not isinstance(valor, list) or any(
            not isinstance(v, str) or not v.strip() for v in valor
        ):
            raise ValueError(f"{campo} debe ser una lista de textos no vacíos.")
        resultado[campo] = [v.strip() for v in valor]
    formato = resultado.setdefault("formato_salida", "")
    if not isinstance(formato, str):
        raise ValueError("formato_salida debe ser texto; usa una cadena vacía si no se especificó.")
    resultado["formato_salida"] = formato.strip()
    return resultado


def son_preguntas(texto: str) -> bool:
    """Solo muestra al usuario entre una y cuatro preguntas numeradas."""
    lineas = [linea.strip() for linea in texto.splitlines() if linea.strip()]
    return 1 <= len(lineas) <= 4 and all(
        re.fullmatch(rf"{numero}[.)]\s+.+", linea)
        for numero, linea in enumerate(lineas, 1)
    )


def consultar_modelo(cliente: OpenAI, modelo: str, mensajes: list,
                     modelos_alternativos: tuple[str, ...] = ()) -> str:
    """Reintenta fallos transitorios dos veces; nunca imprime claves ni cuerpos HTTP."""
    candidatos = (modelo, *modelos_alternativos)
    for posicion, candidato in enumerate(candidatos):
        for intento in range(MAX_REINTENTOS + 1):
            try:
                opciones = {}
                if candidato == "z-ai/glm-5.3-flash":
                    opciones = {
                        "reasoning_effort": "low",
                        "extra_body": {"chat_template_kwargs": {"clear_thinking": True}},
                    }
                respuesta = cliente.chat.completions.create(
                    model=candidato, messages=mensajes, **opciones
                )
                if not respuesta.choices:
                    raise RuntimeError("El endpoint devolvió una respuesta sin opciones de texto.")
                return respuesta.choices[0].message.content or ""
            except (APIConnectionError, APIStatusError) as error:
                estado = getattr(error, "status_code", None)
                if estado in (401, 403):
                    raise RuntimeError("El endpoint rechazó el acceso. Revisa api_key y los permisos del modelo en .env.") from None
                if estado == 404:
                    if posicion + 1 < len(candidatos):
                        print(f"El modelo Flash configurado no está disponible; probando {candidatos[posicion + 1]}.")
                        break
                    raise RuntimeError("No se encontró el endpoint o modelo. Revisa base_url y los modelos en .env.") from None
                transitorio = isinstance(error, APIConnectionError) or estado in (408, 409, 429) or (estado is not None and estado >= 500)
                if not transitorio:
                    raise RuntimeError(f"El endpoint rechazó la solicitud (HTTP {estado}). Revisa su compatibilidad y configuración.") from None
                motivo = ("Límite de uso o cuota del endpoint" if isinstance(error, RateLimitError)
                          else "Error de red o fallo temporal del endpoint")
                if intento == MAX_REINTENTOS:
                    raise RuntimeError(f"{motivo}: se agotaron los dos reintentos. Revisa conexión, cuota o disponibilidad e inténtalo más tarde.") from None
                espera = 2 ** (intento + 1)
                # Se respeta Retry-After numérico, acotado para no bloquear la terminal indefinidamente.
                cabecera = getattr(getattr(error, "response", None), "headers", {}).get("retry-after", "")
                try:
                    espera = min(30, max(espera, float(cabecera)))
                except ValueError:
                    pass
                print(f"{motivo}. Reintento {intento + 1}/2 en {espera:g} segundos.")
                time.sleep(espera)
    raise RuntimeError("No se pudo consultar el modelo.")


def leer_texto(mensaje: str) -> str:
    """No envía entradas vacías al modelo ni consume llamadas con ellas."""
    while True:
        texto = input(mensaje).strip()
        if texto:
            return texto
        print('Escribe una respuesta; puedes indicar "no sé" o "da igual".')


def elegir_rol(roles: dict) -> str:
    """La corrección de un id desconocido es local, sin otra llamada a la IA."""
    print("El modelo indicó un rol que no existe. Elige un id de esta lista:")
    for rol in roles.values():
        print(f"- {rol['id']}: {rol['descripcion']}")
    while True:
        elegido = leer_texto("Id del rol: ")
        if elegido in roles:
            return elegido
        print("Ese id no está en el catálogo.")


def entrevistar(cliente: OpenAI, modelo: str, roles: dict, pedido: str,
                modelos_alternativos: tuple[str, ...] = ()) -> dict:
    """Mantiene el historial, limita preguntas y exige JSON antes de generar."""
    resumen_roles = [{"id": r["id"], "descripcion": r["descripcion"]} for r in roles.values()]
    instrucciones = (
        SYSTEM_PROMPT
        + "\nRellena campos; NO redactes el prompt final. No inventes resultados ni contenido del material."
        + "\ncriterios_extra: solo criterios adicionales pedidos por el usuario."
        + "\nformato_salida: cadena vacía salvo que el usuario pida un formato concreto."
        + "\nsupuestos: lista de toda decisión no confirmada por el usuario, también cuando diga no sé o da igual."
        + "\nSi aún no se proporcionó material, describe el material esperado y marca esa decisión como supuesto."
        + "\nRoles disponibles: " + json.dumps(resumen_roles, ensure_ascii=False)
        + "\nEsquema JSON: " + json.dumps(ESQUEMA, ensure_ascii=False)
    )
    mensajes = [{"role": "system", "content": instrucciones}, {"role": "user", "content": pedido}]
    for turno in range(MAX_TURNOS + 1):
        if turno == MAX_TURNOS:
            print("\nSe alcanzaron las cuatro rondas. Completando los campos pendientes con supuestos.")
            mensajes.append({"role": "system", "content": (
                "Se alcanzó el límite de preguntas. Responde SOLO con JSON válido del esquema. "
                "Completa lo pendiente con supuestos razonables y registra cada uno en supuestos. "
                "No hagas más preguntas."
            )})
        respuesta = consultar_modelo(cliente, modelo, mensajes, modelos_alternativos)
        mensajes.append({"role": "assistant", "content": respuesta})
        if turno < MAX_TURNOS and son_preguntas(respuesta):
            print(f"\nPreguntas ({turno + 1}/{MAX_TURNOS}):\n{respuesta}")
            mensajes.append({"role": "user", "content": leer_texto("Tu respuesta (puedes responder por número): ")})
            continue

        # Una respuesta que incumple el formato tiene como máximo dos correcciones.
        for intento in range(MAX_REINTENTOS + 1):
            try:
                datos = validar_campos(extraer_json(respuesta))
                if datos["rol_id"] not in roles:
                    datos["rol_id"] = elegir_rol(roles)
                return datos
            except ValueError as error:
                if intento == MAX_REINTENTOS:
                    raise RuntimeError("El modelo no entregó campos válidos después de dos correcciones. Prueba otra sesión o modelo.") from None
                print(f"Respuesta inválida. Solicitando JSON válido ({intento + 1}/2).")
                mensajes.append({"role": "system", "content": (
                    f"Responde solo con JSON válido del esquema. Error: {error} "
                    "Completa la información pendiente con supuestos razonables y enuméralos en supuestos. "
                    "No hagas preguntas ni redactes el prompt final."
                )})
                respuesta = consultar_modelo(cliente, modelo, mensajes, modelos_alternativos)
                mensajes.append({"role": "assistant", "content": respuesta})
    raise RuntimeError("La entrevista terminó sin datos válidos.")


def copiar_portapapeles(texto: str) -> bool:
    """Usa herramientas nativas disponibles; si fallan, el prompt sigue en pantalla."""
    if sys.platform == "win32":
        candidatos = [("clip.exe", [])]
        contenido = texto.encode("utf-16")
    elif sys.platform == "darwin":
        candidatos = [("pbcopy", [])]
        contenido = texto.encode("utf-8")
    else:
        candidatos = [("wl-copy", []), ("xclip", ["-selection", "clipboard"]), ("xsel", ["--clipboard", "--input"])]
        contenido = texto.encode("utf-8")
    for herramienta, argumentos in candidatos:
        ejecutable = shutil.which(herramienta)
        if ejecutable:
            try:
                subprocess.run([ejecutable, *argumentos], input=contenido,
                               check=True, capture_output=True, timeout=10)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def guardar_prompt(texto: str, carpeta: Path = BASE_DIR / "prompts_generados") -> Path:
    """Escribe UTF-8 con fecha y microsegundos; modo x impide sobrescrituras."""
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"prompt_{datetime.now():%Y-%m-%d_%H-%M-%S_%f}.md"
    with ruta.open("x", encoding="utf-8") as archivo:
        archivo.write(texto)
    return ruta


def main() -> int:
    """Ejecuta una sesión. Ctrl+C o fin de entrada cancela sin traceback."""
    try:
        config = cargar_configuracion()
        roles = cargar_roles()
        print("Generador de prompts — la IA entrevista; una plantilla fija redacta.")
        pedido = leer_texto("\n¿Qué necesitas? ")
        modelo, motivo_modelo, alternativos = seleccionar_modelo(pedido, config)
        print(f"Modelo seleccionado: {modelo} ({motivo_modelo}).")
        # Desactivar los reintentos internos evita duplicar los del código.
        with OpenAI(base_url=config["base_url"], api_key=config["api_key"],
                    timeout=60, max_retries=0) as cliente:
            datos = entrevistar(cliente, modelo, roles, pedido, alternativos)
        prompt = generar_prompt(datos, roles[datos["rol_id"]])
        print("\n--- PROMPT FINAL ---\n\n" + prompt)
        if copiar_portapapeles(prompt):
            print("Prompt copiado al portapapeles.")
        else:
            print("No fue posible copiar al portapapeles; puedes copiar el texto de la pantalla.")
        if input("\n¿Guardar en prompts_generados/ como .md? [s/N]: ").strip().lower() in ("s", "si", "sí"):
            try:
                print(f"Guardado en: {guardar_prompt(prompt)}")
            except OSError:
                print("No se pudo guardar el archivo. Revisa permisos y espacio; el prompt sigue en pantalla.")
                return 1
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nSesión cancelada.")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
