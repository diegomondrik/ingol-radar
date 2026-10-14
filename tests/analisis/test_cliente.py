"""El cliente de claude: el comando exacto, lo que va por stdin, el entorno, y qué salidas se aceptan."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from radar.analisis import analizador, cliente
from radar.analisis.errores import ClaudeNoDisponible, ErrorClaude

from .ayudas import CONTEXTO, datos_del_usuario, item_valido, novedad

SISTEMA = 'Sos un analista.\nSegunda línea con "comillas" & % ^ y acentos.'
ESQUEMA = {"type": "object", "properties": {"a": {"type": "string"}}}
EXE = "C:/falso/claude.exe"
EXE_REAL = __file__  # un archivo que existe: `localizar_claude` sólo comprueba eso
ENTORNO_MINIMO = {"RADAR_CLAUDE": __file__, "PATH": "/bin", "USERPROFILE": "C:/Users/x", "SYSTEMROOT": "C:/Windows"}


def salida_ok(**cambios) -> bytes:
    datos = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "permission_denials": [],
        "num_turns": 2,
        "structured_output": {"analisis": []},
        "result": "{}",
    }
    datos.update(cambios)
    return json.dumps(datos).encode("utf-8")


class RunFalso:
    """Reemplaza a subprocess.run: guarda cómo lo llamaron y devuelve lo que se le indique."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, error=None):
        self.stdout, self.stderr, self.returncode, self.error = stdout, stderr, returncode, error
        self.args = None
        self.kwargs = None
        self.contenido_del_cwd = None
        self.cwd_existia = None

    def __call__(self, comando, **kwargs):
        self.args, self.kwargs = comando, kwargs
        cwd = kwargs.get("cwd")
        self.cwd_existia = cwd is not None and os.path.isdir(cwd)
        self.contenido_del_cwd = os.listdir(cwd) if self.cwd_existia else None
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(comando, self.returncode, self.stdout, self.stderr)


def ejecutar(run, usuario="datos", entorno=None, **kwargs):
    entorno = dict(ENTORNO_MINIMO) if entorno is None else entorno
    return cliente.ejecutar(usuario, SISTEMA, ESQUEMA, run=run, entorno=entorno, **kwargs)


class TestComando(unittest.TestCase):
    def test_la_lista_exacta(self):
        # Cualquier bandera de más (--dangerously-skip-permissions, --allowedTools, --permission-mode,
        # --add-dir) o un argumento posicional tiene que poner esto en rojo.
        esperado = [
            EXE,
            "-p",
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--system-prompt",
            SISTEMA,
            "--output-format",
            "json",
            "--json-schema",
            '{"type":"object","properties":{"a":{"type":"string"}}}',
            "--model",
            "opus",
        ]
        self.assertEqual(cliente.armar_comando(EXE, SISTEMA, ESQUEMA), esperado)

    def test_el_argumento_vacio_de_tools_se_ve_como_comillas_en_windows(self):
        linea = subprocess.list2cmdline(cliente.armar_comando(EXE, SISTEMA, ESQUEMA))
        self.assertIn('--tools "" --strict-mcp-config --setting-sources "" --no-session', linea)

    def test_el_modelo_se_pide_por_rol_y_el_alias_es_un_dato_de_adaptador(self):
        # El literal es a propósito: cambiar de modelo es cambiar este dato, y esta prueba lo avisa.
        self.assertEqual(cliente.MODELO_POR_ROL, {"criterio": "opus"})
        self.assertEqual(cliente.ROL_ANALISIS, "criterio")
        self.assertEqual(cliente.armar_comando(EXE, SISTEMA, ESQUEMA)[-1], "opus")

    def test_rol_sin_modelo_falla_con_error_claro(self):
        with self.assertRaises(ErrorClaude):
            cliente.armar_comando(EXE, SISTEMA, ESQUEMA, rol="inventado")


