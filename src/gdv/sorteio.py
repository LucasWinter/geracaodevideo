"""Camada 2 — sorteio combinatorio com anti-repeticao.

O ponto do modulo: um LLM ao qual voce pede "gere 5 prompts variados" converge
para o mesmo padrao. Aqui a variacao e estrutural — a matriz sorteia a combinacao
e o LLM so redige o que foi sorteado.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence

from .blocos import MatrizBlocos
from .modelos import EIXOS, Combinacao, Produto, ValorBloco

MAX_TENTATIVAS = 50


class EspacoCombinatorioEsgotado(Exception):
    """Nao ha combinacao inedita disponivel para a janela anti-repeticao pedida."""


def hash_combinacao(valores: dict[str, ValorBloco]) -> str:
    """Identidade estavel de uma combinacao.

    Ordena os eixos antes de somar, entao a ordem de leitura do YAML nao muda o
    hash. Truncado em 12 chars: colisao e improvavel na escala de milhares de
    videos e o valor aparece em nome de arquivo.
    """
    assinatura = "|".join(f"{eixo}={valores[eixo].id}" for eixo in sorted(valores))
    return hashlib.sha256(assinatura.encode("utf-8")).hexdigest()[:12]


def _escolher(
    valores: Sequence[ValorBloco],
    rng: random.Random,
    pesos: dict[str, float] | None = None,
) -> ValorBloco:
    """Sorteia um valor de um eixo.

    `pesos` e o ponto de extensao da camada 6 (loop de feedback): quando as
    metricas de views/GMV existirem, basta passar {id_do_bloco: peso} aqui para
    que os blocos vencedores apareçam mais. Hoje sempre None = uniforme.
    """
    if not valores:
        raise EspacoCombinatorioEsgotado("nenhum valor compativel para sortear")

    if not pesos:
        return rng.choice(list(valores))

    return rng.choices(
        list(valores),
        weights=[max(pesos.get(v.id, 1.0), 0.0) or 1e-9 for v in valores],
        k=1,
    )[0]


def sortear(
    produto: Produto,
    matriz: MatrizBlocos,
    hashes_recentes: Sequence[str] = (),
    rng: random.Random | None = None,
    pesos: dict[str, dict[str, float]] | None = None,
    max_tentativas: int = MAX_TENTATIVAS,
) -> Combinacao:
    """Sorteia uma combinacao inedita para o produto.

    `rng` e injetado para tornar o resultado reproduzivel (`gdv briefing --seed`).
    """
    rng = rng or random.Random()
    recentes = set(hashes_recentes)

    espaco = matriz.espaco(produto.categoria)
    if espaco == 0:
        raise EspacoCombinatorioEsgotado(
            f"nenhuma combinacao possivel para a categoria '{produto.categoria}': "
            f"algum eixo ficou sem valores compativeis. Revise as 'categorias' em blocos.yaml"
        )

    for _ in range(max_tentativas):
        valores = {
            eixo: _escolher(
                matriz.compativeis(eixo, produto.categoria),
                rng,
                (pesos or {}).get(eixo),
            )
            for eixo in EIXOS
        }
        assinatura = hash_combinacao(valores)
        if assinatura not in recentes:
            return Combinacao(valores=valores, hash=assinatura)

    # Sem esta checagem o chamador so veria "demorou" ou um loop infinito.
    raise EspacoCombinatorioEsgotado(
        f"nao achei combinacao inedita para {produto.sku} em {max_tentativas} tentativas. "
        f"A categoria '{produto.categoria}' tem {espaco} combinacoes e a janela "
        f"anti-repeticao guarda {len(recentes)}. Adicione valores em blocos.yaml "
        f"ou reduza a janela (--janela)."
    )


def sortear_lote(
    produtos: Sequence[Produto],
    matriz: MatrizBlocos,
    hashes_recentes: Sequence[str] = (),
    rng: random.Random | None = None,
    pesos: dict[str, dict[str, float]] | None = None,
) -> list[Combinacao]:
    """Sorteia uma combinacao por produto, sem repetir dentro do proprio lote."""
    rng = rng or random.Random()
    janela = list(hashes_recentes)
    combinacoes = []

    for produto in produtos:
        combinacao = sortear(produto, matriz, janela, rng, pesos)
        combinacoes.append(combinacao)
        janela.append(combinacao.hash)

    return combinacoes
