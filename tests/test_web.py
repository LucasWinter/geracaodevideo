"""Rotas do painel, com catalogo falso — nenhum teste toca rede."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gdv.catalogo_supabase import CatalogoSupabase
from gdv.modelos import Produto

from falsos import ClienteFalso
from web import auth as mod_auth
from web.main import app


@pytest.fixture
def catalogo_web() -> CatalogoSupabase:
    catalogo = CatalogoSupabase(ClienteFalso())
    catalogo.salvar_produto(
        Produto("BLS-001", "Bolsa", "bolsas", 89.9, 0.42, "", "https://drive/1", ["frontal"], "ativo")
    )
    catalogo.salvar_produto(
        Produto("CAS-014", "Caneca", "casa", 39.9, 0.55, "", "https://drive/2", ["topo"], "ativo")
    )
    return catalogo


@pytest.fixture
def logado(catalogo_web, monkeypatch) -> TestClient:
    """Cliente com sessao valida: o auth devolve sempre o catalogo falso."""
    monkeypatch.setattr(mod_auth, "catalogo_da_requisicao", lambda _: catalogo_web)
    monkeypatch.setattr("web.main.auth.catalogo_da_requisicao", lambda _: catalogo_web)
    return TestClient(app)


@pytest.fixture
def deslogado(monkeypatch) -> TestClient:
    monkeypatch.setattr("web.main.auth.catalogo_da_requisicao", lambda _: None)
    return TestClient(app)


# -------------------------------------------------------------------- sessao

def test_sem_sessao_redireciona_para_login(deslogado):
    resposta = deslogado.get("/", follow_redirects=False)

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/login"


def test_catalogo_tambem_exige_sessao(deslogado):
    assert deslogado.get("/catalogo", follow_redirects=False).status_code == 303


def test_login_e_publico(deslogado):
    resposta = deslogado.get("/login")

    assert resposta.status_code == 200
    assert "Entrar" in resposta.text


def test_login_com_senha_errada_mostra_erro(deslogado, monkeypatch):
    def recusar(*_):
        raise mod_auth.ErroAutenticacao("e-mail ou senha invalidos")

    monkeypatch.setattr("web.main.auth.entrar", recusar)

    resposta = deslogado.post("/login", data={"email": "a@b.c", "senha": "errada"})

    assert resposta.status_code == 200
    assert "inv" in resposta.text  # "invalidos"


def test_login_certo_grava_cookie_e_redireciona(deslogado, monkeypatch):
    monkeypatch.setattr(
        "web.main.auth.entrar",
        lambda *_: mod_auth.Sessao("token-acesso", "token-refresh", "a@b.c"),
    )

    resposta = deslogado.post(
        "/login", data={"email": "a@b.c", "senha": "certa"}, follow_redirects=False
    )

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/"
    assert mod_auth.COOKIE_ACESSO in resposta.cookies


def test_cookie_de_sessao_e_httponly(deslogado, monkeypatch):
    """Token acessivel por JS seria roubavel via XSS."""
    monkeypatch.setattr(
        "web.main.auth.entrar", lambda *_: mod_auth.Sessao("t", "r", "a@b.c")
    )

    resposta = deslogado.post("/login", data={"email": "a@b.c", "senha": "x"},
                              follow_redirects=False)

    cabecalho = resposta.headers["set-cookie"].lower()
    assert "httponly" in cabecalho
    assert "secure" in cabecalho


# ------------------------------------------------------------------ briefing

def test_briefing_vazio_convida_a_gerar(logado):
    resposta = logado.get("/")

    assert resposta.status_code == 200
    assert "Gerar briefing de hoje" in resposta.text


def test_gerar_cria_os_videos_do_dia(logado, catalogo_web):
    resposta = logado.post("/briefing", data={"qtd": 3}, follow_redirects=False)

    assert resposta.status_code == 303
    videos = catalogo_web.videos()
    assert len(videos) == 3
    assert len({v.combinacao_hash for v in videos}) == 3
    assert all(v.prompt for v in videos)
    assert all(v.status == "briefado" for v in videos)


def test_prompt_aparece_na_pagina_para_copiar(logado):
    logado.post("/briefing", data={"qtd": 1})

    resposta = logado.get("/")

    assert "Prompt (Flow / Veo)" in resposta.text
    assert "data-copiar=" in resposta.text


def test_nao_gera_briefing_duplicado_no_mesmo_dia(logado, catalogo_web):
    logado.post("/briefing", data={"qtd": 2})
    logado.post("/briefing", data={"qtd": 2}, follow_redirects=False)

    assert len(catalogo_web.videos()) == 2


def test_qtd_absurda_e_limitada(logado, catalogo_web):
    logado.post("/briefing", data={"qtd": 999}, follow_redirects=False)

    assert len(catalogo_web.videos()) == 20


def test_sem_produto_ativo_reporta_erro_em_vez_de_500(logado, catalogo_web):
    for sku in ("BLS-001", "CAS-014"):
        produto = catalogo_web.buscar_produto(sku)
        catalogo_web.salvar_produto(
            Produto(produto.sku, produto.nome, produto.categoria, produto.preco,
                    produto.margem, "", "", [], "pausado")
        )

    resposta = logado.post("/briefing", data={"qtd": 5}, follow_redirects=False)

    assert resposta.status_code == 303
    assert "erro=" in resposta.headers["location"]
    assert catalogo_web.videos() == []


# ------------------------------------------------------------------ catalogo

def test_catalogo_lista_os_produtos(logado):
    resposta = logado.get("/catalogo")

    assert "BLS-001" in resposta.text
    assert "Caneca" in resposta.text


def test_formulario_de_novo_produto_abre(logado):
    assert logado.get("/produto/novo").status_code == 200


def test_editar_produto_preenche_o_formulario(logado):
    resposta = logado.get("/produto/BLS-001")

    assert "Bolsa" in resposta.text
    assert 'value="89.90"' in resposta.text


def test_produto_inexistente_volta_para_o_catalogo(logado):
    resposta = logado.get("/produto/NAO-EXISTE", follow_redirects=False)

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/catalogo"


def test_salvar_produto_novo(logado, catalogo_web):
    resposta = logado.post(
        "/produto",
        data={
            "sku": "NOVO-1", "nome": "Produto Novo", "categoria": "Casa",
            "preco": "49.90", "margem": "0.4", "link_shop": "",
            "pasta_drive": "https://drive/novo", "angulos": "frontal;topo",
            "status": "ativo",
        },
        follow_redirects=False,
    )

    assert resposta.status_code == 303
    salvo = catalogo_web.buscar_produto("NOVO-1")
    assert salvo.nome == "Produto Novo"
    assert salvo.categoria == "casa"  # normalizado
    assert salvo.angulos == ["frontal", "topo"]


def test_status_invalido_e_recusado_no_formulario(logado, catalogo_web):
    resposta = logado.post(
        "/produto",
        data={"sku": "X-1", "nome": "X", "categoria": "casa", "status": "inventado"},
    )

    assert "status invalido" in resposta.text
    assert catalogo_web.buscar_produto("X-1") is None


def test_preco_nao_numerico_e_recusado(logado, catalogo_web):
    resposta = logado.post(
        "/produto",
        data={"sku": "X-1", "nome": "X", "categoria": "casa",
              "preco": "quarenta", "status": "ativo"},
    )

    assert "numeros" in resposta.text
    assert catalogo_web.buscar_produto("X-1") is None


def test_campos_obrigatorios(logado, catalogo_web):
    resposta = logado.post(
        "/produto", data={"sku": "  ", "nome": "X", "categoria": "casa", "status": "ativo"}
    )

    assert "obrigat" in resposta.text


# -------------------------------------------------------------------- blocos

def test_matriz_aparece_so_para_leitura(logado):
    resposta = logado.get("/blocos")

    assert resposta.status_code == 200
    assert "gancho_pov" in resposta.text
    assert "Só leitura" in resposta.text
