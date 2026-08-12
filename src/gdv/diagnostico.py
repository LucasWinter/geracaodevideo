"""`gdv doctor` — diz de uma vez o que falta para o pipeline rodar.

Sem isso, cada item faltando falha num momento diferente: voce descobre que nao
tem trilha depois de montar o primeiro video, e que o gancho nao renderizou
depois de assistir ao arquivo final.

As checagens sao funcoes puras que recebem caminhos e ambiente por parametro —
nada de estado global — para poderem ser testadas sem tocar na maquina real.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from . import blocos as mod_blocos
from . import montagem as mod_montagem
from .briefing import JANELA_PADRAO
from .catalogo import Catalogo, ErroCatalogo

OK, AVISO, ERRO = "ok", "aviso", "erro"

# SKUs que vem no repositorio. Se ainda estao la, o catalogo nao foi preenchido.
SKUS_EXEMPLO = {"BLS-001", "CAS-014"}

# Folga desejada entre o espaco combinatorio e a janela anti-repeticao. Abaixo
# disso o sorteio comeca a rejeitar muito antes de achar uma combinacao inedita.
FOLGA_MINIMA = 3


@dataclass(frozen=True)
class Checagem:
    nome: str
    nivel: str
    mensagem: str
    dica: str = ""


def _dica_ffmpeg() -> str:
    if sys.platform.startswith("win"):
        return "winget install Gyan.FFmpeg (e reabra o terminal para o PATH atualizar)"
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    return "sudo apt install ffmpeg"


def checar_ffmpeg() -> Checagem:
    faltando = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if faltando:
        return Checagem(
            "ffmpeg",
            ERRO,
            f"{', '.join(faltando)} nao encontrado no PATH — `gdv montar` nao roda",
            _dica_ffmpeg(),
        )
    return Checagem("ffmpeg", OK, "ffmpeg e ffprobe no PATH")


def checar_produtos(catalogo: Catalogo) -> list[Checagem]:
    try:
        produtos = catalogo.produtos()
    except ErroCatalogo as exc:
        return [
            Checagem(
                "produtos",
                ERRO,
                str(exc),
                f"confira o cabecalho e as colunas de {catalogo.caminho_produtos}",
            )
        ]

    ativos = [p for p in produtos if p.status == "ativo"]
    if not ativos:
        return [
            Checagem(
                "produtos",
                ERRO,
                f"nenhum produto com status=ativo em {catalogo.caminho_produtos}",
                "so produtos ativos entram no sorteio do briefing",
            )
        ]

    checagens = [
        Checagem("produtos", OK, f"{len(ativos)} produto(s) ativo(s) de {len(produtos)}")
    ]

    if {p.sku for p in ativos} & SKUS_EXEMPLO:
        checagens.append(
            Checagem(
                "catalogo preenchido",
                AVISO,
                "os SKUs de exemplo do repositorio ainda estao ativos",
                f"troque {catalogo.caminho_produtos} pelos seus produtos reais",
            )
        )

    sem_fotos = [p.sku for p in ativos if not p.pasta_drive]
    if sem_fotos:
        checagens.append(
            Checagem(
                "fotos",
                AVISO,
                f"sem pasta_drive: {', '.join(sem_fotos)}",
                "sem foto limpa do produto nao da para montar o frame inicial — "
                "e o que mais derruba a qualidade do image-to-video",
            )
        )

    return checagens


def checar_blocos(caminho: Path, catalogo: Catalogo, janela: int = JANELA_PADRAO) -> list[Checagem]:
    try:
        matriz = mod_blocos.carregar(caminho)
    except mod_blocos.ErroBlocos as exc:
        return [Checagem("blocos", ERRO, str(exc), f"corrija {caminho}")]

    checagens = [
        Checagem(
            "blocos",
            OK,
            f"matriz valida: {len(matriz.eixos)} eixos, "
            f"{sum(len(v) for v in matriz.eixos.values())} valores",
        )
    ]

    try:
        categorias = sorted({p.categoria for p in catalogo.produtos() if p.status == "ativo"})
    except ErroCatalogo:
        return checagens  # ja reportado por checar_produtos

    for categoria in categorias:
        espaco = matriz.espaco(categoria)
        if espaco < janela * FOLGA_MINIMA:
            checagens.append(
                Checagem(
                    f"espaco combinatorio ({categoria})",
                    AVISO,
                    f"{espaco} combinacoes para uma janela anti-repeticao de {janela}",
                    "adicione valores em blocos.yaml — com pouca folga o sorteio "
                    "passa a falhar com EspacoCombinatorioEsgotado",
                )
            )

    return checagens


def checar_llm(ambiente: dict[str, str] | None = None) -> Checagem:
    ambiente = os.environ if ambiente is None else ambiente

    if not ambiente.get("GEMINI_API_KEY", "").strip():
        return Checagem(
            "gemini",
            AVISO,
            "GEMINI_API_KEY ausente — os prompts saem em modo template",
            "opcional. Para ativar: copie .env.example para .env e preencha a chave",
        )

    if find_spec("google.genai") is None:
        return Checagem(
            "gemini",
            AVISO,
            "GEMINI_API_KEY definida mas a lib nao esta instalada — cai para template",
            'pip install -e ".[gemini]"',
        )

    return Checagem("gemini", OK, "GEMINI_API_KEY definida e google-genai instalado")


def checar_fonte(ambiente: dict[str, str] | None = None) -> Checagem:
    ambiente = os.environ if ambiente is None else ambiente
    preferida = ambiente.get("GDV_FONTE", "").strip() or None

    resolvida = mod_montagem.resolver_fonte(preferida)
    if resolvida:
        return Checagem("fonte", OK, f"overlay do gancho vai usar {resolvida}")

    detalhe = f"GDV_FONTE aponta para {preferida}, que nao existe" if preferida else (
        "nenhuma fonte conhecida encontrada"
    )
    return Checagem(
        "fonte",
        AVISO,
        f"{detalhe} — os videos sairao SEM o gancho na tela",
        r"defina GDV_FONTE no .env (ex.: C:\Windows\Fonts\arialbd.ttf)",
    )


def checar_trilhas(diretorio: Path) -> Checagem:
    trilhas = mod_montagem.listar_trilhas(diretorio)
    if not trilhas:
        return Checagem(
            "trilhas",
            AVISO,
            f"nenhuma trilha em {diretorio} — os videos sairao sem musica",
            f"coloque alguns {'/'.join(mod_montagem.EXTENSOES_TRILHA)} em {diretorio}; "
            "a trilha rotaciona por video e ajuda no fingerprint unico",
        )
    return Checagem("trilhas", OK, f"{len(trilhas)} trilha(s) em {diretorio}")


def checar_diretorio_gravavel(nome: str, caminho: Path) -> Checagem:
    alvo = caminho if caminho.exists() else caminho.parent
    while not alvo.exists() and alvo != alvo.parent:
        alvo = alvo.parent

    if os.access(alvo, os.W_OK):
        return Checagem(nome, OK, f"{caminho} gravavel")

    return Checagem(nome, AVISO, f"sem permissao de escrita em {alvo}", "ajuste as permissoes")


def diagnosticar(
    dados: Path | str = "data",
    entrada: Path | str = "entrada",
    saida: Path | str = "saida",
    trilhas: Path | str = "assets/audio",
    ambiente: dict[str, str] | None = None,
) -> list[Checagem]:
    catalogo = Catalogo(dados)

    return [
        checar_ffmpeg(),
        *checar_produtos(catalogo),
        *checar_blocos(Path(dados) / "blocos.yaml", catalogo),
        checar_llm(ambiente),
        checar_fonte(ambiente),
        checar_trilhas(Path(trilhas)),
        checar_diretorio_gravavel("entrada", Path(entrada)),
        checar_diretorio_gravavel("saida", Path(saida)),
    ]


def tem_erro(checagens: list[Checagem]) -> bool:
    """Exit code so olha erro: aviso e coisa opcional, nao trava script."""
    return any(c.nivel == ERRO for c in checagens)
