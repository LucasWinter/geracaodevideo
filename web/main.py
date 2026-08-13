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
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gdv import blocos as mod_blocos
from gdv import briefing as mod_briefing
from gdv.catalogo import ErroCatalogo
from gdv.diagnostico import modulo_disponivel
from gdv.modelos import ANGULOS_SUGERIDOS, EIXOS, STATUS_PRODUTO, Produto
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


# ------------------------------------------------------- opcoes dos campos fixos

# Valor sentinela do <select> de categoria: escolher "outra" revela o campo de
# texto. Nao pode colidir com categoria de verdade, dai os underscores.
OUTRA_CATEGORIA = "__outra__"


def opcoes_de_categoria(catalogo) -> list[str]:
    """Categorias da matriz mais as que ja existem no catalogo.

    A matriz manda: valor de bloco com `categorias` so entra no sorteio se casar
    exatamente com a categoria do produto. As do catalogo entram junto para que
    editar um produto antigo nao perca a categoria dele so por ela nao estar no
    blocos.yaml.
    """
    try:
        conhecidas = set(mod_blocos.carregar(BLOCOS).categorias())
    except (mod_blocos.ErroBlocos, OSError):
        # Matriz quebrada nao pode travar o cadastro de produto — /blocos e
        # /briefing ja reportam esse erro com mensagem propria.
        conhecidas = set()

    conhecidas.update(p.categoria for p in catalogo.produtos() if p.categoria)
    return sorted(conhecidas)


def normalizar_angulos(marcados: list[str], livres: str) -> list[str]:
    """Junta as caixas marcadas com o campo livre, sem repetir.

    Cada entrada ainda e quebrada por ponto e virgula: e o formato que a CLI
    grava no CSV, entao um valor colado da mao continua sendo aceito.
    """
    vistos: set[str] = set()
    saida: list[str] = []
    for bruto in [*marcados, livres]:
        for parte in bruto.split(";"):
            parte = parte.strip()
            if parte and parte.lower() not in vistos:
                vistos.add(parte.lower())
                saida.append(parte)
    return saida


def separar_angulos(angulos: list[str]) -> tuple[list[str], str]:
    """Divide os angulos entre as caixas sugeridas e o campo de texto livre.

    Comparacao por minusculas: `Frontal` gravado antes precisa marcar a caixa
    `frontal`, senao salvar de novo duplicaria o valor no campo livre.
    """
    sugeridos = {a.lower() for a in ANGULOS_SUGERIDOS}
    marcados = [a.lower() for a in angulos if a.lower() in sugeridos]
    livres = [a for a in angulos if a.lower() not in sugeridos]
    return marcados, "; ".join(livres)


def margem_em_porcento(fracao: float) -> str:
    """0.42 vira "42".

    A margem e guardada como fracao desde a CLI e continua assim no banco; so a
    exibicao muda. "fracao, ex.: 0.42" era o campo mais confuso do formulario —
    ninguem pensa margem em decimal.
    """
    return f"{round(fracao * 100, 2):g}"


def campos_de(produto: Produto | None) -> dict:
    """Valores que preenchem o formulario de produto.

    Existe para o POST poder devolver a pagina de erro com o que a pessoa
    digitou. Antes o formulario voltava em branco e perdia tudo por causa de uma
    virgula no preco.
    """
    if produto is None:
        return {
            "sku": "", "nome": "", "categoria": "", "preco": "", "margem": "",
            "link_shop": "", "pasta_drive": "", "angulos": [], "status": "ativo",
        }
    return {
        "sku": produto.sku,
        "nome": produto.nome,
        "categoria": produto.categoria,
        "preco": f"{produto.preco:.2f}",
        "margem": margem_em_porcento(produto.margem),
        "link_shop": produto.link_shop,
        "pasta_drive": produto.pasta_drive,
        "angulos": produto.angulos,
        "status": produto.status,
    }


