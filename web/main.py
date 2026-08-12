"""Painel web do pipeline.

Reusa o motor da CLI (`gdv.sorteio`, `gdv.blocos`, `gdv.redator`) em vez de
reimplementar o sorteio: `hash_combinacao()` precisa dar o mesmo valor nos dois
lugares, senao a janela anti-repeticao de um nao enxerga o que o outro gerou.

O que o site NAO faz: montagem. ffmpeg nao roda em serverless — `gdv montar`
continua local.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gdv import blocos as mod_blocos
from gdv import briefing as mod_briefing
from gdv.catalogo import ErroCatalogo
from gdv.diagnostico import modulo_disponivel
from gdv.modelos import EIXOS, STATUS_PRODUTO, Produto
from gdv.redator import criar_redator
from gdv.sorteio import EspacoCombinatorioEsgotado, sortear_lote

from . import auth

RAIZ = Path(__file__).resolve().parents[1]
BLOCOS = RAIZ / "data" / "blocos.yaml"

ESTATICOS = Path(__file__).parent / "static"
MODELOS = Path(__file__).parent / "templates"

app = FastAPI(title="gdv")

# StaticFiles levanta RuntimeError na construcao se o diretorio nao existir, o
# que derrubaria o app inteiro no import por causa do CSS. Pagina sem estilo e
# melhor que 500; /saude denuncia se isso acontecer.
if ESTATICOS.is_dir():
    app.mount("/static", StaticFiles(directory=str(ESTATICOS)), name="static")

templates = Jinja2Templates(directory=str(MODELOS))

PUBLICAS = {"/login", "/static", "/favicon.ico", "/saude"}


@app.middleware("http")
async def exigir_sessao(request: Request, call_next):
    caminho = request.url.path
    if any(caminho.startswith(p) for p in PUBLICAS):
        return await call_next(request)

    catalogo = auth.catalogo_da_requisicao(request)
    if catalogo is None:
        return RedirectResponse("/login", status_code=303)

    # Guardado na requisicao: criar o cliente e refazer set_session em cada rota
    # dobraria a latencia de toda pagina.
    request.state.catalogo = catalogo
    return await call_next(request)


def catalogo_de(request: Request):
    return request.state.catalogo


def _pagina(request: Request, template: str, **contexto) -> HTMLResponse:
    return templates.TemplateResponse(request, template, contexto)


# ----------------------------------------------------------------- diagnostico

@app.get("/saude")
def saude():
    """Diz o que falta no deploy sem precisar de acesso aos logs.

    Publica de proposito: e a rota que serve justamente quando o login nao
    funciona. Reporta apenas booleanos e nomes — nunca o valor de uma variavel
    de ambiente.
    """
    return {
        "python": sys.version.split()[0],
        "modulos": {
            nome: modulo_disponivel(nome)
            for nome in ("gdv", "fastapi", "jinja2", "supabase", "google.genai", "yaml")
        },
        "arquivos": {
            "web/templates": MODELOS.is_dir(),
            "web/static": ESTATICOS.is_dir(),
            "data/blocos.yaml": BLOCOS.is_file(),
        },
        "variaveis_definidas": {
            nome: bool(os.environ.get(nome, "").strip())
            for nome in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "GEMINI_API_KEY")
        },
    }


# ------------------------------------------------------------------- sessao

@app.get("/login", response_class=HTMLResponse)
def form_login(request: Request):
    return _pagina(request, "login.html", erro=None)


@app.post("/login")
def fazer_login(request: Request, email: str = Form(...), senha: str = Form(...)):
    try:
        sessao = auth.entrar(email, senha)
    except auth.ErroAutenticacao as exc:
        return _pagina(request, "login.html", erro=str(exc))

    resposta = RedirectResponse("/", status_code=303)
    auth.gravar_cookies(resposta, sessao)
    return resposta


@app.post("/logout")
def logout():
    resposta = RedirectResponse("/login", status_code=303)
    auth.limpar_cookies(resposta)
    return resposta


# ------------------------------------------------------------------ briefing

@app.get("/", response_class=HTMLResponse)
def briefing_de_hoje(request: Request, data: str | None = None):
    catalogo = catalogo_de(request)
    dia = data or date.today().isoformat()

    videos = [v for v in catalogo.videos() if v.data == dia]
    produtos = {p.sku: p for p in catalogo.produtos()}

    return _pagina(
        request,
        "briefing.html",
        dia=dia,
        videos=videos,
        produtos=produtos,
        erro=request.query_params.get("erro"),
    )


@app.post("/briefing")
async def gerar_briefing(request: Request, qtd: int = Form(5)):
    catalogo = catalogo_de(request)
    dia = date.today().isoformat()

    if any(v.data == dia for v in catalogo.videos()):
        return RedirectResponse(
            "/?erro=Ja+existe+briefing+para+hoje.+Gere+amanha+ou+apague+os+de+hoje.",
            status_code=303,
        )

    try:
        matriz = mod_blocos.carregar(BLOCOS)
        produtos = mod_briefing.selecionar_produtos(
            catalogo.produtos_ativos(), catalogo.contagem_por_sku(), max(1, min(qtd, 20))
        )
        combinacoes = sortear_lote(
            produtos, matriz, catalogo.hashes_recentes(mod_briefing.JANELA_PADRAO), random.Random()
        )
    except (ValueError, EspacoCombinatorioEsgotado, mod_blocos.ErroBlocos) as exc:
        return RedirectResponse(f"/?erro={exc}", status_code=303)

    redator = criar_redator(matriz.termos_proibidos)

    # Sequencial, 5 chamadas ao Gemini estourariam a duracao da funcao. O Redator
    # e sincrono e continua assim; o paralelismo mora aqui.
    pacotes = await asyncio.gather(
        *(
            asyncio.to_thread(redator.redigir, produto, combinacao, dia)
            for produto, combinacao in zip(produtos, combinacoes)
        )
    )

    registros = [
        mod_briefing.registro_de(produto, combinacao, pacote, dia, "0")
        for produto, combinacao, pacote in zip(produtos, combinacoes, pacotes)
    ]

    try:
        catalogo.registrar(registros)
    except ErroCatalogo as exc:
        return RedirectResponse(f"/?erro={exc}", status_code=303)

    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------ catalogo

@app.get("/catalogo", response_class=HTMLResponse)
def listar_catalogo(request: Request):
    catalogo = catalogo_de(request)
    return _pagina(
        request,
        "catalogo.html",
        produtos=catalogo.produtos(),
        contagens=catalogo.contagem_por_sku(),
    )


@app.get("/produto/novo", response_class=HTMLResponse)
def form_novo_produto(request: Request):
    return _pagina(request, "produto.html", produto=None, erro=None, status_validos=STATUS_PRODUTO)


@app.get("/produto/{sku}", response_class=HTMLResponse)
def form_editar_produto(request: Request, sku: str):
    catalogo = catalogo_de(request)
    produto = catalogo.buscar_produto(sku)
    if produto is None:
        return RedirectResponse("/catalogo", status_code=303)

    return _pagina(
        request, "produto.html", produto=produto, erro=None, status_validos=STATUS_PRODUTO
    )


@app.post("/produto")
def salvar_produto(
    request: Request,
    sku: str = Form(...),
    nome: str = Form(...),
    categoria: str = Form(...),
    preco: str = Form("0"),
    margem: str = Form("0"),
    link_shop: str = Form(""),
    pasta_drive: str = Form(""),
    angulos: str = Form(""),
    status: str = Form("ativo"),
):
    catalogo = catalogo_de(request)

    def recusar(mensagem: str):
        return _pagina(
            request,
            "produto.html",
            produto=None,
            erro=mensagem,
            status_validos=STATUS_PRODUTO,
        )

    if status not in STATUS_PRODUTO:
        return recusar(f"status invalido: {status}")
    if not sku.strip() or not nome.strip() or not categoria.strip():
        return recusar("SKU, nome e categoria sao obrigatorios")

    try:
        produto = Produto(
            sku=sku.strip(),
            nome=nome.strip(),
            categoria=categoria.strip().lower(),
            preco=float(preco or 0),
            margem=float(margem or 0),
            link_shop=link_shop.strip(),
            pasta_drive=pasta_drive.strip(),
            angulos=[a.strip() for a in angulos.split(";") if a.strip()],
            status=status,
        )
    except ValueError:
        return recusar("preco e margem precisam ser numeros (use ponto decimal)")

    try:
        catalogo.salvar_produto(produto)
    except ErroCatalogo as exc:
        return recusar(str(exc))

    return RedirectResponse("/catalogo", status_code=303)


@app.get("/blocos", response_class=HTMLResponse)
def ver_blocos(request: Request):
    """So leitura: a matriz e versionada no repositorio, editada no blocos.yaml."""
    matriz = mod_blocos.carregar(BLOCOS)
    return _pagina(request, "blocos.html", matriz=matriz, eixos=EIXOS)
