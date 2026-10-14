"""Pruebas locales: no requieren credenciales ni consumen cuota del endpoint."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    import httpx2 as httpx  # Transporte usado por las versiones actuales del SDK.
except ImportError:
    import httpx  # Compatibilidad con versiones anteriores de openai.
from openai import APIConnectionError, AuthenticationError, DefaultHttpxClient, OpenAI, RateLimitError

import main
from plantilla import generar_prompt


class GeneradorTests(unittest.TestCase):
    def setUp(self):
        self.roles = main.cargar_roles()
        self.datos = {
            "rol_id": "abogado_informatico",
            "tarea": "Revisar riesgos legales del proyecto",
            "material": "Repositorio Python con documentación",
            "contexto": "Chile; proyecto académico; revisión detallada",
            "criterios_extra": ["Privacidad de menores"],
            "formato_salida": "",
            "supuestos": [],
        }

    def cliente(self, *respuestas):
        # Se simula únicamente el límite externo; el bucle real sí se ejecuta.
        cliente = Mock()
        cliente.chat.completions.create.side_effect = [
            r if isinstance(r, Exception) else SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=r))]
            ) for r in respuestas
        ]
        return cliente

    def test_json_con_backticks_texto_y_llaves_en_cadenas(self):
        self.datos["material"] = 'Código con {llaves} y "comillas"'
        texto = "Aquí está:\n```json\n" + json.dumps(self.datos) + "\n```\nListo."
        self.assertEqual(main.extraer_json(texto), self.datos)
        with self.assertRaises(ValueError):
            main.extraer_json("{JSON roto}")

    def test_validacion_de_tipos_campos_y_opcionales(self):
        for cambios in ({"tarea": " "}, {"contexto": 5}, {"supuestos": "Chile"},
                        {"criterios_extra": [1]}, {"formato_salida": None},
                        {"prompt_final": "No permitido"}):
            with self.subTest(cambios=cambios), self.assertRaises(ValueError):
                main.validar_campos(dict(self.datos, **cambios))
        minimos = {k: self.datos[k] for k in ("rol_id", "tarea", "material", "contexto")}
        self.assertEqual(main.validar_campos(minimos)["supuestos"], [])
        with self.assertRaises(ValueError):
            main.validar_campos([self.datos])

    def test_plantilla_fija_criterios_formato_y_supuestos(self):
        texto = generar_prompt(self.datos, self.roles[self.datos["rol_id"]])
        secciones = ["## Rol", "## Tarea", "## Contexto", "## Criterios",
                     "## Formato de respuesta", "## Material a analizar"]
        self.assertEqual([texto.index(s) for s in secciones], sorted(texto.index(s) for s in secciones))
        self.assertIn("Privacidad de menores", texto)
        self.assertIn(self.roles["abogado_informatico"]["formato_salida"], texto)
        self.assertNotIn("Supuestos asumidos", texto)
        self.datos.update(formato_salida="Tabla breve", supuestos=["Uso académico"])
        texto = generar_prompt(self.datos, self.roles["abogado_informatico"])
        self.assertIn("## Formato de respuesta\nTabla breve", texto)
        self.assertTrue(texto.endswith("## Supuestos asumidos\n- Uso académico\n"))

    def test_roles_editables_sin_cambiar_codigo(self):
        rol = {"id": "nuevo", "descripcion": "Editor de textos", "criterios": ["Claridad"],
               "formato_salida": "Lista de cambios"}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as carpeta:
            ruta = Path(carpeta) / "roles.json"
            ruta.write_text(json.dumps([rol]), encoding="utf-8")
            catalogo = main.cargar_roles(ruta)
            self.assertIn("Editor de textos", generar_prompt(self.datos, catalogo["nuevo"]))
            ruta.write_text(json.dumps([rol, rol]), encoding="utf-8")
            with self.assertRaises(ValueError):
                main.cargar_roles(ruta)

    def test_configuracion_solo_desde_env_y_ruta_explicita(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as carpeta:
            ruta = Path(carpeta) / ".env"
            with self.assertRaises(ValueError):
                main.cargar_configuracion(ruta)
            ruta.write_text('base_url="https://endpoint.test/v1"\napi_key="secreto-prueba"\nmodelo=flash\n', encoding="utf-8")
            with patch.dict("os.environ", {"modelo": "otro"}):
                self.assertEqual(main.cargar_configuracion(ruta)["modelo"], "flash")
            ruta.write_text("base_url=ftp://endpoint.test\napi_key=prueba\nmodelo=flash", encoding="utf-8")
            with self.assertRaises(ValueError):
                main.cargar_configuracion(ruta)

    def test_entrevista_termina_en_una_llamada_si_ya_hay_datos(self):
        cliente = self.cliente(json.dumps(self.datos))
        with patch("builtins.input", side_effect=AssertionError("No debe preguntar")):
            self.assertEqual(main.entrevistar(cliente, "flash", self.roles, "Mi pedido"), self.datos)
        self.assertEqual(cliente.chat.completions.create.call_count, 1)
        mensaje = cliente.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("abogado_informatico", mensaje)
        self.assertIn('"tarea"', mensaje)

    def test_cuatro_rondas_y_cierre_forzado_con_supuestos(self):
        self.datos["supuestos"] = ["Profundidad media, sin preferencia del usuario"]
        cliente = self.cliente(*(["1. ¿Qué material analizarás?"] * 4), json.dumps(self.datos))
        with patch("builtins.input", return_value="no sé"), contextlib.redirect_stdout(io.StringIO()):
            resultado = main.entrevistar(cliente, "flash", self.roles, "Analiza mi proyecto")
        self.assertTrue(resultado["supuestos"])
        self.assertEqual(cliente.chat.completions.create.call_count, 5)
        mensajes = cliente.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("supuestos", mensajes[-2]["content"])
        self.assertEqual(mensajes[-2]["role"], "system")

    def test_solo_se_aceptan_hasta_cuatro_preguntas_numeradas(self):
        self.assertTrue(main.son_preguntas("1. ¿País?\n2) ¿Formato?"))
        for texto in ("Texto extra\n1. ¿País?", "", "1. ¿País?\n1. ¿Material?",
                      "\n".join(f"{i}. ¿Dato?" for i in range(1, 6))):
            self.assertFalse(main.son_preguntas(texto))

    def test_json_roto_se_repara_sin_preguntar_al_usuario(self):
        cliente = self.cliente('```json\n{"rol_id":', json.dumps(self.datos))
        with patch("builtins.input", side_effect=AssertionError("No debe preguntar")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.entrevistar(cliente, "flash", self.roles, "Pedido"), self.datos)
        self.assertEqual(cliente.chat.completions.create.call_count, 2)

    def test_json_invalido_no_supera_dos_reintentos(self):
        cliente = self.cliente("No es JSON", "Tampoco", "Nada")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            main.entrevistar(cliente, "flash", self.roles, "Pedido")
        self.assertEqual(cliente.chat.completions.create.call_count, 3)

    def test_rol_desconocido_se_elige_localmente(self):
        self.datos["rol_id"] = "inventado"
        cliente = self.cliente(json.dumps(self.datos))
        with patch("builtins.input", side_effect=["inexistente", "profesor_tutor"]), contextlib.redirect_stdout(io.StringIO()):
            resultado = main.entrevistar(cliente, "flash", self.roles, "Pedido")
        self.assertEqual(resultado["rol_id"], "profesor_tutor")
        self.assertEqual(cliente.chat.completions.create.call_count, 1)

    def test_red_y_rate_limit_tienen_espera_y_reintento_acotados(self):
        peticion = httpx.Request("POST", "https://endpoint.test/v1/chat/completions")
        limite = RateLimitError("límite", response=httpx.Response(429, request=peticion), body=None)
        cliente = self.cliente(APIConnectionError(request=peticion), limite, json.dumps(self.datos))
        with patch("main.time.sleep") as espera, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.consultar_modelo(cliente, "flash", []), json.dumps(self.datos))
        self.assertEqual([c.args[0] for c in espera.call_args_list], [2, 4])
        cliente = self.cliente(limite, limite, limite)
        with patch("main.time.sleep"), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            main.consultar_modelo(cliente, "flash", [])
        self.assertEqual(cliente.chat.completions.create.call_count, 3)

    def test_clave_invalida_no_se_reintenta_ni_se_expone(self):
        peticion = httpx.Request("POST", "https://endpoint.test/v1/chat/completions")
        error = AuthenticationError("SECRETO", response=httpx.Response(401, request=peticion), body=None)
        cliente = self.cliente(error)
        with self.assertRaises(RuntimeError) as resultado:
            main.consultar_modelo(cliente, "flash", [])
        self.assertNotIn("SECRETO", str(resultado.exception))
        self.assertEqual(cliente.chat.completions.create.call_count, 1)

    def test_guardado_utf8_fechado_sin_sobrescribir(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as carpeta:
            destino = Path(carpeta) / "prompts_generados"
            primero = main.guardar_prompt("Revisión jurídica", destino)
            segundo = main.guardar_prompt("Otro", destino)
            self.assertNotEqual(primero, segundo)
            self.assertRegex(primero.name, r"^prompt_\d{4}-\d{2}-\d{2}_.*\.md$")
            self.assertEqual(primero.read_text(encoding="utf-8"), "Revisión jurídica")

    def test_portapapeles_opcional_y_windows_unicode(self):
        with patch("main.shutil.which", return_value=None):
            self.assertFalse(main.copiar_portapapeles("Texto"))
        with patch("main.sys.platform", "win32"), patch("main.shutil.which", return_value="clip.exe"), patch("main.subprocess.run") as ejecutar:
            self.assertTrue(main.copiar_portapapeles("Revisión jurídica"))
            self.assertIn("Revisión jurídica", ejecutar.call_args.kwargs["input"].decode("utf-16"))

    def test_sdk_real_serializa_y_procesa_el_endpoint_compatible(self):
        recibidos = []

        def endpoint(peticion):
            recibidos.append(json.loads(peticion.content))
            self.assertEqual(peticion.url.path, "/v1/chat/completions")
            return httpx.Response(200, json={
                "id": "prueba", "object": "chat.completion", "created": 0,
                "model": "flash-prueba", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": json.dumps(self.datos)},
                    "finish_reason": "stop"}],
            })

        # El SDK es real; solo se sustituye el transporte, sin abrir conexiones.
        transporte = httpx.MockTransport(endpoint)
        with OpenAI(base_url="https://endpoint.test/v1", api_key="clave-ficticia",
                    max_retries=0, http_client=DefaultHttpxClient(transport=transporte)) as cliente:
            datos = main.entrevistar(cliente, "flash-prueba", self.roles, "Revisa el proyecto")
        self.assertEqual(datos, self.datos)
        self.assertEqual(len(recibidos), 1)
        self.assertEqual(recibidos[0]["model"], "flash-prueba")

    def test_flujo_completo_terminal_y_guardado(self):
        cliente = self.cliente("1. ¿País y material?", json.dumps(self.datos))
        cliente.__enter__ = Mock(return_value=cliente)
        cliente.__exit__ = Mock(return_value=False)
        salida = io.StringIO()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as carpeta:
            guardar_real = main.guardar_prompt
            with patch("main.cargar_configuracion", return_value={"base_url": "https://endpoint.test/v1", "api_key": "ficticia", "modelo": "flash"}), \
                 patch("main.OpenAI", return_value=cliente), \
                 patch("builtins.input", side_effect=["Analiza mi proyecto", "Chile, repositorio Python", "s"]), \
                 patch("main.copiar_portapapeles", return_value=False), \
                 patch("main.guardar_prompt", side_effect=lambda texto: guardar_real(texto, Path(carpeta))), \
                 contextlib.redirect_stdout(salida):
                self.assertEqual(main.main(), 0)
            archivos = list(Path(carpeta).glob("*.md"))
            self.assertEqual(len(archivos), 1)
            self.assertIn("## Material a analizar", archivos[0].read_text(encoding="utf-8"))
        self.assertIn("PROMPT FINAL", salida.getvalue())
        self.assertIn("Guardado en:", salida.getvalue())
        self.assertEqual(cliente.chat.completions.create.call_count, 2)

    def test_quinta_respuesta_no_puede_iniciar_otra_ronda(self):
        cliente = self.cliente(*(["1. ¿Material?"] * 5), json.dumps(self.datos))
        with patch("builtins.input", return_value="da igual") as entrada, contextlib.redirect_stdout(io.StringIO()):
            main.entrevistar(cliente, "flash", self.roles, "Pedido")
        self.assertEqual(entrada.call_count, 4)
        self.assertEqual(cliente.chat.completions.create.call_count, 6)

    def test_fallo_portapapeles_no_interrumpe_el_programa(self):
        with patch("main.shutil.which", return_value="herramienta"), \
             patch("main.subprocess.run", side_effect=OSError("No disponible")):
            self.assertFalse(main.copiar_portapapeles("Texto"))


if __name__ == "__main__":
    unittest.main()
