"""Camada 2 — carga e validacao da matriz combinatoria (blocos.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .modelos import EIXOS, ValorBloco

CHAVES_RESERVADAS = {"id", "texto", "en", "categorias"}


class ErroBlocos(Exception):
    pass


@dataclass(frozen=True)
class MatrizBlocos:
    eixos: dict[str, list[ValorBloco]]
    termos_proibidos: tuple[str, ...]

    def compativeis(self, eixo: str, categoria: str) -> list[ValorBloco]:
        return [v for v in self.eixos[eixo] if v.serve(categoria)]

    def espaco(self, categoria: str) -> int:
        """Quantas combinacoes distintas existem para uma categoria de produto."""
        total = 1
        for eixo in self.eixos:
            total *= len(self.compativeis(eixo, categoria))
        return total


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
