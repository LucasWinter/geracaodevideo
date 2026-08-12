"""Camada 5 — montagem, diferenciacao e export.

Esta e a etapa que mais protege a conta e e 100% automatizavel: cada video sai
com fingerprint proprio (velocidade, crop, curva de cor e trilha distintas, mais
metadados limpos), sem que a diferenca seja perceptivel para quem assiste.

A variacao e derivada do hash da combinacao, nao de `random`: reprocessar o mesmo
video da exatamente o mesmo arquivo.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .modelos import RegistroVideo

LARGURA, ALTURA = 1080, 1920
FPS = 30

FONTE_PADRAO = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
EXTENSOES_TRILHA = (".mp3", ".m4a", ".aac", ".wav")


class ErroMontagem(Exception):
    pass


@dataclass(frozen=True)
class Variacao:
    """Diferenciacao sutil, deterministica por video."""

    velocidade: float  # 0.97 – 1.03
    crop: float  # 0.01 – 0.03 (fracao removida de cada dimensao)
    contraste: float  # 0.97 – 1.03
    saturacao: float  # 0.97 – 1.05


def verificar_ffmpeg() -> None:
    faltando = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if faltando:
        raise ErroMontagem(
            f"{', '.join(faltando)} nao encontrado no PATH. "
            f"Instale com `sudo apt install ffmpeg` (Debian/Ubuntu) ou `brew install ffmpeg` (macOS)."
        )


def parametros_variacao(chave: str) -> Variacao:
    """Deriva a variacao do hash da combinacao — reproduzivel, nunca aleatoria."""
    digest = hashlib.sha256(chave.encode("utf-8")).digest()
    return Variacao(
        velocidade=round(0.97 + (digest[0] % 61) / 1000, 3),
        crop=round(0.01 + (digest[1] % 21) / 1000, 3),
        contraste=round(0.97 + (digest[2] % 61) / 1000, 3),
        saturacao=round(0.97 + (digest[3] % 81) / 1000, 3),
    )


def localizar_clipes(diretorio: Path, arquivo: str) -> list[Path]:
    """Acha os clipes baixados do Flow.

    Aceita `SKU_data_hash.mp4` (clipe unico) ou `SKU_data_hash_1.mp4`,
    `_2.mp4`, ... para os ritmos de multiplos clipes.
    """
    diretorio = Path(diretorio)
    base = Path(arquivo).stem

    unico = diretorio / f"{base}.mp4"
    if unico.exists():
        return [unico]

    partes = sorted(diretorio.glob(f"{base}_*.mp4"))
    if partes:
        return partes

    raise ErroMontagem(
        f"nenhum clipe encontrado em {diretorio} para '{base}'. "
        f"Salve o download do Flow como {base}.mp4 (ou {base}_1.mp4, {base}_2.mp4)."
    )


def tem_audio(caminho: Path) -> bool:
    """Clipe do Flow pode vir mudo; concatenar com a=1 nesse caso quebra."""
    saida = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "json",
            str(caminho),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(json.loads(saida.stdout or "{}").get("streams"))


def selecionar_trilha(trilhas: Sequence[Path], video_id: str) -> Path | None:
    """Rotaciona as faixas pelo id do video: consecutivos nunca repetem trilha."""
    if not trilhas:
        return None
    ordenadas = sorted(trilhas)
    try:
        indice = int(video_id)
    except ValueError:
        indice = int(hashlib.sha256(video_id.encode()).hexdigest()[:8], 16)
    return ordenadas[indice % len(ordenadas)]


def listar_trilhas(diretorio: Path | str) -> list[Path]:
    diretorio = Path(diretorio)
    if not diretorio.is_dir():
        return []
    return [p for p in sorted(diretorio.iterdir()) if p.suffix.lower() in EXTENSOES_TRILHA]


def escapar_drawtext(texto: str) -> str:
    """drawtext interpreta \\ : ' % — sem escapar, um gancho com ':' quebra o filtro."""
    for alvo, substituto in (
        ("\\", r"\\"),
        (":", r"\:"),
        ("'", r"\'"),
        ("%", r"\%"),
        (",", r"\,"),
    ):
        texto = texto.replace(alvo, substituto)
    return texto