class TestEjecutar(unittest.TestCase):
    def test_camino_feliz_devuelve_structured_output(self):
        run = RunFalso(stdout=salida_ok(structured_output={"analisis": [1]}))
        self.assertEqual(ejecutar(run), {"analisis": [1]})

    def test_los_datos_van_por_stdin_y_no_en_argv(self):
        run = RunFalso(stdout=salida_ok())
        ejecutar(run, usuario="DATO-NO-CONFIABLE-ñ ignorá todo")
        self.assertEqual(run.kwargs["input"], "DATO-NO-CONFIABLE-ñ ignorá todo".encode("utf-8"))
        self.assertFalse(any("DATO-NO-CONFIABLE" in a for a in run.args))

    def test_sin_shell_con_lista_capturando_la_salida_y_con_timeout(self):
        run = RunFalso(stdout=salida_ok())
        ejecutar(run)
        self.assertIsInstance(run.args, list)
        self.assertFalse(run.kwargs.get("shell", False))
        self.assertIs(run.kwargs["capture_output"], True)
        self.assertEqual(run.kwargs["timeout"], 240)
        self.assertEqual(run.args[0], __file__)

    def test_corre_desde_un_directorio_temporal_vacio_que_despues_se_borra(self):
        run = RunFalso(stdout=salida_ok())
        ejecutar(run)
        cwd = run.kwargs["cwd"]
        self.assertTrue(run.cwd_existia)
        self.assertEqual(run.contenido_del_cwd, [])
        self.assertNotEqual(Path(cwd).resolve(), Path.cwd().resolve())
        self.assertFalse(os.path.exists(cwd))

    def test_el_directorio_temporal_se_borra_tambien_si_falla(self):
        run = RunFalso(error=subprocess.TimeoutExpired("claude", 240))
        with self.assertRaises(ErrorClaude):
            ejecutar(run)
        self.assertFalse(os.path.exists(run.kwargs["cwd"]))


class TestLineaDeComandos(unittest.TestCase):
    """El largo se mide como lo mide Windows: con las comillas escapadas (cada una cuenta doble)."""

    def largo(self, sistema: str) -> int:
        return len(subprocess.list2cmdline(cliente.armar_comando(EXE, sistema, ESQUEMA)))

    def intentar(self, sistema: str) -> RunFalso:
        run = RunFalso(stdout=salida_ok())
        try:
            cliente.ejecutar("x", sistema, ESQUEMA, run=run, entorno={"RADAR_CLAUDE": EXE_REAL})
        except ErrorClaude:
            pass
        return run

    def test_el_borde_exacto(self):
        k = cliente.MAX_LINEA_DE_COMANDOS - self.largo("") + 2  # el argumento vacío cuenta como "" (2 caracteres)
        justo = "a" * k
        self.assertEqual(self.largo(justo), cliente.MAX_LINEA_DE_COMANDOS)
        self.assertEqual(self.largo(justo + "a"), cliente.MAX_LINEA_DE_COMANDOS + 1)

    def test_en_el_borde_corre_y_un_caracter_mas_no(self):
        # El exe de la prueba es otro que EXE: se mide con el mismo.
        exe_cmd = len(subprocess.list2cmdline([EXE_REAL])) - len(subprocess.list2cmdline([EXE]))
        k = cliente.MAX_LINEA_DE_COMANDOS - self.largo("") + 2 - exe_cmd
        self.assertIsNotNone(self.intentar("a" * k).args)
        con_uno_mas = self.intentar("a" * (k + 1))
        self.assertIsNone(con_uno_mas.args)

    def test_un_contexto_lleno_de_comillas_pasa_por_largo_crudo_pero_no_escapado(self):
        sistema = ' "' * 12_000  # 24 000 caracteres crudos, 36 000 ya escapados
        self.assertLess(len(sistema), cliente.MAX_LINEA_DE_COMANDOS)
        self.assertGreater(self.largo(sistema), 32_767)
        self.assertIsNone(self.intentar(sistema).args)

    def test_el_mensaje_dice_que_es_demasiado_largo(self):
        with self.assertRaises(ErrorClaude) as ctx:
            cliente.ejecutar("x", ' "' * 12_000, ESQUEMA, run=RunFalso(), entorno={"RADAR_CLAUDE": EXE_REAL})
        self.assertIn("demasiado largo", str(ctx.exception))

    def test_nunca_se_acepta_una_linea_que_windows_no_aguanta(self):
        for n in (1, 5000, 9000, 11_000, 14_000):
            run = self.intentar(' "' * n)
            if run.args is not None:
                self.assertLessEqual(len(subprocess.list2cmdline(run.args)), 32_767, n)


