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
from collections.abc import Sequence
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
from gdv.modelos import (
    ANGULOS_SUGERIDOS,
    EIXOS,
    STATUS_PRODUTO,
    TIPOS_PARAMETRO,
    Parametro,
    Produto,
)
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

PUBLICAS = {"/login", "/static", "/favicon.ico", "/saude", "/recuperar", "/redefinir"}


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

# Minimo do Supabase e 6; 8 e o que este painel exige, porque a conta e criada
# com senha padrao e a troca precisa valer alguma coisa.
MINIMO_SENHA = 8


def parametros_seguros(catalogo) -> tuple[list, str]:
    """Os parametros do painel, ou lista vazia e o motivo da falha.

    A tabela `parametros` e uma extensao opcional: sem ela a matriz do
    blocos.yaml sozinha gera o dia inteiro. Deixar o erro subir faz uma tabela
    nova derrubar briefing, catalogo, cadastro de produto e matriz de uma vez,
    porque `parametros()` passou a ser chamado em quase toda pagina — foi
    exatamente o que aconteceu quando ela foi criada e o cache de schema do
    PostgREST ainda nao a enxergava.

    O motivo volta junto para virar aviso na tela em vez de 500 mudo.
    """
    try:
        return catalogo.parametros(), ""
    except ErroCatalogo as exc:
        return [], str(exc)


def matriz_de(catalogo) -> tuple[mod_blocos.MatrizBlocos, str]:
    """A matriz do YAML somada aos parametros do painel, mais o aviso de falha.

    Toda leitura da matriz passa por aqui — briefing, tela de blocos, tela de
    parametros. Se o site enxergasse uma matriz e a CLI outra, `hash_combinacao`
    daria valores diferentes e a janela anti-repeticao de um nao veria o outro.
    """
    parametros, aviso = parametros_seguros(catalogo)
    return mod_blocos.mesclar(mod_blocos.carregar(BLOCOS), parametros), aviso


def opcoes_de_categoria(catalogo, parametros: Sequence) -> list[str]:
    """Categorias da matriz, das criadas no painel e das que ja existem no catalogo.

    A matriz manda: valor de bloco com `categorias` so entra no sorteio se casar
    exatamente com a categoria do produto. As do catalogo entram junto para que
    editar um produto antigo nao perca a categoria dele so por ela nao estar no
    blocos.yaml.
    """
    try:
        conhecidas = set(mod_blocos.mesclar(mod_blocos.carregar(BLOCOS), parametros).categorias())
    except (mod_blocos.ErroBlocos, OSError):
        # Matriz quebrada nao pode travar o cadastro de produto — /blocos e
        # /briefing ja reportam esse erro com mensagem propria.
        conhecidas = set()

    conhecidas.update(p.texto for p in parametros if p.tipo == "categoria")
    conhecidas.update(p.categoria for p in catalogo.produtos() if p.categoria)
    return sorted(c for c in conhecidas if c)


def opcoes_de_angulo(parametros: Sequence) -> list[str]:
    """Os angulos fixos do codigo mais os criados no painel."""
    extras = [p.texto for p in parametros if p.tipo == "angulo"]
    vistos = {a.lower() for a in ANGULOS_SUGERIDOS}
    return list(ANGULOS_SUGERIDOS) + [a for a in extras if a.lower() not in vistos]


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


def separar_angulos(angulos: list[str], oferecidos: Sequence[str]) -> tuple[list[str], str]:
    """Divide os angulos entre as caixas oferecidas e o campo de texto livre.

    Comparacao por minusculas: `Frontal` gravado antes precisa marcar a caixa
    `frontal`, senao salvar de novo duplicaria o valor no campo livre.
    """
    sugeridos = {a.lower() for a in oferecidos}
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
    catalogo = catalogo_de(request)
    # Uma leitura de parametros por requisicao: antes este formulario chamava
    # `parametros()` duas vezes, uma por lista, e cada chamada e uma ida a rede.
    parametros, aviso = parametros_seguros(catalogo)
    angulos = opcoes_de_angulo(parametros)
    marcados, livres = separar_angulos(campos["angulos"], angulos)
    return _pagina(
        request,
        "produto.html",
        campos=campos,
        angulos_marcados=marcados,
        angulos_livres=livres,
        existente=existente,
        erro=erro,
        aviso_parametros=aviso,
        status_validos=STATUS_PRODUTO,
        categorias=opcoes_de_categoria(catalogo, parametros),
        angulos_sugeridos=angulos,
        outra_categoria=OUTRA_CATEGORIA,
        hoje=date.today().isoformat(),  # so para o exemplo de nome de arquivo
    )


# ------------------------------------------------------------ paginas de erro

