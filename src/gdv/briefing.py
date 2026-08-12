"""Camada 4 (parcial) — briefing diario.

Entrega o pacote do dia num markdown pronto para copiar e colar no Flow. A
geracao do video em si continua manual: e o passo que exige olho humano.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .blocos import MatrizBlocos
from .protocolo import ProtocoloCatalogo
from .modelos import EIXOS, Combinacao, PacoteCriativo, Produto, RegistroVideo
from .redator import Redator
from .sorteio import sortear_lote

JANELA_PADRAO = 30


@dataclass(frozen=True)
class ItemBriefing:
    produto: Produto
    combinacao: Combinacao
    pacote: PacoteCriativo


def selecionar_produtos(
    produtos: Sequence[Produto],
    contagens: Counter[str],
    qtd: int,
) -> list[Produto]:
    """Round-robin priorizando quem tem menos videos no log.

    Sortear o SKU junto com o resto deixaria um produto dominar a semana por
    azar de `random`; aqui a distribuicao e explicita.
    """
    if not produtos:
        raise ValueError("nenhum produto ativo no catalogo (status=ativo em produtos.csv)")

    ordenados = sorted(produtos, key=lambda p: (contagens.get(p.sku, 0), p.sku))
    return [ordenados[i % len(ordenados)] for i in range(qtd)]


def montar(
    catalogo: ProtocoloCatalogo,
    matriz: MatrizBlocos,
    redator: Redator,
    data: str,
    qtd: int = 5,
    rng: random.Random | None = None,
    janela: int = JANELA_PADRAO,
) -> tuple[list[ItemBriefing], list[RegistroVideo]]:
    """Sorteia, redige e devolve os itens do dia + as linhas para o catalogo.

    Nao escreve nada: quem persiste e o chamador (para `--dry-run` funcionar).
    """
    produtos = selecionar_produtos(catalogo.produtos_ativos(), catalogo.contagem_por_sku(), qtd)
    combinacoes = sortear_lote(produtos, matriz, catalogo.hashes_recentes(janela), rng)

    itens: list[ItemBriefing] = []
    registros: list[RegistroVideo] = []
    proximo = catalogo.proximo_id()

    for indice, (produto, combinacao) in enumerate(zip(produtos, combinacoes)):
        pacote = redator.redigir(produto, combinacao, data)
        itens.append(ItemBriefing(produto=produto, combinacao=combinacao, pacote=pacote))
        registros.append(registro_de(produto, combinacao, pacote, data, str(proximo + indice)))

    return itens, registros


def registro_de(
    produto: Produto,
    combinacao: Combinacao,
    pacote: PacoteCriativo,
    data: str,
    video_id: str,
) -> RegistroVideo:
    """Converte o resultado do sorteio + redacao numa linha de catalogo.

    Extraido para que o site e a CLI montem o registro exatamente igual. No
    backend Supabase o `video_id` e ignorado — quem numera e a identity.
    """
    return RegistroVideo(
        id=video_id,
        sku=produto.sku,
        data=data,
        combinacao_hash=combinacao.hash,
        blocos_json=json.dumps(combinacao.ids(), sort_keys=True, ensure_ascii=False),
        gancho=pacote.gancho,
        legenda=pacote.legenda,
        hashtags=" ".join(f"#{t}" for t in pacote.hashtags),
        prompt=pacote.prompt_veo,
        arquivo=pacote.nome_arquivo,
        status="briefado",
    )


def renderizar(itens: Sequence[ItemBriefing], registros: Sequence[RegistroVideo], data: str) -> str:
    linhas = [
        f"# Briefing {data}",
        "",
        f"{len(itens)} vídeos. Para cada um: monte o frame inicial no Nano Banana com a foto real "
        "do produto no cenário indicado, valide o frame, e só então gere o vídeo no Flow.",
        "",
    ]

    for item, registro in zip(itens, registros):
        pacote = item.pacote
        combinacao = item.combinacao

        linhas += [
            f"## {registro.id} — {item.produto.nome} (`{item.produto.sku}`)",
            "",
            f"- **Combinação** `{combinacao.hash}` · redigido por `{pacote.fonte}`",
            f"- **Fotos do produto:** {item.produto.pasta_drive}",
            f"- **Cenário para o frame inicial:** {combinacao['cenario'].texto}",
            f"- **Ritmo:** {combinacao['ritmo'].texto} "
            f"({combinacao.clipes}× {combinacao.duracao}s)",
            f"- **Salvar como:** `{pacote.nome_arquivo}`",
            "",
            "**Prompt (Flow / Veo):**",
            "",
            "```text",
            pacote.prompt_veo,
            "```",
            "",
            f"**Gancho na tela:** {pacote.gancho}",
            "",
            f"**Legenda:** {pacote.legenda}",
            "",
            f"**Hashtags:** {registro.hashtags}",
            "",
            "<details><summary>Blocos sorteados</summary>",
            "",
            # gancho_pov usa o texto ja interpolado: o cru traria "{preco}".
            *[
                f"- `{eixo}`: "
                f"{pacote.gancho if eixo == 'gancho_pov' else combinacao[eixo].texto}"
                for eixo in EIXOS
            ],
            "",
            "</details>",
            "",
        ]

    linhas += [
        "---",
        "",
        "Depois de baixar os clipes do Flow para `entrada/`, marque cada vídeo com "
        "`gdv status <id> gerado` e rode `gdv montar`.",
        "",
    ]

    return "\n".join(linhas)


def salvar(texto: str, data: str, diretorio: Path | str = "saida/briefing") -> Path:
    destino = Path(diretorio)
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / f"{data}.md"
    caminho.write_text(texto, encoding="utf-8")
    return caminho
