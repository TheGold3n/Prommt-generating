"""Redacción fija del prompt: este módulo no consulta a ningún modelo."""


def generar_prompt(datos: dict, rol: dict) -> str:
    """La misma estructura sirve para todos los roles del catálogo editable."""
    criterios = rol["criterios"] + datos.get("criterios_extra", [])
    texto_criterios = "\n".join(f"- {criterio}" for criterio in criterios)
    formato = datos.get("formato_salida", "").strip() or rol["formato_salida"]
    secciones = [
        f"## Rol\nActúa como {rol['descripcion']}.",
        f"## Tarea\n{datos['tarea']}",
        f"## Contexto\n{datos['contexto']}",
        f"## Criterios\n{texto_criterios}",
        f"## Formato de respuesta\n{formato}",
        f"## Material a analizar\n{datos['material']}",
    ]
    if datos.get("supuestos"):
        secciones.append("## Supuestos asumidos\n" + "\n".join(f"- {s}" for s in datos["supuestos"]))
    return "\n\n".join(secciones) + "\n"
