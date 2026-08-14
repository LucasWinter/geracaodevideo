"""Tipos compartilhados por todas as camadas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Ordem canonica dos eixos da matriz. Usada na validacao de blocos.yaml e na
# apresentacao do briefing; o hash de combinacao ordena por conta propria.
EIXOS = ("gancho_pov", "cenario", "camera", "iluminacao", "detalhe_close", "ritmo")

STATUS_PRODUTO = ("ativo", "pausado", "esgotado")
STATUS_VIDEO = ("briefado", "gerado", "montado", "postado")

# Rotulo e explicacao de cada status, para o painel. O valor gravado continua
# sendo a chave — quem le a tela nao precisa saber o que "briefado" quer dizer,
# e "briefado" sozinho nao diz a ninguem em que ponto do fluxo o video esta.
ROTULO_STATUS_PRODUTO: dict[str, tuple[str, str]] = {
    "ativo": ("Ativo", "Entra no sorteio do briefing."),
    "pausado": ("Pausado", "Fica no catálogo, mas não recebe vídeo novo."),
    "esgotado": ("Sem estoque", "Não recebe vídeo novo enquanto não voltar ao ar."),
}

ROTULO_STATUS_VIDEO: dict[str, tuple[str, str]] = {
    "briefado": ("A gerar", "O texto está pronto. Falta gerar o vídeo no Flow."),
    "gerado": ("Gerado", "O clipe foi baixado. Falta montar com `gdv montar`."),
    "montado": ("Montado", "O vídeo final está pronto para subir no TikTok."),
    "postado": ("Postado", "Publicado no TikTok Shop."),
}

# Angulos de foto que o redator recebe como `angulos_disponiveis`. Diferente de
# EIXOS e STATUS_*, esta lista nao valida nada: e so a sugestao que o formulario
# oferece como caixas de selecao. Produto com angulo fora dela continua valido —
# o campo livre do formulario existe justamente para isso.
#
# Cada um vem com rotulo e explicacao porque "frontal" sozinho nao diz a quem
# fotografa o que precisa estar no quadro, nem por que aquele angulo importa.
ANGULOS_DETALHADOS: tuple[tuple[str, str, str], ...] = (
    ("frontal", "Frontal",
     "O produto de frente, inteiro e centralizado. É a referência de formato e "
     "proporção — sem ela o frame inicial erra o desenho do produto."),
    ("lateral", "Lateral",
     "De perfil, mostrando profundidade e espessura. Importa em bolsa, caixa e "
     "tudo que parece mais fino de frente do que é."),
    ("traseira", "Traseira",
     "As costas do produto. Vale quando há alça, etiqueta ou acabamento que só "
     "aparece por trás."),
    ("superior", "De cima (flat lay)",
     "Visto de cima, apoiado. É o enquadramento que combina com cenário de mesa "
     "e o mais fácil de reproduzir em casa."),
    ("detalhe-textura", "Close na textura",
     "Trama, verniz, granulado. É o que comunica qualidade numa tela de celular, "
     "onde o produto inteiro fica pequeno demais para julgar."),
    ("detalhe-fecho", "Close no fecho",
     "Zíper, botão ou trava — de preferência sendo operado. Movimento no close "
     "prova que funciona, foto parada não."),
    ("em-uso", "Em uso",
     "Alguém usando de verdade, rosto fora do quadro. Dá contexto e escala ao "
     "mesmo tempo, e é o que mais aproxima do formato nativo do TikTok."),
    ("escala-mao", "Escala com a mão",
     "A mão ao lado do produto para dar noção de tamanho. Resolve a dúvida que "
     "mais aparece nos comentários."),
    ("interior", "Interior",
     "Por dentro: compartimentos, forro, capacidade. Decisivo em bolsa, "
     "necessaire e organizador."),
    ("embalagem", "Embalagem",
     "Como o produto chega ao cliente. É o insumo do unboxing."),
)

ANGULOS_SUGERIDOS = tuple(id_ for id_, _, _ in ANGULOS_DETALHADOS)

# O que cada eixo da matriz controla. Serve as telas de Matriz e Parametros:
# o nome tecnico do eixo nao diz a quem cadastra o que ele muda no video.
DESCRICAO_EIXO: dict[str, tuple[str, str]] = {
    "gancho_pov": (
        "Gancho e ponto de vista",
        "Como o vídeo abre e de que ângulo humano. É o que segura o dedo nos "
        "dois primeiros segundos — o eixo que mais decide se alguém assiste.",
    ),
    "cenario": (
        "Cenário",
        "Onde a cena acontece: superfície, fundo e clima. Cenário errado para a "
        "categoria destoa mais que qualquer outro erro.",
    ),
    "camera": (
        "Movimento de câmera",
        "Como a câmera se desloca. Define se o produto é revelado, contornado ou "
        "acompanhado — e o quanto o vídeo parece feito à mão ou produzido.",
    ),
    "iluminacao": (
        "Iluminação",
        "A luz da cena. Muda a percepção de qualidade do produto mais que "
        "qualquer outro eixo, com o mesmo produto e o mesmo cenário.",
    ),
    "detalhe_close": (
        "Detalhe em close",
        "O que ganha close no meio do vídeo. É o momento em que o produto prova "
        "o acabamento, e o que separa vídeo de catálogo de vídeo que vende.",
    ),
    "ritmo": (
        "Ritmo",
        "Quantos clipes e de que duração. Define se o vídeo é um take contínuo "
        "ou uma sequência cortada — e quantos clipes você precisa baixar do Flow.",
    ),
}


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
    # Explicacao em portugues, so para quem le o painel. Nao entra no prompt nem
    # no hash: existe para o valor novo nascer tao explicado quanto os de fabrica.
    descricao: str = ""
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