def _form_produto(request: Request, campos: dict, *, existente: bool, erro: str | None):
    marcados, livres = separar_angulos(campos["angulos"])
    return _pagina(
        request,
        "produto.html",
        campos=campos,
        angulos_marcados=marcados,
        angulos_livres=livres,
        existente=existente,
        erro=erro,
        status_validos=STATUS_PRODUTO,
        categorias=opcoes_de_categoria(catalogo_de(request)),
        angulos_sugeridos=ANGULOS_SUGERIDOS,
        outra_categoria=OUTRA_CATEGORIA,
        hoje=date.today().isoformat(),  # so para o exemplo de nome de arquivo
    )


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

QTDS_BRIEFING = (1, 2, 3, 5, 8, 10, 15, 20)


@app.get("/", response_class=HTMLResponse)
def briefing_de_hoje(request: Request, data: str | None = None):
    catalogo = catalogo_de(request)
    hoje = date.today().isoformat()

    try:
        dia_data = date.fromisoformat(data) if data else date.today()
    except ValueError:
        # `?data=` vem da URL, entao qualquer coisa pode chegar. Data invalida
        # cai para hoje em vez de derrubar a pagina inteira.
        dia_data = date.today()
    dia = dia_data.isoformat()

    videos = [v for v in catalogo.videos() if v.data == dia]
    produtos = {p.sku: p for p in catalogo.produtos()}

    return _pagina(
        request,
        "briefing.html",
        dia=dia,
        hoje=hoje,
        dia_anterior=(dia_data - timedelta(days=1)).isoformat(),
        dia_seguinte=(dia_data + timedelta(days=1)).isoformat(),
        videos=videos,
        produtos=produtos,
        qtds=QTDS_BRIEFING,
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
    return _form_produto(request, campos_de(None), existente=False, erro=None)


@app.get("/produto/{sku}", response_class=HTMLResponse)
def form_editar_produto(request: Request, sku: str):
    catalogo = catalogo_de(request)
    produto = catalogo.buscar_produto(sku)
    if produto is None:
        return RedirectResponse("/catalogo", status_code=303)

    return _form_produto(request, campos_de(produto), existente=True, erro=None)


@app.post("/produto")
def salvar_produto(
    request: Request,
    sku: str = Form(...),
    nome: str = Form(...),
    categoria: str = Form(...),
    categoria_nova: str = Form(""),
    preco: str = Form("0"),
    margem: str = Form("0"),
    link_shop: str = Form(""),
    pasta_drive: str = Form(""),
    angulos: list[str] = Form(default=[]),
    angulos_livres: str = Form(""),
    status: str = Form("ativo"),
    existente: str = Form(""),
):
    catalogo = catalogo_de(request)

    escolhida = categoria_nova if categoria == OUTRA_CATEGORIA else categoria
    escolhida = escolhida.strip().lower()
    lista_angulos = normalizar_angulos(angulos, angulos_livres)

    def recusar(mensagem: str):
        """Devolve o formulario com o que foi digitado, nao em branco."""
        return _form_produto(
            request,
            {
                "sku": sku.strip(), "nome": nome.strip(), "categoria": escolhida,
                "preco": preco, "margem": margem, "link_shop": link_shop.strip(),
                "pasta_drive": pasta_drive.strip(), "angulos": lista_angulos,
                "status": status,
            },
            existente=bool(existente),
            erro=mensagem,
        )

    if status not in STATUS_PRODUTO:
        return recusar(f"status invalido: {status}")
    if not sku.strip() or not nome.strip() or not escolhida:
        return recusar("SKU, nome e categoria sao obrigatorios")

    try:
        produto = Produto(
            sku=sku.strip(),
            nome=nome.strip(),
            categoria=escolhida,
            preco=float(preco or 0),
            # O formulario manda porcentagem; o modelo guarda fracao.
            margem=float(margem or 0) / 100,
            link_shop=link_shop.strip(),
            pasta_drive=pasta_drive.strip(),
            angulos=lista_angulos,
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