class TestEntorno(unittest.TestCase):
    def entorno(self, **extra):
        e = dict(ENTORNO_MINIMO)
        e.update(extra)
        return e

    def test_variables_que_cambiarian_quien_paga_o_a_donde_va_el_prompt_cortan_antes_de_correr(self):
        casos = {
            "ANTHROPIC_API_KEY": "VALOR-SECRETO-9",
            "ANTHROPIC_AUTH_TOKEN": "VALOR-SECRETO-9",
            "ANTHROPIC_BASE_URL": "https://VALOR-SECRETO-9.example",
            "CLAUDE_CODE_USE_BEDROCK": "VALOR-SECRETO-9",
            "CLAUDE_CODE_USE_VERTEX": "VALOR-SECRETO-9",
            "CLAUDE_CODE_USE_FOUNDRY": "VALOR-SECRETO-9",
            "anthropic_api_key": "VALOR-SECRETO-9",  # Windows no distingue mayúsculas
        }
        for nombre, valor in casos.items():
            with self.subTest(nombre):
                run = RunFalso(stdout=salida_ok())
                with self.assertRaises(ClaudeNoDisponible) as ctx:
                    ejecutar(run, entorno=self.entorno(**{nombre: valor}))
                self.assertIsNone(run.args)
                self.assertIn(nombre.upper(), str(ctx.exception).upper())
                self.assertIn("suscripción", str(ctx.exception))
                self.assertNotIn(valor, str(ctx.exception))  # el valor (una clave) no se muestra

    def test_el_mensaje_de_la_clave_es_en_castellano_llano(self):
        with self.assertRaises(ClaudeNoDisponible) as ctx:
            ejecutar(RunFalso(), entorno=self.entorno(ANTHROPIC_API_KEY="x"))
        self.assertIn("hay una clave de API en el entorno", str(ctx.exception))
        self.assertIn("el radar usa tu suscripción y no corre así; sacala o corré en otra terminal", str(ctx.exception))

    def test_las_vacias_no_cortan_pero_tampoco_pasan(self):
        run = RunFalso(stdout=salida_ok())
        ejecutar(run, entorno=self.entorno(ANTHROPIC_API_KEY="", ANTHROPIC_BASE_URL="   ", CLAUDE_CODE_USE_BEDROCK=""))
        env = run.kwargs["env"]
        for nombre in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK"):
            self.assertNotIn(nombre, env)

    def test_el_subproceso_recibe_un_entorno_explicito_sin_lo_que_cambia_el_proveedor(self):
        run = RunFalso(stdout=salida_ok())
        sucio = self.entorno(
            ANTHROPIC_MODEL="x",
            ANTHROPIC_DEFAULT_OPUS_MODEL="x",
            ANTHROPIC_VERTEX_PROJECT_ID="x",
            AWS_ACCESS_KEY_ID="x",
            AWS_SECRET_ACCESS_KEY="x",
            AWS_PROFILE="x",
            GOOGLE_APPLICATION_CREDENTIALS="C:/clave.json",
            HOME="/home/x",
            APPDATA="C:/a",
            LOCALAPPDATA="C:/l",
            TEMP="C:/t",
            TMP="C:/t",
            CLAUDE_CODE_OAUTH_TOKEN="de-suscripcion",
        )
        ejecutar(run, entorno=sucio)
        env = run.kwargs["env"]
        self.assertIsNot(env, sucio)
        for nombre in env:
            self.assertFalse(nombre.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_")), nombre)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        for necesaria in ("PATH", "USERPROFILE", "SYSTEMROOT", "HOME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
            self.assertEqual(env[necesaria], sucio[necesaria], necesaria)
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "de-suscripcion")  # sesión de suscripción: se mantiene

    def test_el_entorno_de_quien_llama_no_se_modifica(self):
        sucio = self.entorno(ANTHROPIC_MODEL="x", AWS_PROFILE="x")
        copia = dict(sucio)
        ejecutar(RunFalso(stdout=salida_ok()), entorno=sucio)
        self.assertEqual(sucio, copia)

    def test_sin_entorno_indicado_se_usa_el_del_proceso_y_tambien_se_revisa(self):
        anterior = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "sk-de-prueba"
        try:
            with self.assertRaises(ClaudeNoDisponible):
                cliente.ejecutar("x", SISTEMA, ESQUEMA, run=RunFalso(stdout=salida_ok()))
        finally:
            if anterior is None:
                del os.environ["ANTHROPIC_API_KEY"]
            else:
                os.environ["ANTHROPIC_API_KEY"] = anterior

    def test_la_corrida_entera_se_corta_si_hay_clave_de_api(self):
        novs = [novedad(i) for i in range(1, 8)]
        run = RunFalso(stdout=salida_ok())

        def runner(**kw):
            return cliente.ejecutar(run=run, entorno=self.entorno(ANTHROPIC_API_KEY="x"), **kw)

        r = analizador.analizar(novs, runner=runner, contexto=CONTEXTO)
        self.assertIsNone(run.args)
        self.assertIn("suscripción", r.abortada)
        self.assertEqual(set(r.sin_analizar), {n.clave for n in novs})