def construir_comando(
    clipes: Sequence[Path],
    saida: Path,
    variacao: Variacao,
    gancho: str = "",
    trilha: Path | None = None,
    fonte: str | None = None,
    audio_original: bool = False,
) -> list[str]:
    """Monta a lista de argumentos do ffmpeg.

    Lista, nunca string de shell: gancho e nome de arquivo vem de dados do
    usuario e nao podem virar comando.
    """
    if not clipes:
        raise ErroMontagem("nenhum clipe para montar")

    entradas: list[str] = []
    for clipe in clipes:
        entradas += ["-i", str(clipe)]
    if trilha is not None:
        entradas += ["-i", str(trilha)]
    indice_trilha = len(clipes)

    filtros: list[str] = []

    # Normaliza cada clipe antes de concatenar: o filtro concat exige mesma
    # resolucao, SAR e framerate, e clipes do Flow divergem em timebase.
    for i in range(len(clipes)):
        filtros.append(
            f"[{i}:v]"
            f"crop=iw*{1 - variacao.crop:.4f}:ih*{1 - variacao.crop:.4f},"
            f"scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,"
            f"crop={LARGURA}:{ALTURA},"
            f"setsar=1,fps={FPS}"
            f"[v{i}]"
        )

    rotulos_v = "".join(f"[v{i}]" for i in range(len(clipes)))

    if audio_original:
        rotulos = "".join(f"[v{i}][{i}:a]" for i in range(len(clipes)))
        filtros.append(f"{rotulos}concat=n={len(clipes)}:v=1:a=1[vc][ac]")
    else:
        filtros.append(f"{rotulos_v}concat=n={len(clipes)}:v=1:a=0[vc]")

    # Diferenciacao: velocidade + curva de cor leve.
    cadeia_v = (
        f"[vc]setpts=PTS/{variacao.velocidade},"
        f"eq=contrast={variacao.contraste}:saturation={variacao.saturacao}"
    )

    caminho_fonte = fonte or FONTE_PADRAO
    if gancho and Path(caminho_fonte).exists():
        cadeia_v += (
            f",drawtext=fontfile={caminho_fonte}"
            f":text='{escapar_drawtext(gancho)}'"
            f":fontcolor=white:fontsize=54:line_spacing=10"
            f":borderw=4:bordercolor=black@0.7"
            f":x=(w-text_w)/2:y=h*0.16"
            f":enable='between(t,0.4,3.4)'"
        )
    cadeia_v += "[vout]"
    filtros.append(cadeia_v)

    mapeamentos = ["-map", "[vout]"]

    if audio_original and trilha is not None:
        # Original em primeiro plano, trilha por baixo. `duration=first` evita
        # que uma musica longa estique o video.
        filtros.append(
            f"[ac]atempo={variacao.velocidade}[ao];"
            f"[{indice_trilha}:a]volume=0.25[am];"
            f"[ao][am]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        mapeamentos += ["-map", "[aout]"]
    elif audio_original:
        filtros.append(f"[ac]atempo={variacao.velocidade}[aout]")
        mapeamentos += ["-map", "[aout]"]
    elif trilha is not None:
        mapeamentos += ["-map", f"{indice_trilha}:a", "-shortest"]

    tem_saida_audio = audio_original or trilha is not None

    comando = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *entradas,
        "-filter_complex", ";".join(filtros),
        *mapeamentos,
        "-c:v", "libx264",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", "20",
        "-preset", "medium",
        "-r", str(FPS),
    ]

    if tem_saida_audio:
        comando += ["-c:a", "aac", "-b:a", "128k"]
    else:
        comando += ["-an"]

    # Limpa metadados: nada de tags do Flow ou do editor no arquivo publicado.
    comando += ["-map_metadata", "-1", "-movflags", "+faststart", str(saida)]

    return comando


def montar_video(
    registro: RegistroVideo,
    entrada: Path | str = "entrada",
    saida: Path | str = "saida/videos",
    trilhas: Path | str = "assets/audio",
    fonte: str | None = None,
) -> Path:
    """Monta um video do catalogo. Devolve o caminho do arquivo final."""
    verificar_ffmpeg()

    clipes = localizar_clipes(Path(entrada), registro.arquivo)
    destino = Path(saida)
    destino.mkdir(parents=True, exist_ok=True)
    arquivo_final = destino / registro.arquivo

    comando = construir_comando(
        clipes=clipes,
        saida=arquivo_final,
        variacao=parametros_variacao(registro.combinacao_hash or registro.id),
        gancho=registro.gancho,
        trilha=selecionar_trilha(listar_trilhas(trilhas), registro.id),
        fonte=fonte,
        # concat com a=1 exige audio em TODOS os clipes.
        audio_original=all(tem_audio(c) for c in clipes),
    )

    resultado = subprocess.run(comando, capture_output=True, text=True)
    if resultado.returncode != 0:
        raise ErroMontagem(
            f"ffmpeg falhou no video {registro.id} (codigo {resultado.returncode}):\n"
            f"{resultado.stderr.strip()}"
        )

    return arquivo_final
