"""O entrypoint da Vercel precisa explicar a propria falha.

Um crash de import vira `FUNCTION_INVOCATION_FAILED`, uma pagina que nao diz
nada. Estes testes fixam o comportamento de fallback: responder o diagnostico
em JSON, no navegador, sem depender de fastapi.
"""

from __future__ import annotations

import asyncio
import json

from api.index import _diagnostico, criar_app_de_erro


def _chamar(app, caminho: str = "/saude") -> tuple[int, dict, bytes]:
    """Roda um app ASGI cru, sem TestClient — o fallback nao usa fastapi."""
    enviados: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(mensagem):
        enviados.append(mensagem)

    scope = {"type": "http", "method": "GET", "path": caminho, "headers": []}
    asyncio.run(app(scope, receive, send))

    inicio = next(m for m in enviados if m["type"] == "http.response.start")
    corpo = b"".join(m.get("body", b"") for m in enviados if m["type"] == "http.response.body")
    return inicio["status"], dict(inicio["headers"]), corpo


def test_app_normal_e_o_painel():
    """Com tudo instalado, o entrypoint serve o app de verdade."""
    from api.index import app

    assert app.title == "gdv"


def test_fallback_responde_500_com_json():
    app = criar_app_de_erro({"erro": "algo quebrou"})

    status, cabecalhos, corpo = _chamar(app)

    assert status == 500
    assert cabecalhos[b"content-type"].startswith(b"application/json")
    assert json.loads(corpo)["erro"] == "algo quebrou"


def test_fallback_responde_em_qualquer_rota():
    """Nao ha roteamento: qualquer URL que o usuario abrir mostra o diagnostico."""
    app = criar_app_de_erro({"erro": "x"})

    assert _chamar(app, "/")[0] == 500
    assert _chamar(app, "/qualquer/coisa")[0] == 500


def test_fallback_ignora_scope_nao_http():
    app = criar_app_de_erro({"erro": "x"})
    enviados = []

    async def send(mensagem):
        enviados.append(mensagem)

    async def receive():
        return {}

    asyncio.run(app({"type": "lifespan"}, receive, send))

    assert enviados == []


def test_diagnostico_aponta_dependencia_e_arquivo():
    try:
        raise ModuleNotFoundError("No module named 'fastapi'")
    except ModuleNotFoundError as exc:
        detalhe = _diagnostico(exc)

    assert "fastapi" in detalhe["erro"]
    assert detalhe["traceback"]
    assert detalhe["modulos"]["gdv"] is True
    assert detalhe["arquivos"]["web/main.py"] is True
    assert "pyproject.toml" in detalhe["o_que_fazer"]


def test_diagnostico_nao_vaza_variavel_de_ambiente(monkeypatch):
    monkeypatch.setenv("SUPABASE_ANON_KEY", "chave-secreta-nao-pode-vazar")

    detalhe = _diagnostico(RuntimeError("x"))

    assert "chave-secreta-nao-pode-vazar" not in json.dumps(detalhe)


def test_diagnostico_nao_explode_com_modulo_de_pai_ausente():
    detalhe = _diagnostico(RuntimeError("x"))

    assert detalhe["modulos"]["google.genai"] in (True, False)