def _pagina_de_erro(request: Request, titulo: str, detalhe: str, explicacao: str):
    return templates.TemplateResponse(
        request,
        "erro.html",
        {"titulo": titulo, "detalhe": detalhe, "explicacao": explicacao},
        status_code=500,
    )


@app.exception_handler(ErroCatalogo)
def erro_de_dados(request: Request, exc: ErroCatalogo):
    """Falha de Supabase vira pagina legivel, nao 500 em branco."""
    return _pagina_de_erro(
        request,
        "Não consegui falar com o banco",
        str(exc),
        "Costuma ser sessão expirada, RLS negando a consulta ou tabela que o "
        "PostgREST ainda não enxerga. Sair e entrar de novo resolve o primeiro caso.",
    )


@app.exception_handler(Exception)
def erro_inesperado(request: Request, exc: Exception):
    """Ultimo recurso: mostrar a causa em vez de "Internal Server Error".

    Sem isto, depurar o deploy exige o log da Vercel — e quem toca o painel nem
    sempre tem acesso a ele. Vai o tipo e a mensagem, nunca o traceback nem
    valor de variavel de ambiente; o traceback fica no log da funcao.
    """
    return _pagina_de_erro(
        request,
        "Algo quebrou nesta página",
        f"{type(exc).__name__}: {exc}",
        "O traceback completo está no log da função na Vercel. Se a mensagem "
        "citar um módulo ausente, ele precisa estar em [project].dependencies.",
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
        "tabelas": _tabelas_alcancaveis(),
    }