class TestSalidasRechazadas(unittest.TestCase):
    def rechaza(self, run, contiene=None):
        with self.assertRaises(ErrorClaude) as ctx:
            ejecutar(run)
        # Una falla de ESTA llamada no es "claude no disponible": esa corta toda la corrida.
        self.assertNotIsInstance(ctx.exception, ClaudeNoDisponible)
        if contiene:
            self.assertIn(contiene, str(ctx.exception))

    def test_codigo_de_salida_distinto_de_cero(self):
        self.rechaza(RunFalso(stdout=salida_ok(), stderr=b"se rompio", returncode=1), "código 1")

    def test_el_motivo_del_stderr_se_limpia(self):
        run = RunFalso(stderr=b"\x1b[31mfallo\x1b[0m <script>x</script> " + b"a" * 5000, returncode=2)
        with self.assertRaises(ErrorClaude) as ctx:
            ejecutar(run)
        self.assertLess(len(str(ctx.exception)), 500)
        self.assertNotIn("\x1b", str(ctx.exception))
        self.assertNotIn("<script", str(ctx.exception))

    def test_json_roto(self):
        self.rechaza(RunFalso(stdout=b'{"is_error": false, '), "JSON")

    def test_no_es_utf8(self):
        self.rechaza(RunFalso(stdout=b"\xff\xfe\x00{"), "JSON")

    def test_vacia(self):
        self.rechaza(RunFalso(stdout=b""), "JSON")

    def test_json_que_no_es_un_objeto(self):
        self.rechaza(RunFalso(stdout=b"[1, 2]"), "objeto")

    def test_anidamiento_profundo_no_rompe_con_otro_error(self):
        self.rechaza(RunFalso(stdout=b"[" * 100_000 + b"]" * 100_000), "JSON")

    def test_is_error_true(self):
        self.rechaza(RunFalso(stdout=salida_ok(is_error=True)), "is_error")

    def test_is_error_ausente(self):
        datos = json.loads(salida_ok())
        del datos["is_error"]
        self.rechaza(RunFalso(stdout=json.dumps(datos).encode()), "is_error")

    def test_is_error_que_no_es_booleano_falso(self):
        self.rechaza(RunFalso(stdout=salida_ok(is_error=0)), "is_error")

    def test_subtype_que_no_es_success(self):
        self.rechaza(RunFalso(stdout=salida_ok(subtype="error_max_turns")), "subtype")

    def test_permission_denials_no_vacio(self):
        self.rechaza(RunFalso(stdout=salida_ok(permission_denials=[{"tool_name": "Read"}])), "herramienta")

    def test_permission_denials_ausente(self):
        datos = json.loads(salida_ok())
        del datos["permission_denials"]
        self.rechaza(RunFalso(stdout=json.dumps(datos).encode()), "herramienta")

    def test_structured_output_ausente(self):
        datos = json.loads(salida_ok())
        del datos["structured_output"]
        self.rechaza(RunFalso(stdout=json.dumps(datos).encode()), "structured_output")

    def test_structured_output_que_no_es_objeto(self):
        self.rechaza(RunFalso(stdout=salida_ok(structured_output="texto")), "structured_output")
        self.rechaza(RunFalso(stdout=salida_ok(structured_output=None)), "structured_output")

    def test_el_texto_de_result_no_reemplaza_a_structured_output(self):
        datos = json.loads(salida_ok(result='{"analisis": []}'))
        del datos["structured_output"]
        self.rechaza(RunFalso(stdout=json.dumps(datos).encode()), "structured_output")

    def test_timeout(self):
        self.rechaza(RunFalso(error=subprocess.TimeoutExpired("claude", 240)), "240")

    def test_no_se_pudo_lanzar(self):
        self.rechaza(RunFalso(error=PermissionError("acceso denegado")), "no se pudo correr")

    def test_salida_gigante(self):
        self.rechaza(RunFalso(stdout=b" " * (cliente.MAX_SALIDA + 1)), "pesa más")


