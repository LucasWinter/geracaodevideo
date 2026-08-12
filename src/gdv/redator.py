"""Camada 2 — redacao do pacote criativo.

A estrutura criativa ja foi decidida pelo sorteio. Aqui o unico trabalho e
redigir: transformar a combinacao em prompt do Veo (ingles), gancho, legenda e
hashtags.

Dois adapters:
  RedatorTemplate  deterministico, offline, sem dependencia externa. E o piso.
  RedatorGemini    texto mais natural. Cai para o template em qualquer falha —
                   um dia sem briefing por causa de um 429 e pior que um prompt
                   de template.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Protocol

from .modelos import Combinacao, PacoteCriativo, Produto

log = logging.getLogger(__name__)

MODELO_PADRAO = "gemini-flash-latest"

INSTRUCAO_SISTEMA = """You write short-form product video prompts for TikTok Shop.

You will receive a product and a creative combination that was already drawn from
a matrix. Your job is ONLY to phrase it well. Do not invent a different scene,
camera move, lighting or pacing than the one given — those were chosen on purpose.

Rules:
- `prompt_veo` must be in English, one paragraph, present tense, describing a
  single continuous shot (or the given number of shots). Describe the real
  product as given; never invent brand names, logos or text on the product.
- Never state or imply an efficacy claim (whitening, curing, treating, slimming,
  guaranteed or time-bound results). This is a TikTok Shop policy hard rule and
  applies to `prompt_veo`, `gancho` and `legenda` alike.
- `gancho` and `legenda` must be in Brazilian Portuguese, casual, first person,
  no emoji in `gancho`.
