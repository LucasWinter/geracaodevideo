"""Camada 2 — carga e validacao da matriz combinatoria (blocos.yaml)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .modelos import EIXOS, Parametro, ValorBloco

CHAVES_RESERVADAS = {"id", "texto", "en", "categorias"}


class ErroBlocos(Exception):
    pass


@dataclass(frozen=True)
class MatrizBlocos:
    eixos: dict[str, list[ValorBloco]]
    termos_proibidos: tuple[str, ...]

    def compativeis(self, eixo: str, categoria: str) -> list[ValorBloco]:
        return [v for v in self.eixos[eixo] if v.serve(categoria)]

    def categorias(self) -> tuple[str, ...]:
        """Toda categoria citada por algum valor da matriz.

        E a lista que o formulario de produto oferece. Um valor com `categorias`
        so entra no sorteio quando a categoria do produto casa exatamente, e
        errar a grafia nao levanta erro nenhum — so encolhe o espaco
        combinatorio em silencio, que e a falha que ninguem percebe.
        """
        vistas = {c for valores in self.eixos.values() for v in valores for c in v.categorias}
        return tuple(sorted(vistas))

    def espaco(self, categoria: str) -> int:
        """Quantas combinacoes distintas existem para uma categoria de produto."""
        total = 1
        for eixo in self.eixos:
            total *= len(self.compativeis(eixo, categoria))
        return total


def chave_de(texto: str) -> str:
    """Deriva um id estavel a partir do texto digitado no painel.

    Sem acento, sem espaco, minusculo — o mesmo formato dos ids do YAML, porque
    os dois vao para o mesmo lugar: a assinatura que vira hash de combinacao.
    """
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    limpo = re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")
    return limpo[:48]


def mesclar(matriz: MatrizBlocos, parametros: Sequence[Parametro]) -> MatrizBlocos:
    """Soma a matriz do YAML os valores de eixo criados no painel.

    O YAML ganha em caso de empate de id: ele e versionado e revisado, a tabela
    e editavel por qualquer pessoa do time. Parametro de eixo desconhecido e
    ignorado em vez de derrubar o briefing — um eixo removido do codigo nao pode
    quebrar a geracao do dia.
    """
    eixos = {eixo: list(valores) for eixo, valores in matriz.eixos.items()}

    for parametro in parametros:
        if parametro.tipo != "eixo" or parametro.eixo not in eixos:
            continue
        if any(v.id == parametro.chave for v in eixos[parametro.eixo]):
            continue

        eixos[parametro.eixo].append(
            ValorBloco(
                eixo=parametro.eixo,
                id=parametro.chave,
                texto=parametro.texto,
                # Sem descritor em ingles o prompt do Veo perde qualidade, mas
                # cair para o portugues e melhor que sortear um valor vazio.
                en=parametro.en or parametro.texto,
                categorias=tuple(parametro.categorias),
                extras=dict(parametro.extras),
            )
        )

    return MatrizBlocos(eixos=eixos, termos_proibidos=matriz.termos_proibidos)


def carregar(caminho: Path | str = "data/blocos.yaml") -> MatrizBlocos:
    caminho = Path(caminho)
    if not caminho.exists():
        raise ErroBlocos(f"matriz de blocos nao encontrada: {caminho}")

    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    if not isinstance(dados, dict):
        raise ErroBlocos(f"{caminho}: esperado um mapa no topo do arquivo")

    brutos = dados.get("eixos")
    if not isinstance(brutos, dict):
        raise ErroBlocos(f"{caminho}: chave 'eixos' ausente ou nao e um mapa")

    faltando = set(EIXOS) - set(brutos)
    if faltando:
        raise ErroBlocos(f"{caminho}: eixos ausentes {sorted(faltando)}")

    sobrando = set(brutos) - set(EIXOS)
    if sobrando:
        raise ErroBlocos(
            f"{caminho}: eixos desconhecidos {sorted(sobrando)}; "
            f"adicione o eixo em modelos.EIXOS antes de usa-lo"
        )

    eixos = {eixo: _carregar_eixo(caminho, eixo, brutos[eixo]) for eixo in EIXOS}

    termos = dados.get("termos_proibidos") or []
    if not isinstance(termos, list):
        raise ErroBlocos(f"{caminho}: 'termos_proibidos' deve ser uma lista")

    return MatrizBlocos(
        eixos=eixos,
        termos_proibidos=tuple(str(t).strip().lower() for t in termos if str(t).strip()),
    )


def _carregar_eixo(caminho: Path, eixo: str, valores: Any) -> list[ValorBloco]:
    if not isinstance(valores, list) or len(valores) < 2:
        raise ErroBlocos(
            f"{caminho}: eixo '{eixo}' precisa ser uma lista com pelo menos 2 valores "
            f"(com 1 valor ele nao varia nada e so encolhe o espaco combinatorio)"
        )

    carregados: list[ValorBloco] = []
    vistos: set[str] = set()

    for indice, bruto in enumerate(valores):
        if not isinstance(bruto, dict):
            raise ErroBlocos(f"{caminho}: eixo '{eixo}' posicao {indice} nao e um mapa")

        for chave in ("id", "texto", "en"):
            if not str(bruto.get(chave, "")).strip():
                raise ErroBlocos(
                    f"{caminho}: eixo '{eixo}' posicao {indice} sem '{chave}'"
                )

        identificador = str(bruto["id"]).strip()
        if identificador in vistos:
            raise ErroBlocos(
                f"{caminho}: id duplicado '{identificador}' no eixo '{eixo}'; "
                f"ids precisam ser unicos porque compoem o hash de combinacao"
            )
        vistos.add(identificador)

        categorias = bruto.get("categorias") or []
        if not isinstance(categorias, list):
            raise ErroBlocos(
                f"{caminho}: 'categorias' de '{identificador}' deve ser uma lista"
            )

        carregados.append(
            ValorBloco(
                eixo=eixo,
                id=identificador,
                texto=str(bruto["texto"]).strip(),
                en=str(bruto["en"]).strip(),
                categorias=tuple(str(c).strip().lower() for c in categorias),
                # Campos livres (clipes, duracao, ...) viajam junto sem o loader
                # precisar conhece-los.
                extras={k: v for k, v in bruto.items() if k not in CHAVES_RESERVADAS},
            )
        )

    return carregados
