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
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gdv import blocos as mod_blocos
from gdv import briefing as mod_briefing
from gdv.catalogo import ErroCatalogo
from gdv.diagnostico import modulo_disponivel
from gdv.modelos import (
    ANGULOS_DETALHADOS,
    DESCRICAO_EIXO,
    EIXOS,
    ROTULO_STATUS_PRODUTO,
    ROTULO_STATUS_VIDEO,
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

# Disponiveis em todo template: o status gravado e uma chave tecnica
# ("briefado", "esgotado") e nenhuma tela deve mostrar a chave crua.
templates.env.globals["rotulo_status_video"] = ROTULO_STATUS_VIDEO
templates.env.globals["rotulo_status_produto"] = ROTULO_STATUS_PRODUTO

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


def _ir_para(caminho: str, **avisos: str) -> RedirectResponse:
    """Redireciona levando o aviso na query, sempre escapado.

    Antes a mensagem era interpolada crua (`f"/?erro={exc}"`): texto com `&`,
    `#` ou `%` truncava o aviso no meio ou quebrava a URL inteira. `urlencode`
    resolve os tres casos.

    `ok=` e o par de `erro=`: toda acao que muda dados volta dizendo o que fez,
    porque redirecionar em silencio deixa a pessoa sem saber se funcionou.
    """
    limpos = {chave: valor for chave, valor in avisos.items() if valor}
    destino = f"{caminho}?{urlencode(limpos)}" if limpos else caminho
    return RedirectResponse(destino, status_code=303)


def _explicar(exc: Exception) -> str:
    """Traduz a falha para quem nao conhece o pipeline por dentro.

    As mensagens do motor citam `blocos.yaml`, `produtos.csv` e `--janela`:
    corretas no terminal, inuteis para quem so abre o painel. O texto tecnico
    continua no log; aqui vai o que a pessoa consegue fazer a respeito.
    """
    if isinstance(exc, EspacoCombinatorioEsgotado):
        return (
            "As combinações possíveis para estes produtos acabaram por enquanto — "
            "o sorteio evita repetir as 30 últimas. Some valores novos em "
            "Parâmetros (um cenário a mais já multiplica o total) ou gere menos "
            "vídeos hoje."
        )
    if isinstance(exc, ValueError) and "nenhum produto ativo" in str(exc):
        return (
            "Nenhum produto está com o status Ativo. Abra o Catálogo e ative pelo "
            "menos um produto antes de gerar o briefing."
        )
    if isinstance(exc, mod_blocos.ErroBlocos):
        return f"A matriz de blocos está com um problema e o sorteio não pode rodar: {exc}"
    if isinstance(exc, ErroCatalogo):
        return (
            f"Não consegui falar com o banco de dados: {exc}. Se persistir, saia e "
            "entre de novo — costuma ser a sessão expirada."
        )
    return str(exc)


def _para_campo_numerico(bruto: str) -> str:
    """O valor de volta ao formulario, no formato que o <input type=number> aceita."""
    try:
        return f"{_numero(bruto):g}"
    except ValueError:
        return bruto


def _contagem(quantos: int, singular: str, plural: str) -> str:
    return f"{quantos} {singular if quantos == 1 else plural}."


def _numero(bruto: str) -> float:
    """Aceita "89,90" e "89.90". Vazio vira zero.

    Virgula decimal e o padrao brasileiro: recusar "89,90" era o erro mais
    comum do cadastro, e o mais irritante, porque a virgula esta certa. Quando
    ha virgula, o ponto so pode ser separador de milhar ("1.234,56").
    """
    texto = (bruto or "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return float(texto or 0)


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


def opcoes_de_angulo(parametros: Sequence) -> list[dict]:
    """Os angulos de fabrica mais os criados no painel, cada um explicado.

    Devolve dicionarios, nao strings: "frontal" sozinho nao diz a quem fotografa
    o que precisa estar no quadro. O `id` continua sendo o que vai para o banco.
    """
    opcoes = [
        {"id": id_, "rotulo": rotulo, "descricao": descricao}
        for id_, rotulo, descricao in ANGULOS_DETALHADOS
    ]
    vistos = {o["id"].lower() for o in opcoes}

    for p in parametros:
        if p.tipo == "angulo" and p.texto.lower() not in vistos:
            vistos.add(p.texto.lower())
            opcoes.append(
                {"id": p.texto, "rotulo": p.texto, "descricao": p.descricao}
            )
    return opcoes


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
    marcados, livres = separar_angulos(campos["angulos"], [a["id"] for a in angulos])
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


@app.exception_handler(RequestValidationError)
def erro_de_formulario(request: Request, exc: RequestValidationError):
    """Formulario incompleto vira pagina, nao o JSON de validacao do FastAPI.

    So acontece quando algo envia menos campos que o formulario da tela — um
    atalho antigo, uma extensao do navegador. O padrao do FastAPI responderia
    `{"detail":[{"type":"missing",...}]}` na cara de quem usa o painel.
    """
    faltando = ", ".join(
        str(parte)
        for erro in exc.errors()
        for parte in erro.get("loc", ())[1:]
    )
    return _pagina_de_erro(
        request,
        "Faltou preencher alguma coisa",
        f"O formulário chegou incompleto: {faltando or 'campo desconhecido'}.",
        "Volte à tela anterior e envie o formulário de novo, preenchendo todos "
        "os campos obrigatórios.",
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
        return "As duas senhas digitadas não são iguais."
    if len(nova) < MINIMO_SENHA:
        return f"A senha precisa ter pelo menos {MINIMO_SENHA} caracteres."
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
        return recusar(
            "Este link não é válido. Abra o link direto do e-mail de recuperação."
        )

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
        ativos=[p for p in produtos.values() if p.status == "ativo"],
        qtds=QTDS_BRIEFING,
        erro=request.query_params.get("erro"),
        ok=request.query_params.get("ok"),
    )


async def _redigir(catalogo, matriz, produtos, dia: str, excluir: Sequence[str] = ()):
    """Sorteia e redige um video por produto. Nao persiste nada.

    `excluir` soma hashes a janela de anti-repeticao so nesta rodada. E o que
    faz o "refazer" entregar combinacoes diferentes das que acabaram de ser
    descartadas, sem que elas voltem a ocupar a janela para sempre — a linha ja
    foi apagada do log, entao para as proximas geracoes elas nao existiram.
    """
    recentes = list(catalogo.hashes_recentes(mod_briefing.JANELA_PADRAO)) + list(excluir)
    combinacoes = sortear_lote(produtos, matriz, recentes, random.Random())

    redator = criar_redator(matriz.termos_proibidos)

    # Sequencial, 5 chamadas ao Gemini estourariam a duracao da funcao. O Redator
    # e sincrono e continua assim; o paralelismo mora aqui.
    pacotes = await asyncio.gather(
        *(
            asyncio.to_thread(redator.redigir, produto, combinacao, dia)
            for produto, combinacao in zip(produtos, combinacoes)
        )
    )

    return [
        mod_briefing.registro_de(produto, combinacao, pacote, dia, "0")
        for produto, combinacao, pacote in zip(produtos, combinacoes, pacotes)
    ]


ERROS_DE_GERACAO = (ValueError, EspacoCombinatorioEsgotado, mod_blocos.ErroBlocos, ErroCatalogo)


@app.post("/briefing")
async def gerar_briefing(request: Request, qtd: int = Form(5)):
    catalogo = catalogo_de(request)
    dia = date.today().isoformat()

    if any(v.data == dia for v in catalogo.videos()):
        return _ir_para(
            "/",
            erro="O briefing de hoje já foi gerado. Para sortear outras "
                 'combinações, use "Refazer os de hoje".',
        )

    try:
        matriz, _ = matriz_de(catalogo)
        produtos = mod_briefing.selecionar_produtos(
            catalogo.produtos_ativos(), catalogo.contagem_por_sku(), max(1, min(qtd, 20))
        )
        registros = await _redigir(catalogo, matriz, produtos, dia)
        catalogo.registrar(registros)
    except ERROS_DE_GERACAO as exc:
        return _ir_para("/", erro=_explicar(exc))

    return _ir_para("/", ok=_contagem(len(registros), "vídeo pronto", "vídeos prontos"))


@app.post("/briefing/refazer")
async def refazer_briefing(request: Request):
    """Descarta os briefados de hoje e sorteia outros no lugar.

    So mexe em `briefado`: se um video ja foi marcado como gerado, o clipe
    correspondente esta baixado em `entrada/` com aquele nome de arquivo, e
    apagar a linha deixaria o arquivo orfao para a montagem.
    """
    catalogo = catalogo_de(request)
    dia = date.today().isoformat()

    try:
        descartaveis = [
            v for v in catalogo.videos() if v.data == dia and v.status == "briefado"
        ]
        if not descartaveis:
            return _ir_para(
                "/",
                erro="Não há vídeo pendente de hoje para refazer. Só dá para "
                     "sortear de novo o que ainda não foi gerado no Flow.",
            )

        # Guardado antes de apagar: e o que garante combinacao diferente agora.
        descartados = [v.combinacao_hash for v in descartaveis]
        catalogo.remover_videos([v.id for v in descartaveis])

        matriz, _ = matriz_de(catalogo)
        produtos = mod_briefing.selecionar_produtos(
            catalogo.produtos_ativos(), catalogo.contagem_por_sku(), len(descartaveis)
        )
        registros = await _redigir(catalogo, matriz, produtos, dia, excluir=descartados)
        catalogo.registrar(registros)
    except ERROS_DE_GERACAO as exc:
        return _ir_para("/", erro=_explicar(exc))

    return _ir_para(
        "/", ok=_contagem(len(registros), "vídeo sorteado de novo", "vídeos sorteados de novo")
    )


@app.post("/briefing/produto")
async def gerar_para_produto(request: Request, sku: str = Form(...)):
    """Um video sob demanda para um SKU escolhido, somado ao dia de hoje."""
    catalogo = catalogo_de(request)
    dia = date.today().isoformat()

    try:
        produto = catalogo.buscar_produto(sku.strip())
        if produto is None:
            return _ir_para(
                "/", erro=f"Não encontrei o produto {sku} no catálogo."
            )
        if produto.status != "ativo":
            rotulo = ROTULO_STATUS_PRODUTO.get(produto.status, (produto.status, ""))[0]
            return _ir_para(
                "/",
                erro=f"{produto.nome} está como “{rotulo}”. Mude o status para "
                     "Ativo no catálogo antes de gerar um vídeo.",
            )

        matriz, _ = matriz_de(catalogo)
        registros = await _redigir(catalogo, matriz, [produto], dia)
        catalogo.registrar(registros)
    except ERROS_DE_GERACAO as exc:
        return _ir_para("/", erro=_explicar(exc))

    return _ir_para("/", ok=f"Vídeo avulso criado para {produto.nome}.")


@app.post("/briefing/descartar")
def descartar_video(request: Request, video_id: str = Form(...)):
    """Apaga um video do log. Depois disso ele nao existiu: sai da janela."""
    catalogo = catalogo_de(request)

    try:
        registro = catalogo.buscar(video_id)
        if registro.status != "briefado":
            rotulo = ROTULO_STATUS_VIDEO.get(registro.status, (registro.status, ""))[0]
            return _ir_para(
                "/",
                erro=f"Este vídeo já está como “{rotulo}” — o clipe existe fora do "
                     "painel. Só dá para descartar o que ainda não foi gerado.",
            )
        catalogo.remover_videos([video_id])
    except ErroCatalogo as exc:
        return _ir_para("/", erro=_explicar(exc))

    return _ir_para("/", ok="Vídeo descartado. A combinação volta a poder ser sorteada.")


# ------------------------------------------------------------------ catalogo

@app.get("/catalogo", response_class=HTMLResponse)
def listar_catalogo(request: Request):
    catalogo = catalogo_de(request)
    return _pagina(
        request,
        "catalogo.html",
        produtos=catalogo.produtos(),
        contagens=catalogo.contagem_por_sku(),
        erro=request.query_params.get("erro"),
        ok=request.query_params.get("ok"),
    )


@app.get("/produto/novo", response_class=HTMLResponse)
def form_novo_produto(request: Request):
    return _form_produto(request, campos_de(None), existente=False, erro=None)


@app.get("/produto/{sku}", response_class=HTMLResponse)
def form_editar_produto(request: Request, sku: str):
    catalogo = catalogo_de(request)
    produto = catalogo.buscar_produto(sku)
    if produto is None:
        # Redirecionar calado fazia a pessoa achar que tinha clicado errado.
        return _ir_para("/catalogo", erro=f"Não encontrei o produto {sku} no catálogo.")

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
                # Normalizado: "89,90" num <input type=number> e valor invalido,
                # e o navegador apaga o campo em vez de mostrar o que a pessoa
                # digitou. Vira "89.9" e sobrevive ao erro de outro campo.
                "preco": _para_campo_numerico(preco),
                "margem": _para_campo_numerico(margem),
                "link_shop": link_shop.strip(),
                "pasta_drive": pasta_drive.strip(), "angulos": lista_angulos,
                "status": status,
            },
            existente=bool(existente),
            erro=mensagem,
        )

    if status not in STATUS_PRODUTO:
        return recusar(f"Status inválido: {status}.")
    if not sku.strip() or not nome.strip() or not escolhida:
        return recusar("Preencha SKU, nome e categoria — são os três obrigatórios.")

    # Salvar e upsert pelo SKU: sem esta checagem, cadastrar um produto novo com
    # SKU ja usado sobrescrevia o antigo em silencio, e nada avisava que um
    # produto do catalogo tinha acabado de virar outro.
    if not existente and catalogo.buscar_produto(sku.strip()) is not None:
        return recusar(
            f"Já existe um produto com o SKU {sku.strip()}. Abra-o no catálogo "
            "para editar, ou use outro código."
        )

    try:
        produto = Produto(
            sku=sku.strip(),
            nome=nome.strip(),
            categoria=escolhida,
            preco=_numero(preco),
            # O formulario manda porcentagem; o modelo guarda fracao.
            margem=_numero(margem) / 100,
            link_shop=link_shop.strip(),
            pasta_drive=pasta_drive.strip(),
            angulos=lista_angulos,
            status=status,
        )
    except ValueError:
        return recusar(
            "Preço e margem precisam ser números. Escreva só os dígitos, "
            "com vírgula ou ponto nos centavos — por exemplo 89,90."
        )

    try:
        catalogo.salvar_produto(produto)
    except ErroCatalogo as exc:
        return recusar(_explicar(exc))

    acao = "atualizado" if existente else "cadastrado"
    return _ir_para("/catalogo", ok=f"Produto {produto.sku} {acao}.")


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
        descricao_eixo=DESCRICAO_EIXO,
    )


