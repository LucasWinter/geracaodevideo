from __future__ import annotations

import random
from collections import Counter

import pytest

from gdv import briefing as mod_briefing
from gdv.modelos import Produto
from gdv.redator import RedatorTemplate, criar_redator, validar_claims
from gdv.sorteio import sortear


# ------------------------------------------------------------------ claims

@pytest.mark.parametrize(
    "texto",
    [
        "esse serum clareia manchas rapido",
        "Clareia as manchas",
        "resultado garantido",
        "veja o efeito em 7 dias",
        "clinically proven formula",
    ],
)
def test_filtro_barra_claim(texto, matriz):
    assert validar_claims(texto, matriz.termos_proibidos)


@pytest.mark.parametrize(
    "texto",
    [
        "bolsa linda pra usar todo dia",
        "fiz uma curadoria de achadinhos",  # "cura" dentro de outra palavra
        "a cor clara combina com tudo",
    ],
)
def test_filtro_nao_da_falso_positivo(texto, matriz):
    assert validar_claims(texto, matriz.termos_proibidos) == []


def test_filtro_ignora_acentuacao():
    assert validar_claims("isso EMAGRECE mesmo", ("emagrece",))


# ----------------------------------------------------------------- template

def test_template_e_deterministico(matriz, produto):
    combinacao = sortear(produto, matriz, rng=random.Random(5))
    redator = RedatorTemplate(matriz.termos_proibidos)

    a = redator.redigir(produto, combinacao, "2026-08-12")
    b = redator.redigir(produto, combinacao, "2026-08-12")

    assert a == b
    assert a.fonte == "template"


def test_template_reflete_os_blocos_sorteados(matriz, produto):
    combinacao = sortear(produto, matriz, rng=random.Random(9))
    pacote = RedatorTemplate(matriz.termos_proibidos).redigir(produto, combinacao, "2026-08-12")

    for eixo in ("cenario", "camera", "iluminacao", "detalhe_close"):
        assert combinacao[eixo].en in pacote.prompt_veo


def test_nome_de_arquivo_casa_com_o_hash(matriz, produto):
    combinacao = sortear(produto, matriz, rng=random.Random(2))
    pacote = RedatorTemplate().redigir(produto, combinacao, "2026-08-12")

    assert pacote.nome_arquivo == f"BLS-001_2026-08-12_{combinacao.hash}.mp4"


def test_gancho_recebe_o_preco_formatado(matriz, produto):
    combinacao = sortear(produto, matriz, rng=random.Random(1))
    while combinacao["gancho_pov"].id != "achado_preco":
        combinacao = sortear(produto, matriz, rng=random.Random())

    pacote = RedatorTemplate().redigir(produto, combinacao, "2026-08-12")

    assert "89,90" in pacote.gancho


def test_template_recusa_claim_vindo_do_catalogo(matriz):
    proibido = Produto(
        sku="SER-01",
        nome="Sérum que clareia manchas",
        categoria="cuidados",
        preco=59.9,
        margem=0.4,
        link_shop="",
        pasta_drive="",
        angulos=[],
        status="ativo",
    )
    combinacao = sortear(proibido, matriz, rng=random.Random(4))

    with pytest.raises(ValueError, match="termo proibido"):
        RedatorTemplate(matriz.termos_proibidos).redigir(proibido, combinacao, "2026-08-12")


def test_sem_chave_cai_para_template(matriz, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert isinstance(criar_redator(matriz.termos_proibidos), RedatorTemplate)


def test_sem_llm_ignora_a_chave(matriz, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "chave-qualquer")

    assert isinstance(criar_redator(matriz.termos_proibidos, forcar_template=True), RedatorTemplate)


# ----------------------------------------------------------------- briefing

def _produto(sku: str) -> Produto:
    return Produto(sku, sku, "casa", 10.0, 0.3, "", "", [], "ativo")


def test_selecao_prioriza_sku_com_menos_videos():
    produtos = [_produto("A"), _produto("B"), _produto("C")]
    contagens = Counter({"A": 10, "B": 0, "C": 3})

    escolhidos = [p.sku for p in mod_briefing.selecionar_produtos(produtos, contagens, 3)]

    assert escolhidos == ["B", "C", "A"]


def test_selecao_cicla_quando_pede_mais_que_o_catalogo():
    produtos = [_produto("A"), _produto("B")]

    escolhidos = [p.sku for p in mod_briefing.selecionar_produtos(produtos, Counter(), 5)]

    assert escolhidos == ["A", "B", "A", "B", "A"]


def test_selecao_sem_produto_ativo_e_erro_claro():
    with pytest.raises(ValueError, match="nenhum produto ativo"):
        mod_briefing.selecionar_produtos([], Counter(), 3)


def test_briefing_gera_registros_com_hashes_distintos(catalogo, matriz):
    itens, registros = mod_briefing.montar(
        catalogo, matriz, RedatorTemplate(matriz.termos_proibidos),
        data="2026-08-12", qtd=5, rng=random.Random(42),
    )

    assert len(registros) == 5
    assert len({r.combinacao_hash for r in registros}) == 5
    assert all(r.status == "briefado" for r in registros)
    assert [r.id for r in registros] == ["1", "2", "3", "4", "5"]


def test_briefing_do_dia_seguinte_evita_os_de_ontem(catalogo, matriz):
    redator = RedatorTemplate(matriz.termos_proibidos)

    _, ontem = mod_briefing.montar(
        catalogo, matriz, redator, data="2026-08-12", qtd=5, rng=random.Random(1)
    )
    catalogo.registrar(ontem)

    _, hoje = mod_briefing.montar(
        catalogo, matriz, redator, data="2026-08-13", qtd=5, rng=random.Random(1)
    )

    assert not {r.combinacao_hash for r in hoje} & {r.combinacao_hash for r in ontem}
    assert [r.id for r in hoje] == ["6", "7", "8", "9", "10"]


def test_markdown_traz_o_essencial_de_cada_video(catalogo, matriz):
    itens, registros = mod_briefing.montar(
        catalogo, matriz, RedatorTemplate(matriz.termos_proibidos),
        data="2026-08-12", qtd=2, rng=random.Random(8),
    )

    texto = mod_briefing.renderizar(itens, registros, "2026-08-12")

    assert "# Briefing 2026-08-12" in texto
    for item, registro in zip(itens, registros):
        assert item.pacote.prompt_veo in texto
        assert registro.arquivo in texto
        assert item.produto.pasta_drive in texto
        assert item.combinacao["cenario"].texto in texto