class TestCadena(unittest.TestCase):
    """cliente.ejecutar -> analizar, con un `run` falso: lo que falla en una llamada se reintenta."""

    def runner(self, run):
        return lambda **kw: cliente.ejecutar(run=run, entorno=dict(ENTORNO_MINIMO), **kw)

    def test_un_timeout_en_el_lote_se_reintenta_de_a_una_y_se_recupera(self):
        llamadas = []

        def run(comando, **kwargs):
            llamadas.append(kwargs["input"].decode("utf-8"))
            if len(llamadas) == 1:
                raise subprocess.TimeoutExpired(comando, kwargs["timeout"])
            items = datos_del_usuario(llamadas[-1])
            ok = {"analisis": [item_valido(p["clave"]) for p in items]}
            return subprocess.CompletedProcess(comando, 0, salida_ok(structured_output=ok), b"")

        novs = [novedad(1), novedad(2)]
        r = analizador.analizar(novs, runner=self.runner(run), contexto=CONTEXTO)
        self.assertEqual(set(r.analizadas), {"f:1", "f:2"})
        self.assertEqual((r.llamadas, r.reintentos), (3, 2))
        self.assertIsNone(r.abortada)
        self.assertEqual(len(datos_del_usuario(llamadas[0])), 2)
        self.assertEqual([len(datos_del_usuario(u)) for u in llamadas[1:]], [1, 1])

    def test_timeouts_siempre_dejan_cada_novedad_sin_analizar_con_el_motivo(self):
        def run(comando, **kwargs):
            raise subprocess.TimeoutExpired(comando, kwargs["timeout"])

        novs = [novedad(1), novedad(2)]
        r = analizador.analizar(novs, runner=self.runner(run), contexto=CONTEXTO)
        self.assertEqual(set(r.sin_analizar), {"f:1", "f:2"})
        self.assertTrue(all("240" in m for m in r.sin_analizar.values()))
        self.assertIsNone(r.abortada)  # un timeout no es "claude no disponible"

    def test_json_roto_o_codigo_distinto_de_cero_tampoco_cortan_la_corrida(self):
        for stdout, rc in ((b"{roto", 0), (salida_ok(), 3)):
            with self.subTest(rc=rc):
                run = RunFalso(stdout=stdout, returncode=rc)
                r = analizador.analizar([novedad(1)], runner=self.runner(run), contexto=CONTEXTO)
                self.assertEqual(set(r.sin_analizar), {"f:1"})
                self.assertIsNone(r.abortada)


# ------------------------------------------------------------------ un claude falso, de verdad

ECO = r'''
import json, os, sys
datos = sys.stdin.buffer.read().decode("utf-8")
sys.stdout.write(json.dumps({
    "type": "result", "subtype": "success", "is_error": False, "permission_denials": [],
    "structured_output": {
        "argv": sys.argv[1:],
        "stdin": datos,
        "cwd_vacio": os.listdir(os.getcwd()) == [],
        "entorno": sorted(os.environ),
    },
}))
'''


