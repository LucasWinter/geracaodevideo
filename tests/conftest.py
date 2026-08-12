from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gdv import blocos as mod_blocos
from gdv.catalogo import Catalogo
from gdv.modelos import Produto

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def matriz():
    return mod_blocos.carregar(RAIZ / "data" / "blocos.yaml")


@pytest.fixture
def dados(tmp_path) -> Path:
    """Copia do catalogo real, isolado por teste."""
    destino = tmp_path / "data"
    destino.mkdir()
    for nome in ("produtos.csv", "videos.csv", "blocos.yaml"):
        shutil.copy(RAIZ / "data" / nome, destino / nome)
    return destino


@pytest.fixture
def catalogo(dados) -> Catalogo:
    return Catalogo(dados)


@pytest.fixture
def produto() -> Produto:
    return Produto(
        sku="BLS-001",
        nome="Bolsa Tiracolo",
        categoria="bolsas",
        preco=89.90,
        margem=0.42,
        link_shop="https://exemplo",
        pasta_drive="https://drive/exemplo",
        angulos=["frontal"],
        status="ativo",
    )