def _tabelas_alcancaveis() -> dict[str, str]:
    """Diz se o PostgREST conhece cada tabela, sem precisar de sessao.

    Como anonimo a RLS nega a leitura — e tudo bem: negar prova que a tabela
    existe. O que interessa distinguir e "nao encontrada" (tabela ausente ou
    fora do cache de schema do PostgREST) de "sem permissao". Sem isso, uma
    tabela recem-criada aparece como 500 em toda pagina e nao ha como
    diagnosticar sem o log da Vercel.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    chave = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not chave:
        return {"_": "SUPABASE_URL/ANON_KEY nao configurados"}

    try:
        from supabase import create_client

        cliente = create_client(url, chave)
    except Exception as exc:
        return {"_": f"{type(exc).__name__}: {exc}"[:200]}

    resultado = {}
    for tabela in ("produtos", "videos", "parametros"):
        try:
            cliente.table(tabela).select("*").limit(1).execute()
            resultado[tabela] = "ok"
        except Exception as exc:
            # Mensagem truncada: basta para reconhecer PGRST205 (tabela fora do
            # cache) ou 42501 (RLS), que sao os dois casos que importam.
            resultado[tabela] = f"{type(exc).__name__}: {exc}"[:200]
    return resultado


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


# ------------------------------------------------------------------ senha

def _senha_fraca(nova: str, repetida: str) -> str | None:
    if nova != repetida:
        return "as duas senhas nao sao iguais"
    if len(nova) < MINIMO_SENHA:
        return f"a senha precisa de pelo menos {MINIMO_SENHA} caracteres"
    return None


@app.get("/recuperar", response_class=HTMLResponse)
def form_recuperar(request: Request):
    return _pagina(request, "recuperar.html", enviado=False, erro=None)


@app.post("/recuperar", response_class=HTMLResponse)
def pedir_recuperacao(request: Request, email: str = Form(...)):
    """Sempre responde a mesma coisa, exista o e-mail ou nao.

    Dizer "esse e-mail nao existe" transformaria a tela num verificador de quem
    tem conta. O Supabase so envia para quem esta cadastrado, entao o "so para
    usuarios da base" e garantido pelo servidor, nao pela mensagem.
    """
    auth.pedir_recuperacao(email.strip(), str(request.url_for("form_redefinir")))
    return _pagina(request, "recuperar.html", enviado=True, erro=None)


@app.get("/redefinir", response_class=HTMLResponse, name="form_redefinir")
def form_redefinir(request: Request):
    """O token vem no fragmento da URL, que o navegador nao manda ao servidor.

    Por isso a pagina e servida vazia e o JS copia o fragmento para o formulario
    antes do envio.
    """
    return _pagina(request, "redefinir.html", erro=None, acesso="", refresh="")


@app.post("/redefinir")
def aplicar_redefinicao(
    request: Request,
    acesso: str = Form(""),
    refresh: str = Form(""),
    senha: str = Form(...),
    senha2: str = Form(...),
):
    def recusar(mensagem: str):
        """Devolve o token junto: o fragmento da URL ja foi consumido, e sem ele
        a pessoa teria que pedir outro e-mail so porque errou a confirmacao."""
        return _pagina(
            request, "redefinir.html", erro=mensagem, acesso=acesso, refresh=refresh
        )

    problema = _senha_fraca(senha, senha2)
    if problema:
        return recusar(problema)
    if not acesso:
        return recusar("link invalido — abra o do e-mail de novo")

    try:
        sessao = auth.redefinir_com_token(acesso, refresh, senha)
    except auth.ErroAutenticacao as exc:
        return recusar(str(exc))

    resposta = RedirectResponse("/", status_code=303)
    auth.gravar_cookies(resposta, sessao)
    return resposta


@app.get("/senha", response_class=HTMLResponse)
def form_senha(request: Request):
    return _pagina(request, "senha.html", erro=None, trocada=False)


@app.post("/senha", response_class=HTMLResponse)
def trocar_senha(request: Request, senha: str = Form(...), senha2: str = Form(...)):
    problema = _senha_fraca(senha, senha2)
    if problema:
        return _pagina(request, "senha.html", erro=problema, trocada=False)

    try:
        auth.trocar_senha(
            request.cookies.get(auth.COOKIE_ACESSO, ""),
            request.cookies.get(auth.COOKIE_REFRESH, ""),
            senha,
        )
    except auth.ErroAutenticacao as exc:
        return _pagina(request, "senha.html", erro=str(exc), trocada=False)

    return _pagina(request, "senha.html", erro=None, trocada=True)


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
        matriz, _ = matriz_de(catalogo)
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
    """So leitura. Para acrescentar valor sem deploy, a tela e /parametros."""
    catalogo = catalogo_de(request)
    parametros, aviso = parametros_seguros(catalogo)
    matriz = mod_blocos.mesclar(mod_blocos.carregar(BLOCOS), parametros)
    do_painel = {(p.eixo, p.chave) for p in parametros if p.tipo == "eixo"}
    return _pagina(
        request, "blocos.html", matriz=matriz, eixos=EIXOS,
        do_painel=do_painel, aviso_parametros=aviso,
    )


# ---------------------------------------------------------------- parametros

@app.get("/parametros", response_class=HTMLResponse)
def ver_parametros(request: Request):
    catalogo = catalogo_de(request)
    parametros, aviso = parametros_seguros(catalogo)
    try:
        matriz = mod_blocos.carregar(BLOCOS)
    except (mod_blocos.ErroBlocos, OSError):
        matriz = None

    return _pagina(
        request,
        "parametros.html",
        parametros=parametros,
        aviso_parametros=aviso,
        eixos=EIXOS,
        # Um valor existente por eixo, para o formulario mostrar o estilo de
        # descritor em ingles que funciona — copiar um modelo e mais facil que
        # adivinhar o que o Veo entende.
        exemplos={
            eixo: matriz.eixos[eixo][0] for eixo in EIXOS
        } if matriz else {},
        categorias=opcoes_de_categoria(catalogo, parametros),
        erro=request.query_params.get("erro"),
    )


@app.post("/parametros")
def criar_parametro(
    request: Request,
    tipo: str = Form(...),
    eixo: str = Form(""),
    texto: str = Form(...),
    en: str = Form(""),
    categorias: list[str] = Form(default=[]),
):
    catalogo = catalogo_de(request)

    def recusar(mensagem: str):
        return RedirectResponse(f"/parametros?erro={mensagem}", status_code=303)

    if tipo not in TIPOS_PARAMETRO:
        return recusar(f"tipo invalido: {tipo}")

    texto = texto.strip()
    if not texto:
        return recusar("o texto e obrigatorio")

    eixo = eixo.strip() if tipo == "eixo" else ""
    if tipo == "eixo":
        if eixo not in EIXOS:
            return recusar(f"eixo invalido: {eixo}")
        if not en.strip():
            return recusar("o descritor em ingles e obrigatorio para valor de eixo")

    chave = mod_blocos.chave_de(texto)
    if not chave:
        return recusar("o texto precisa ter letras ou numeros")

    try:
        catalogo.salvar_parametro(
            Parametro(
                tipo=tipo,
                eixo=eixo,
                chave=chave,
                texto=texto,
                en=en.strip(),
                # Sem categoria marcada o valor serve todo produto, igual ao YAML.
                categorias=tuple(c.strip().lower() for c in categorias if c.strip()),
            )
        )
    except ErroCatalogo as exc:
        return recusar(str(exc))

    return RedirectResponse("/parametros", status_code=303)


@app.post("/parametros/remover")
def remover_parametro(
    request: Request, tipo: str = Form(...), eixo: str = Form(""), chave: str = Form(...)
):
    """Remover so tira o valor do sorteio dali para a frente.

    Video ja gerado com ele continua valido: o hash guardado no log e um texto,
    nao uma referencia. O que nao pode e renomear a chave.
    """
    catalogo = catalogo_de(request)
    try:
        catalogo.remover_parametro(tipo, eixo, chave)
    except ErroCatalogo as exc:
        return RedirectResponse(f"/parametros?erro={exc}", status_code=303)

    return RedirectResponse("/parametros", status_code=303)
