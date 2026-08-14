"""Rotas do painel, com catalogo falso — nenhum teste toca rede."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gdv import blocos as mod_blocos
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


# ---------------------------------------------------------------- diagnostico

def test_saude_responde_sem_sessao(deslogado):
    """E a rota que serve justamente quando o login nao funciona."""
    resposta = deslogado.get("/saude")

    assert resposta.status_code == 200
    assert resposta.json()["modulos"]["fastapi"] is True


def test_saude_confirma_o_que_o_deploy_precisa(deslogado):
    dados = deslogado.get("/saude").json()

    assert dados["modulos"]["gdv"] is True
    assert dados["modulos"]["supabase"] is True
    assert dados["arquivos"]["web/templates"] is True
    assert dados["arquivos"]["data/blocos.yaml"] is True


def test_saude_nao_vaza_valor_de_variavel(deslogado, monkeypatch):
    monkeypatch.setenv("SUPABASE_ANON_KEY", "chave-secreta-nao-pode-vazar")

    resposta = deslogado.get("/saude")

    assert "chave-secreta-nao-pode-vazar" not in resposta.text
    assert resposta.json()["variaveis_definidas"]["SUPABASE_ANON_KEY"] is True


def test_app_sobe_sem_o_diretorio_de_estaticos(monkeypatch):
    """CSS ausente no bundle nao pode derrubar o app inteiro no import.

    Reexecuta o modulo com todo `is_dir()` respondendo False, que e como o
    ambiente da funcao se comporta quando web/static nao entrou no bundle.
    """
    import importlib
    import pathlib

    import web.main as mod

    monkeypatch.setattr(pathlib.Path, "is_dir", lambda _: False)
    try:
        recarregado = importlib.reload(mod)
        assert recarregado.app is not None
        rotas = {getattr(r, "path", None) for r in recarregado.app.routes}
        assert "/saude" in rotas
        assert "/static" not in rotas  # o mount foi pulado, nao explodiu
    finally:
        monkeypatch.undo()
        importlib.reload(mod)


def test_sem_config_do_supabase_diz_o_que_falta(deslogado):
    """Deploy sem as variaveis na Vercel: mensagem acionavel, nao stack trace."""
    resposta = deslogado.post("/login", data={"email": "a@b.c", "senha": "x"})

    assert resposta.status_code == 200
    assert "SUPABASE_URL" in resposta.text


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

def test_categoria_e_um_select_com_as_da_matriz(logado):
    """Digitar 'bolsa' no singular nao da erro — so encolhe o sorteio calado."""
    resposta = logado.get("/produto/novo")

    assert '<select id="categoria" name="categoria"' in resposta.text
    for categoria in ("bolsas", "acessorios", "casa", "cuidados", "moda"):
        assert f'value="{categoria}"' in resposta.text


def test_matriz_quebrada_nao_impede_cadastrar_produto(logado, monkeypatch):
    """/blocos e /briefing ja reportam esse erro; o cadastro nao deve travar."""
    def falhar(_):
        raise mod_blocos.ErroBlocos("blocos.yaml sumiu")

    monkeypatch.setattr("web.main.mod_blocos.carregar", falhar)

    resposta = logado.get("/produto/novo")

    assert resposta.status_code == 200
    assert 'value="bolsas"' in resposta.text  # veio do catalogo, nao da matriz


def test_categoria_do_catalogo_entra_na_lista(logado, catalogo_web):
    """Produto antigo com categoria fora da matriz nao pode perde-la ao editar."""
    catalogo_web.salvar_produto(
        Produto("PET-1", "Coleira", "petshop", 10.0, 0.3, "", "", [], "ativo")
    )

    resposta = logado.get("/produto/PET-1")

    assert 'value="petshop" selected' in resposta.text


def test_categoria_nova_pelo_campo_livre(logado, catalogo_web):
    resposta = logado.post(
        "/produto",
        data={"sku": "PAP-1", "nome": "Caderno", "categoria": "__outra__",
              "categoria_nova": "Papelaria", "status": "ativo"},
        follow_redirects=False,
    )

    assert resposta.status_code == 303
    assert catalogo_web.buscar_produto("PAP-1").categoria == "papelaria"


def test_categoria_vazia_e_recusada_mesmo_com_outra_marcada(logado, catalogo_web):
    resposta = logado.post(
        "/produto",
        data={"sku": "X-1", "nome": "X", "categoria": "__outra__",
              "categoria_nova": "   ", "status": "ativo"},
    )

    assert "obrigat" in resposta.text
    assert catalogo_web.buscar_produto("X-1") is None


def test_angulos_vem_das_caixas_marcadas(logado, catalogo_web):
    resposta = logado.post(
        "/produto",
        data={"sku": "ANG-1", "nome": "X", "categoria": "casa", "status": "ativo",
              "angulos": ["frontal", "em-uso"], "angulos_livres": "de-cima; frontal"},
        follow_redirects=False,
    )

    assert resposta.status_code == 303
    # 'frontal' repetido entre caixa e campo livre entra uma vez so.
    assert catalogo_web.buscar_produto("ANG-1").angulos == ["frontal", "em-uso", "de-cima"]


def test_formulario_remarca_os_angulos_salvos(logado, catalogo_web):
    catalogo_web.salvar_produto(
        Produto("ANG-2", "X", "casa", 1.0, 0.1, "", "", ["lateral", "macro-costura"], "ativo")
    )

    # Sem normalizar, a assercao passaria a depender da indentacao do template.
    html = " ".join(logado.get("/produto/ANG-2").text.split())

    assert 'value="lateral" checked' in html
    assert 'value="frontal" checked' not in html
    assert 'name="angulos_livres" value="macro-costura"' in html


def test_erro_no_formulario_preserva_o_que_foi_digitado(logado):
    """Antes o formulario voltava em branco e perdia tudo por causa do preco."""
    resposta = logado.post(
        "/produto",
        data={"sku": "X-1", "nome": "Nome Digitado", "categoria": "casa",
              "preco": "quarenta", "status": "ativo", "angulos": ["frontal"]},
    )

    assert "numeros" in resposta.text
    assert 'value="Nome Digitado"' in resposta.text
    assert 'value="casa" selected' in resposta.text


def test_margem_e_digitada_em_porcento_e_guardada_como_fracao(logado, catalogo_web):
    """"fração, ex.: 0.42" era o campo mais confuso; o banco segue em fração."""
    logado.post(
        "/produto",
        data={"sku": "MAR-1", "nome": "X", "categoria": "casa",
              "margem": "42", "status": "ativo"},
        follow_redirects=False,
    )

    assert catalogo_web.buscar_produto("MAR-1").margem == pytest.approx(0.42)


def test_margem_volta_ao_formulario_em_porcento(logado, catalogo_web):
    catalogo_web.salvar_produto(
        Produto("MAR-2", "X", "casa", 10.0, 0.425, "", "", [], "ativo")
    )

    html = " ".join(logado.get("/produto/MAR-2").text.split())

    # 42.5, nao 0.425 nem "42.50000000000001"
    assert 'name="margem" type="number" step="0.1" min="0" max="100"' in html
    assert 'value="42.5"' in html


def test_formulario_agrupa_os_campos_pelo_efeito_que_tem(logado):
    """Sete campos no mesmo peso visual nao diziam quais chegam ao prompt."""
    html = logado.get("/produto/novo").text

    assert "O que entra no vídeo" in html      # categoria, preco, angulos
    assert "Links que o briefing mostra" in html  # drive, shop
    assert "não afeta nada" in html            # margem, no <details>


def test_link_do_shop_aparece_no_briefing(logado, catalogo_web):
    """A legenda gerada manda "link do produto na vitrine"; o link tem que estar la."""
    catalogo_web.salvar_produto(
        Produto("SHP-1", "X", "casa", 10.0, 0.3, "https://shop/xyz", "", [], "ativo")
    )
    logado.post("/briefing", data={"qtd": 3})

    assert "https://shop/xyz" in logado.get("/").text


def test_quantidade_do_briefing_e_um_select(logado):
    resposta = logado.get("/")

    assert '<select id="qtd" name="qtd">' in resposta.text
    assert '<option value="20"' in resposta.text


# -------------------------------------------------------------- navegacao

def test_aba_atual_e_marcada(logado):
    assert 'href="/catalogo" aria-current="page"' in logado.get("/catalogo").text


def test_tela_de_produto_destaca_o_catalogo(logado):
    """/produto/... e uma tela do catalogo; destacar Briefing confundiria."""
    resposta = logado.get("/produto/BLS-001")

    assert 'href="/catalogo" aria-current="page"' in resposta.text


def test_dia_passado_nao_oferece_gerar(logado):
    """O briefing so e gerado para hoje — botao em outro dia mentiria."""
    resposta = logado.get("/?data=2020-01-01")

    assert "2020-01-01" in resposta.text
    assert "Gerar briefing de hoje" not in resposta.text


def test_data_invalida_na_url_cai_para_hoje(logado):
    from datetime import date

    resposta = logado.get("/?data=nao-e-data")

    assert resposta.status_code == 200
    assert date.today().isoformat() in resposta.text


# -------------------------------------------------------------------- blocos

def test_matriz_aparece_so_para_leitura(logado):
    resposta = logado.get("/blocos")

    assert resposta.status_code == 200
    assert "gancho_pov" in resposta.text
    assert "Só leitura" in resposta.text


# ---------------------------------------------------------------- parametros

def test_parametros_exige_sessao(deslogado):
    assert deslogado.get("/parametros", follow_redirects=False).status_code == 303


def test_parametros_abre_vazio(logado):
    resposta = logado.get("/parametros")

    assert resposta.status_code == 200
    assert "Nada ainda" in resposta.text


def test_valor_de_eixo_criado_entra_no_sorteio(logado, catalogo_web):
    """O ponto da aba: ampliar a matriz sem editar arquivo nem fazer deploy."""
    logado.post(
        "/parametros",
        data={"tipo": "eixo", "eixo": "cenario", "texto": "varanda ao entardecer",
              "en": "apartment balcony at dusk"},
        follow_redirects=False,
    )

    assert [p.chave for p in catalogo_web.parametros()] == ["varanda_ao_entardecer"]
    assert "varanda ao entardecer" in logado.get("/blocos").text


def test_valor_criado_aparece_marcado_na_matriz(logado):
    logado.post(
        "/parametros",
        data={"tipo": "eixo", "eixo": "camera", "texto": "drone baixo", "en": "low drone pass"},
    )

    html = " ".join(logado.get("/blocos").text.split())

    assert '<code>drone_baixo</code> <span class="etiqueta">painel</span>' in html


def test_valor_de_eixo_exige_ingles(logado, catalogo_web):
    """Sem o `en` o prompt do Veo perde a descricao da cena."""
    resposta = logado.post(
        "/parametros",
        data={"tipo": "eixo", "eixo": "cenario", "texto": "varanda", "en": "  "},
        follow_redirects=False,
    )

    assert "ingles" in resposta.headers["location"]
    assert catalogo_web.parametros() == []


def test_eixo_invalido_e_recusado(logado, catalogo_web):
    resposta = logado.post(
        "/parametros",
        data={"tipo": "eixo", "eixo": "inventado", "texto": "x", "en": "x"},
        follow_redirects=False,
    )

    assert "eixo+invalido" in resposta.headers["location"].replace("%20", "+")
    assert catalogo_web.parametros() == []


def test_texto_sem_letras_nao_gera_chave(logado, catalogo_web):
    resposta = logado.post(
        "/parametros",
        data={"tipo": "categoria", "texto": "!!!"},
        follow_redirects=False,
    )

    assert "erro=" in resposta.headers["location"]
    assert catalogo_web.parametros() == []


def test_categoria_criada_aparece_no_formulario_de_produto(logado):
    logado.post("/parametros", data={"tipo": "categoria", "texto": "papelaria"})

    assert 'value="papelaria"' in logado.get("/produto/novo").text


def test_angulo_criado_vira_caixa_de_selecao(logado):
    logado.post("/parametros", data={"tipo": "angulo", "texto": "dobrado"})

    html = " ".join(logado.get("/produto/novo").text.split())

    assert 'name="angulos" value="dobrado"' in html


def test_angulo_criado_e_reconhecido_ao_editar(logado, catalogo_web):
    """Sem isto o angulo criado cairia no campo livre em vez de marcar a caixa."""
    logado.post("/parametros", data={"tipo": "angulo", "texto": "dobrado"})
    catalogo_web.salvar_produto(
        Produto("ANG-3", "X", "casa", 1.0, 0.1, "", "", ["dobrado"], "ativo")
    )

    html = " ".join(logado.get("/produto/ANG-3").text.split())

    assert 'value="dobrado" checked' in html
    assert 'name="angulos_livres" value=""' in html


def test_categoria_criada_restringe_o_bloco(logado, catalogo_web):
    logado.post(
        "/parametros",
        data={"tipo": "eixo", "eixo": "cenario", "texto": "prateleira",
              "en": "store shelf", "categorias": ["casa"]},
    )

    parametro = catalogo_web.parametros()[0]
    assert parametro.categorias == ("casa",)


def test_remover_tira_o_valor(logado, catalogo_web):
    logado.post(
        "/parametros",
        data={"tipo": "eixo", "eixo": "cenario", "texto": "varanda", "en": "balcony"},
    )

    logado.post(
        "/parametros/remover",
        data={"tipo": "eixo", "eixo": "cenario", "chave": "varanda"},
        follow_redirects=False,
    )

    assert catalogo_web.parametros() == []


# --------------------------------------------------------------------- senha

def test_recuperar_e_publico(deslogado):
    assert deslogado.get("/recuperar").status_code == 200


def test_recuperar_nao_revela_se_o_email_existe(deslogado, monkeypatch):
    """Mensagem diferente por e-mail transformaria a tela num verificador de contas."""
    chamados = []
    monkeypatch.setattr(
        "web.main.auth.pedir_recuperacao", lambda email, url: chamados.append(email)
    )

    conhecido = deslogado.post("/recuperar", data={"email": "existe@b.c"}).text
    desconhecido = deslogado.post("/recuperar", data={"email": "nao-existe@b.c"}).text

    assert conhecido == desconhecido
    assert "Se esse e-mail estiver cadastrado" in conhecido
    assert chamados == ["existe@b.c", "nao-existe@b.c"]


def test_redefinir_e_publico(deslogado):
    """Quem chega pelo link do e-mail nao tem sessao ainda."""
    assert deslogado.get("/redefinir").status_code == 200


def test_redefinir_sem_token_avisa(deslogado):
    resposta = deslogado.post(
        "/redefinir", data={"acesso": "", "refresh": "", "senha": "senha-longa-1",
                            "senha2": "senha-longa-1"}
    )

    assert "link inv" in resposta.text


def test_redefinir_devolve_o_token_quando_a_senha_nao_confere(deslogado):
    """Sem devolver, errar a confirmacao obrigaria a pedir outro e-mail."""
    resposta = deslogado.post(
        "/redefinir",
        data={"acesso": "tok-abc", "refresh": "ref-abc",
              "senha": "senha-longa-1", "senha2": "outra-senha-2"},
    )

    assert "nao sao iguais" in resposta.text
    assert 'value="tok-abc"' in resposta.text


def test_redefinir_recusa_senha_curta(deslogado):
    resposta = deslogado.post(
        "/redefinir", data={"acesso": "t", "senha": "1234", "senha2": "1234"}
    )

    assert "8 caracteres" in resposta.text


def test_redefinir_com_token_valido_loga(deslogado, monkeypatch):
    monkeypatch.setattr(
        "web.main.auth.redefinir_com_token",
        lambda *_: mod_auth.Sessao("novo-at", "novo-rt", "a@b.c"),
    )

    resposta = deslogado.post(
        "/redefinir",
        data={"acesso": "t", "refresh": "r", "senha": "senha-longa-1", "senha2": "senha-longa-1"},
        follow_redirects=False,
    )

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/"
    assert mod_auth.COOKIE_ACESSO in resposta.cookies


def test_trocar_senha_exige_sessao(deslogado):
    assert deslogado.get("/senha", follow_redirects=False).status_code == 303


def test_trocar_senha_confirma(logado, monkeypatch):
    monkeypatch.setattr("web.main.auth.trocar_senha", lambda *_: None)

    resposta = logado.post(
        "/senha", data={"senha": "senha-longa-1", "senha2": "senha-longa-1"}
    )

    assert "Senha trocada" in resposta.text


def test_trocar_senha_recusa_divergente(logado, monkeypatch):
    def nao_deveria(*_):
        raise AssertionError("nao pode chamar o Supabase com senha invalida")

    monkeypatch.setattr("web.main.auth.trocar_senha", nao_deveria)

    resposta = logado.post("/senha", data={"senha": "senha-longa-1", "senha2": "diferente-2"})

    assert "nao sao iguais" in resposta.text


def test_login_oferece_recuperacao(deslogado):
    assert 'href="/recuperar"' in deslogado.get("/login").text
