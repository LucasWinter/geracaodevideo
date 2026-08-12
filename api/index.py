"""Entrypoint ASGI para a Vercel.

A Vercel serve funcoes a partir de /api. O pacote `gdv` vive em src/, entao
entra no sys.path antes do import do app — necessario quando o projeto nao foi
instalado no ambiente da funcao.

Se o import do painel falhar, este modulo NAO deixa a funcao morrer: serve um
app ASGI minimo que responde o diagnostico completo em JSON. Um crash de import
na Vercel vira `FUNCTION_INVOCATION_FAILED`, uma pagina que nao diz nada e
obriga a caçar log; assim a causa aparece no navegador.
"""

import json
import sys
import traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
for caminho in (RAIZ, RAIZ / "src"):
    if str(caminho) not in sys.path:
        sys.path.insert(0, str(caminho))


def _modulo_importa(nome: str) -> bool:
    # find_spec importa os pacotes-pai, entao 'google.genai' levanta quando
    # 'google' nao existe, em vez de devolver None.
    try:
        from importlib.util import find_spec

        return find_spec(nome) is not None
    except Exception:
        return False


def _diagnostico(erro: BaseException) -> dict:
    """So caminhos, nomes e booleanos — nunca o valor de variavel de ambiente."""
    try:
        conteudo_raiz = sorted(p.name for p in RAIZ.iterdir())
    except OSError:
        conteudo_raiz = []

    return {
        "erro": f"{type(erro).__name__}: {erro}",
        "traceback": traceback.format_exception(type(erro), erro, erro.__traceback__),
        "python": sys.version.split()[0],
        "raiz": str(RAIZ),
        "conteudo_da_raiz": conteudo_raiz,
        "arquivos": {
            "web/main.py": (RAIZ / "web" / "main.py").is_file(),
            "web/templates": (RAIZ / "web" / "templates").is_dir(),
            "web/static": (RAIZ / "web" / "static").is_dir(),
            "src/gdv/__init__.py": (RAIZ / "src" / "gdv" / "__init__.py").is_file(),
            "data/blocos.yaml": (RAIZ / "data" / "blocos.yaml").is_file(),
        },
        "modulos": {
            nome: _modulo_importa(nome)
            for nome in (
                "gdv", "web", "fastapi", "jinja2", "supabase",
                "google.genai", "yaml",
                # python-multipart: nome antigo e novo. FastAPI levanta no
                # import se faltar e alguma rota usa Form(...).
                "multipart", "python_multipart",
            )
        },
        "sys_path": sys.path,
        "o_que_fazer": (
            "Dependencia False (fastapi/jinja2/supabase/google.genai/multipart): ela precisa estar "
            "em [project].dependencies do pyproject.toml — a Vercel nao instala extras. "
            "Arquivo False: nao entrou no bundle da funcao; confira includeFiles no vercel.json."
        ),
    }


def criar_app_de_erro(detalhe: dict):
    """ASGI puro de proposito: nao depende de fastapi, que pode ser o ausente."""
    corpo = json.dumps(detalhe, indent=2, ensure_ascii=False).encode("utf-8")

    async def app_de_erro(scope, receive, send):
        if scope["type"] != "http":
            return

        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": corpo})

    return app_de_erro


try:
    from web.main import app
except Exception as exc:  # pragma: no cover - so ocorre em deploy quebrado
    app = criar_app_de_erro(_diagnostico(exc))


__all__ = ["app"]
