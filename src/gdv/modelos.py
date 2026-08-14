"""Tipos compartilhados por todas as camadas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Ordem canonica dos eixos da matriz. Usada na validacao de blocos.yaml e na
# apresentacao do briefing; o hash de combinacao ordena por conta propria.
EIXOS = ("gancho_pov", "cenario", "camera", "iluminacao", "detalhe_close", "ritmo")

STATUS_PRODUTO = ("ativo", "pausado", "esgotado")
STATUS_VIDEO = ("briefado", "gerado", "montado", "postado")

# Angulos de foto que o redator recebe como `angulos_disponiveis`. Diferente de
# EIXOS e STATUS_*, esta lista nao valida nada: e so a sugestao que o formulario
# oferece como caixas de selecao. Produto com angulo fora dela continua valido —
# o campo livre do formulario existe justamente para isso.
ANGULOS_SUGERIDOS = (
    "frontal",
    "lateral",
    "traseira",
    "superior",
    "detalhe-textura",
    "detalhe-fecho",
    "em-uso",
    "escala-mao",
    "interior",
    "embalagem",
)


@dataclass(frozen=True)
class Produto:
    sku: str
    nome: str
    categoria: str
    preco: float
    margem: float
    link_shop: str
    pasta_drive: str
    angulos: list[str]
    status: str


TIPOS_PARAMETRO = ("eixo", "categoria", "angulo")


@dataclass(frozen=True)
class Parametro:
    """Opcao criada pelo painel, somada as que vem do `data/blocos.yaml`.

    O YAML continua sendo a base versionada. Esta tabela existe para o time
    conseguir ampliar a matriz sem editar arquivo e refazer deploy.

    `chave` e o id estavel: para `tipo="eixo"` ela entra no hash de combinacao
    exatamente como o `id` do YAML, entao nao pode ser renomeada depois de
    sorteada. Por isso o formulario a deriva do texto uma vez e nao deixa editar.
    """

    tipo: str
    chave: str
    texto: str
    eixo: str = ""  # so preenchido quando tipo == "eixo"
    en: str = ""
    categorias: tuple[str, ...] = ()
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValorBloco:
    """Um valor sorteavel de um eixo da matriz."""

    eixo: str
    id: str
    texto: str
    en: str
    categorias: tuple[str, ...] = ()
    extras: dict[str, Any] = field(default_factory=dict)

    def serve(self, categoria: str) -> bool:
        """Valor sem `categorias` serve qualquer produto."""
        return not self.categorias or categoria in self.categorias


@dataclass(frozen=True)
class Combinacao:
    valores: dict[str, ValorBloco]
    hash: str

    def __getitem__(self, eixo: str) -> ValorBloco:
        return self.valores[eixo]

    @property
    def clipes(self) -> int:
        return int(self.valores["ritmo"].extras.get("clipes", 1))

    @property
    def duracao(self) -> int:
        return int(self.valores["ritmo"].extras.get("duracao", 8))

    def ids(self) -> dict[str, str]:
        return {eixo: valor.id for eixo, valor in self.valores.items()}


@dataclass(frozen=True)
class PacoteCriativo:
    """Saida do redator: tudo que voce precisa para gerar e postar um video."""

    prompt_veo: str
    gancho: str
    legenda: str
    hashtags: list[str]
    nome_arquivo: str
    fonte: str  # "gemini" ou "template" — util para depurar um briefing ruim


@dataclass
class RegistroVideo:
    id: str
    sku: str
    data: str
    combinacao_hash: str
    blocos_json: str
    gancho: str
    legenda: str
    hashtags: str
    arquivo: str
    status: str
    # O prompt fica no registro, nao so no markdown do dia: e o que o site
    # mostra no botao de copiar, e o que permite reabrir um briefing antigo.
    prompt: str = ""
    views: str = ""
    gmv: str = ""