# ---------------------------------------------------------------- parametros

@app.get("/parametros", response_class=HTMLResponse)
def ver_parametros(request: Request):
    return _pagina_parametros(
        request,
        campos=CAMPOS_PARAMETRO_VAZIOS,
        erro=request.query_params.get("erro"),
        ok=request.query_params.get("ok"),
    )


# O formulario volta com o que foi digitado quando a validacao recusa. Antes
# qualquer erro redirecionava e apagava tudo — inclusive o descritor em ingles,
# que e o campo mais demorado de escrever da tela.
CAMPOS_PARAMETRO_VAZIOS = {
    "tipo": "eixo", "eixo": "", "texto": "", "en": "", "descricao": "", "categorias": [],
}


def _pagina_parametros(request: Request, campos: dict, erro: str | None = None,
                       ok: str | None = None, status_code: int = 200) -> HTMLResponse:
    catalogo = catalogo_de(request)
    parametros, aviso = parametros_seguros(catalogo)
    try:
        matriz = mod_blocos.carregar(BLOCOS)
    except (mod_blocos.ErroBlocos, OSError):
        matriz = None

    resposta = _pagina(
        request,
        "parametros.html",
        parametros=parametros,
        aviso_parametros=aviso,
        eixos=EIXOS,
        descricao_eixo=DESCRICAO_EIXO,
        # Um valor existente por eixo, para o formulario mostrar o estilo de
        # descritor em ingles que funciona — copiar um modelo e mais facil que
        # adivinhar o que o Veo entende.
        exemplos={
            eixo: matriz.eixos[eixo][0] for eixo in EIXOS
        } if matriz else {},
        categorias=opcoes_de_categoria(catalogo, parametros),
        campos=campos,
        erro=erro,
        ok=ok,
    )
    resposta.status_code = status_code
    return resposta