class TestClaudeFalsoDeVerdad(unittest.TestCase):
    """Corre un proceso de verdad (un script de Python en el lugar de claude) para ver qué llega: los
    argumentos pasan por la línea de comandos real del sistema, con sus reglas de comillas."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.script = Path(cls.tmp.name) / "eco.py"
        cls.script.write_text(ECO, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def correr(self, usuario, sistema, entorno=None):
        vistos = []

        def run(comando, **kwargs):
            vistos.append((comando, kwargs))
            return subprocess.run([sys.executable, "-I", str(self.script), *comando[1:]], **kwargs)

        base = {k: v for k, v in os.environ.items() if not k.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))}
        base["RADAR_CLAUDE"] = __file__
        base.update(entorno or {})
        eco = cliente.ejecutar(usuario, sistema, ESQUEMA, run=run, entorno=base)
        return eco, vistos[0]

    def test_argumentos_y_stdin_llegan_identicos(self):
        sistema = (
            'comillas "dobles" y \'simples\' & | < > ^ % PATH % %% ! ; $HOME `x` \\ \\\\ \\" fin\\\n'
            "salto de línea y\ttab, acentos áéíóú ñ ü ¿¡ — « » 日本語 😀\n"
            "\u202e bidi  espacios   dobles \\"
        )
        usuario = 'línea 1\r\nlínea 2 "x" <<<FIN>>> %PATH% ñ 日本 😀\n\n  sangría\\'
        eco, (comando, kwargs) = self.correr(usuario, sistema)
        self.assertEqual(eco["stdin"], usuario)
        self.assertEqual(eco["argv"], comando[1:])
        self.assertEqual(eco["argv"][eco["argv"].index("--system-prompt") + 1], sistema)
        self.assertIs(kwargs["capture_output"], True)

    def test_el_argumento_vacio_de_tools_y_de_setting_sources_llega_vacio(self):
        eco, _ = self.correr("x", "s")
        argv = eco["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertEqual(argv[:1], ["-p"])
        self.assertEqual(argv.count("--tools"), 1)

    def test_el_esquema_llega_como_json_igual(self):
        eco, _ = self.correr("x", "s")
        argv = eco["argv"]
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), ESQUEMA)

    def test_arranca_en_un_directorio_vacio_y_sin_variables_de_proveedor(self):
        eco, _ = self.correr(
            "x", "s", entorno={"ANTHROPIC_MODEL": "m", "AWS_PROFILE": "p", "GOOGLE_APPLICATION_CREDENTIALS": "c"}
        )
        self.assertTrue(eco["cwd_vacio"])
        for nombre in eco["entorno"]:
            self.assertFalse(nombre.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_")), nombre)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", eco["entorno"])
        self.assertIn("PATH", {n.upper() for n in eco["entorno"]})


class TestLocalizar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def archivo(self, *partes) -> Path:
        ruta = self.dir.joinpath(*partes)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(b"x")
        return ruta

    def test_gana_la_variable_de_entorno(self):
        exe = self.archivo("a", "claude.exe")
        r = cliente.localizar_claude({"RADAR_CLAUDE": str(exe)}, which=lambda n: "/otro/claude", home=self.dir)
        self.assertEqual(r, str(exe))

    def test_la_variable_que_apunta_a_nada_es_un_error_claro(self):
        with self.assertRaises(ClaudeNoDisponible) as ctx:
            cliente.localizar_claude({"RADAR_CLAUDE": str(self.dir / "no-existe.exe")}, which=lambda n: None, home=self.dir)
        self.assertIn("RADAR_CLAUDE", str(ctx.exception))

    def test_despues_el_path(self):
        exe = self.archivo("bin", "claude.exe")
        r = cliente.localizar_claude({}, which=lambda n: str(exe), home=self.dir)
        self.assertEqual(r, str(exe))

    def test_despues_local_bin(self):
        exe = self.archivo(".local", "bin", "claude.exe")
        r = cliente.localizar_claude({}, which=lambda n: None, home=self.dir)
        self.assertEqual(r, str(exe))

    def test_local_bin_sin_extension(self):
        exe = self.archivo(".local", "bin", "claude")
        r = cliente.localizar_claude({}, which=lambda n: None, home=self.dir)
        self.assertEqual(r, str(exe))

    def test_no_esta_error_claro_que_dice_donde_ponerlo(self):
        with self.assertRaises(ClaudeNoDisponible) as ctx:
            cliente.localizar_claude({}, which=lambda n: None, home=self.dir)
        mensaje = str(ctx.exception)
        self.assertIn("RADAR_CLAUDE", mensaje)
        self.assertIn(".local", mensaje)

    def test_ejecutar_sin_claude_lanza_claude_no_disponible_sin_correr_nada(self):
        run = RunFalso(stdout=salida_ok())
        with self.assertRaises(ClaudeNoDisponible):
            cliente.ejecutar("x", SISTEMA, ESQUEMA, run=run, entorno={"RADAR_CLAUDE": str(self.dir / "no.exe")})
        self.assertIsNone(run.args)

    def test_un_cmd_o_bat_se_rechaza(self):
        for nombre in ("claude.cmd", "claude.BAT"):
            exe = self.archivo("npm", nombre)
            with self.assertRaises(ClaudeNoDisponible, msg=nombre) as ctx:
                cliente.localizar_claude({"RADAR_CLAUDE": str(exe)}, which=lambda n: None, home=self.dir)
            self.assertIn("claude.exe", str(ctx.exception))
        with self.assertRaises(ClaudeNoDisponible):
            cliente.localizar_claude({}, which=lambda n: str(self.dir / "npm" / "claude.cmd"), home=self.dir)

    def test_un_claude_suelto_en_el_directorio_actual_no_se_toma(self):
        suelto = self.archivo("trabajo", "claude.exe")
        real = self.archivo(".local", "bin", "claude.exe")
        anterior = os.getcwd()
        os.chdir(suelto.parent)
        try:
            r = cliente.localizar_claude({}, which=lambda n: str(suelto), home=self.dir)
        finally:
            os.chdir(anterior)
        self.assertEqual(r, str(real))


if __name__ == "__main__":
    unittest.main()
