from __future__ import annotations

import random

import pytest

from gdv.blocos import MatrizBlocos
from gdv.modelos import EIXOS, ValorBloco
from gdv.sorteio import (
    EspacoCombinatorioEsgotado,
    hash_combinacao,
    sortear,
    sortear_lote,
)


def _valores(eixo: str, quantos: int) -> list[ValorBloco]:
    return [
        ValorBloco(eixo=eixo, id=f"{eixo}{i}", texto=f"t{i}", en=f"e{i}")
        for i in range(quantos)
    ]


def _matriz_minima(quantos: int = 2) -> MatrizBlocos:
    return MatrizBlocos(
        eixos={eixo: _valores(eixo, quantos) for eixo in EIXOS},
        termos_proibidos=(),
    )


def test_hash_independe_da_ordem_de_leitura(matriz, produto):
    combinacao = sortear(produto, matriz, rng=random.Random(1))
    invertido = dict(reversed(list(combinacao.valores.items())))

    assert hash_combinacao(invertido) == combinacao.hash


def test_hash_muda_quando_um_bloco_muda(matriz, produto):
    a = sortear(produto, matriz, rng=random.Random(1))
    trocado = dict(a.valores)
    outro = next(v for v in matriz.eixos["camera"] if v.id != a["camera"].id)
    trocado["camera"] = outro

    assert hash_combinacao(trocado) != a.hash


def test_mesmo_seed_reproduz_a_combinacao(matriz, produto):
    a = sortear(produto, matriz, rng=random.Random(42))
    b = sortear(produto, matriz, rng=random.Random(42))

    assert a.hash == b.hash


def test_anti_repeticao_rejeita_hash_da_janela(matriz, produto):
    conhecido = sortear(produto, matriz, rng=random.Random(7))
    novo = sortear(produto, matriz, [conhecido.hash], rng=random.Random(7))

    assert novo.hash != conhecido.hash


def test_lote_nao_repete_dentro_do_proprio_dia(matriz, produto):
    combinacoes = sortear_lote([produto] * 5, matriz, rng=random.Random(3))
    hashes = [c.hash for c in combinacoes]

    assert len(set(hashes)) == 5


def test_espaco_esgotado_levanta_em_vez_de_travar(produto):
    matriz = _matriz_minima(2)
    rng = random.Random(0)

    # 2^6 = 64 combinacoes; a janela cobre todas elas.
    janela = set()
    while len(janela) < matriz.espaco(produto.categoria):
        janela.add(sortear(produto, matriz, rng=rng).hash)

    with pytest.raises(EspacoCombinatorioEsgotado, match="combinacao inedita"):
        sortear(produto, matriz, list(janela), rng=rng)


def test_categoria_incompativel_nao_e_sorteada(matriz):
    from gdv.modelos import Produto

    caneca = Produto(
        sku="CAS-014",
        nome="Caneca",
        categoria="casa",
        preco=39.9,
        margem=0.5,
        link_shop="",
        pasta_drive="",
        angulos=[],
        status="ativo",
    )

    for _ in range(40):
        combinacao = sortear(caneca, matriz, rng=random.Random())
        # "provando no espelho" e "costura" sao exclusivos de bolsas/moda.
        assert combinacao["gancho_pov"].serve("casa")
        assert combinacao["detalhe_close"].serve("casa")


def test_pesos_direcionam_o_sorteio(produto):
    """Ponto de extensao da camada 6: bloco vencedor aparece mais."""
    matriz = _matriz_minima(2)
    favorito = matriz.eixos["camera"][0].id
    pesos = {"camera": {favorito: 1000.0}}

    escolhas = [
        sortear(produto, matriz, rng=random.Random(i), pesos=pesos)["camera"].id
        for i in range(30)
    ]

    assert escolhas.count(favorito) > 25