@app.post("/parametros", response_class=HTMLResponse)
def criar_parametro(
    request: Request,
    tipo: str = Form(...),
    eixo: str = Form(""),
    texto: str = Form(...),
    en: str = Form(""),
    descricao: str = Form(""),
    categorias: list[str] = Form(default=[]),
):
    catalogo = catalogo_de(request)
    texto = texto.strip()
    eixo = eixo.strip() if tipo == "eixo" else ""

    def recusar(mensagem: str):
        """Devolve o formulario preenchido, nao a tela em branco."""
        return _pagina_parametros(
            request,
            campos={
                "tipo": tipo, "eixo": eixo, "texto": texto, "en": en.strip(),
                "descricao": descricao.strip(), "categorias": list(categorias),
            },
            erro=mensagem,
        )

    if tipo not in TIPOS_PARAMETRO:
        return recusar(f"Tipo inválido: {tipo}.")

    if not texto:
        return recusar("Escreva o texto em português — é o nome que aparece no painel.")

    if tipo == "eixo":
        if eixo not in EIXOS:
            return recusar(f"Eixo inválido: {eixo}.")
        if not en.strip():
            return recusar(
                "A descrição da cena em inglês é obrigatória: é ela que o Veo "
                "usa para montar as imagens do vídeo."
            )

    chave = mod_blocos.chave_de(texto)
    if not chave:
        return recusar("O texto precisa ter pelo menos uma letra ou número.")

    try:
        catalogo.salvar_parametro(
            Parametro(
                tipo=tipo,
                eixo=eixo,
                chave=chave,
                texto=texto,
                en=en.strip(),
                descricao=descricao.strip(),
                # Sem categoria marcada o valor serve todo produto, igual ao YAML.
                categorias=tuple(c.strip().lower() for c in categorias if c.strip()),
            )
        )
    except ErroCatalogo as exc:
        return recusar(_explicar(exc))

    return _ir_para("/parametros", ok=f"“{texto}” entrou no sorteio.")


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
        return _ir_para("/parametros", erro=_explicar(exc))

    return _ir_para("/parametros", ok="Valor removido. Ele não será mais sorteado.")