- `legenda` is at most 150 characters.
- `hashtags` is 4 to 6 tags, lowercase, no `#` prefix.
"""

ESQUEMA_RESPOSTA = {
    "type": "object",
    "properties": {
        "prompt_veo": {"type": "string"},
        "gancho": {"type": "string"},
        "legenda": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["prompt_veo", "gancho", "legenda", "hashtags"],
}


def _normalizar(texto: str) -> str:
    """Minusculas sem acento — o filtro de claim nao pode depender de acentuacao."""
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def validar_claims(texto: str, termos_proibidos: tuple[str, ...]) -> list[str]:
    """Devolve os termos proibidos encontrados no texto.

    Instrucao de prompt nao e controle — este filtro e. Casa por palavra inteira
    para "cura" nao acusar em "curadoria".
    """
    alvo = _normalizar(texto)
    achados = []

    for termo in termos_proibidos:
        padrao = r"\b" + r"\s+".join(re.escape(p) for p in _normalizar(termo).split()) + r"\b"
        if re.search(padrao, alvo):
            achados.append(termo)

    return achados


def nome_arquivo(produto: Produto, data: str, hash_combinacao: str) -> str:
    """Casa o arquivo baixado do Flow com a linha em videos.csv."""
    return f"{produto.sku}_{data}_{hash_combinacao}.mp4"


def _gancho(produto: Produto, combinacao: Combinacao) -> str:
    return combinacao["gancho_pov"].texto.format(
        preco=f"{produto.preco:.2f}".replace(".", ","),
        nome=produto.nome,
    )


def _hashtags(produto: Produto) -> list[str]:
    base = ["tiktokshop", "achadinhos", "achadinhostiktok"]
    slug = re.sub(r"[^a-z0-9]", "", _normalizar(produto.categoria))
    return base + [slug] if slug else base


class Redator(Protocol):
    def redigir(self, produto: Produto, combinacao: Combinacao, data: str) -> PacoteCriativo: ...


class RedatorTemplate:
    """Piso deterministico: mesma entrada, mesma saida, sem rede."""

    def __init__(self, termos_proibidos: tuple[str, ...] = ()) -> None:
        self.termos_proibidos = termos_proibidos

    def redigir(self, produto: Produto, combinacao: Combinacao, data: str) -> PacoteCriativo:
        ritmo = combinacao["ritmo"]
        prompt = (
            f"{ritmo.en}. {combinacao['gancho_pov'].en}. "
            f"The product is a {produto.nome}, shown exactly as in the reference image, "
            f"with no invented branding or text. "
            f"Setting: {combinacao['cenario'].en}. "
            f"Camera: {combinacao['camera'].en}. "
            f"Lighting: {combinacao['iluminacao'].en}. "
            f"Include {combinacao['detalhe_close'].en}. "
            f"Vertical 9:16, photoreal, no on-screen text, no dialogue."
        )

        gancho = _gancho(produto, combinacao)
        legenda = f"{gancho} — link do produto na vitrine 🛍️"

        pacote = PacoteCriativo(
            prompt_veo=prompt,
            gancho=gancho,
            legenda=legenda,
            hashtags=_hashtags(produto),
            nome_arquivo=nome_arquivo(produto, data, combinacao.hash),
            fonte="template",
        )

        # O template so monta texto vindo de blocos.yaml e do catalogo. Se algo
        # proibido apareceu, veio de um desses arquivos e precisa ser corrigido
        # na fonte — nao ha para onde cair.
        achados = validar_claims(
            f"{prompt} {gancho} {legenda}", self.termos_proibidos
        )
        if achados:
            raise ValueError(
                f"termo proibido {achados} no texto de template do SKU {produto.sku}; "
                f"corrija blocos.yaml ou o nome do produto em produtos.csv"
            )

        return pacote


class RedatorGemini:
    """Redige via Gemini, com queda para template em qualquer falha."""

    def __init__(
        self,
        api_key: str,
        termos_proibidos: tuple[str, ...] = (),
        modelo: str = MODELO_PADRAO,
    ) -> None:
        from google import genai  # import tardio: dependencia opcional

        self.cliente = genai.Client(api_key=api_key)
        self.modelo = modelo
        self.termos_proibidos = termos_proibidos
        self.reserva = RedatorTemplate(termos_proibidos)

    def redigir(self, produto: Produto, combinacao: Combinacao, data: str) -> PacoteCriativo:
        try:
            dados = self._chamar(produto, combinacao)
        except Exception as exc:  # rede, cota, JSON malformado, schema violado
            log.warning("Gemini falhou para %s (%s); usando template", produto.sku, exc)
            return self.reserva.redigir(produto, combinacao, data)

        texto = f"{dados['prompt_veo']} {dados['gancho']} {dados['legenda']}"
        achados = validar_claims(texto, self.termos_proibidos)
        if achados:
            log.warning(
                "Gemini devolveu termo proibido %s para %s; usando template",
                achados,
                produto.sku,
            )
            return self.reserva.redigir(produto, combinacao, data)

        return PacoteCriativo(
            prompt_veo=dados["prompt_veo"].strip(),
            gancho=dados["gancho"].strip(),
            legenda=dados["legenda"].strip(),
            hashtags=[t.strip().lstrip("#") for t in dados["hashtags"] if t.strip()],
            nome_arquivo=nome_arquivo(produto, data, combinacao.hash),
            fonte="gemini",
        )

    def _chamar(self, produto: Produto, combinacao: Combinacao) -> dict:
        from google.genai import types

        briefing = {
            "produto": {
                "nome": produto.nome,
                "categoria": produto.categoria,
                "preco_brl": produto.preco,
                "angulos_disponiveis": produto.angulos,
            },
            "combinacao": {
                eixo: {"texto_pt": valor.texto, "descricao_en": valor.en}
                for eixo, valor in combinacao.valores.items()
            },
            "clipes": combinacao.clipes,
            "duracao_por_clipe_s": combinacao.duracao,
            "gancho_sugerido_pt": _gancho(produto, combinacao),
        }

        resposta = self.cliente.models.generate_content(
            model=self.modelo,
            contents=json.dumps(briefing, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=INSTRUCAO_SISTEMA,
                response_mime_type="application/json",
                response_schema=ESQUEMA_RESPOSTA,
                temperature=1.0,
            ),
        )

        dados = json.loads(resposta.text)
        faltando = set(ESQUEMA_RESPOSTA["required"]) - set(dados)
        if faltando:
            raise ValueError(f"resposta sem os campos {sorted(faltando)}")
        if not dados["hashtags"]:
            raise ValueError("resposta sem hashtags")
        return dados


def criar_redator(
    termos_proibidos: tuple[str, ...] = (),
    forcar_template: bool = False,
    api_key: str | None = None,
) -> Redator:
    """Escolhe o redator disponivel. Sem chave, o pipeline segue offline."""
    if forcar_template:
        return RedatorTemplate(termos_proibidos)

    chave = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
    if not chave:
        log.info("GEMINI_API_KEY ausente; redigindo por template")
        return RedatorTemplate(termos_proibidos)

    try:
        return RedatorGemini(chave, termos_proibidos)
    except ImportError:
        log.warning("google-genai nao instalado (pip install '.[gemini]'); usando template")
        return RedatorTemplate(termos_proibidos)
