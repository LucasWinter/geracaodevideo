"""Camada 1 — catalogo.

Toda I/O de arquivo do projeto mora aqui. Nenhum outro modulo abre CSV: e esse
contrato que um futuro `CatalogoSheets` implementaria sem tocar no motor.
"""

from __future__ import annotations

import csv
import os
import tempfile
from collections import Counter
from pathlib import Path

from .modelos import STATUS_VIDEO, Produto, RegistroVideo

COLUNAS_PRODUTOS = [
    "sku",
    "nome",
    "categoria",
    "preco",
    "margem",
    "link_shop",
    "pasta_drive",
    "angulos",
    "status",
]

COLUNAS_VIDEOS = [
    "id",
    "sku",
    "data",
    "combinacao_hash",
    "blocos_json",
    "gancho",
    "legenda",
    "hashtags",
    "arquivo",
    "status",
    "views",
    "gmv",
]


class ErroCatalogo(Exception):
    pass


class Catalogo:
    def __init__(self, diretorio: Path | str = "data") -> None:
        self.diretorio = Path(diretorio)
        self.caminho_produtos = self.diretorio / "produtos.csv"
        self.caminho_videos = self.diretorio / "videos.csv"

    # ---------------------------------------------------------------- produtos

    def produtos(self) -> list[Produto]:
        if not self.caminho_produtos.exists():
            raise ErroCatalogo(f"catalogo de produtos nao encontrado: {self.caminho_produtos}")

        with self.caminho_produtos.open(newline="", encoding="utf-8") as fh:
            leitor = csv.DictReader(fh)
            faltando = set(COLUNAS_PRODUTOS) - set(leitor.fieldnames or [])
            if faltando:
                raise ErroCatalogo(
                    f"{self.caminho_produtos} sem as colunas {sorted(faltando)}"
                )
            return [self._para_produto(linha) for linha in leitor if linha.get("sku")]

    def produtos_ativos(self) -> list[Produto]:
        return [p for p in self.produtos() if p.status == "ativo"]

    @staticmethod
    def _para_produto(linha: dict[str, str]) -> Produto:
        sku = linha["sku"].strip()
        try:
            preco = float(linha["preco"] or 0)
            margem = float(linha["margem"] or 0)
        except ValueError as exc:
            raise ErroCatalogo(f"preco/margem invalido no SKU {sku}: {exc}") from exc

        return Produto(
            sku=sku,
            nome=linha["nome"].strip(),
            categoria=linha["categoria"].strip().lower(),
            preco=preco,
            margem=margem,
            link_shop=linha["link_shop"].strip(),
            pasta_drive=linha["pasta_drive"].strip(),
            angulos=[a.strip() for a in linha["angulos"].split(";") if a.strip()],
            status=linha["status"].strip().lower(),
        )

    # ------------------------------------------------------------------ videos

    def videos(self) -> list[RegistroVideo]:
        if not self.caminho_videos.exists():
            return []

        with self.caminho_videos.open(newline="", encoding="utf-8") as fh:
            return [
                RegistroVideo(**{coluna: linha.get(coluna, "") or "" for coluna in COLUNAS_VIDEOS})
                for linha in csv.DictReader(fh)
                if linha.get("id")
            ]

    def hashes_recentes(self, n: int = 30) -> list[str]:
        """Janela anti-repeticao: os n hashes mais recentes do log.

        O log e append-only, entao as ultimas linhas sao as mais recentes.
        """
        return [v.combinacao_hash for v in self.videos()[-n:] if v.combinacao_hash]

    def contagem_por_sku(self) -> Counter[str]:
        return Counter(v.sku for v in self.videos())

    def proximo_id(self) -> int:
        maior = 0
        for video in self.videos():
            try:
                maior = max(maior, int(video.id))
            except ValueError:
                continue  # id fora do padrao numerico nao participa da sequencia
        return maior + 1

    def registrar(self, registros: list[RegistroVideo]) -> None:
        """Append no log. Reescrita atomica para nao corromper se o processo morrer."""
        if not registros:
            return

        for registro in registros:
            if registro.status not in STATUS_VIDEO:
                raise ErroCatalogo(
                    f"status invalido {registro.status!r}; esperado um de {STATUS_VIDEO}"
                )

        existentes = self.videos()
        self._escrever_videos(existentes + registros)

    def marcar_status(self, video_id: str, novo_status: str) -> RegistroVideo:
        if novo_status not in STATUS_VIDEO:
            raise ErroCatalogo(
                f"status invalido {novo_status!r}; esperado um de {STATUS_VIDEO}"
            )

        registros = self.videos()
        for registro in registros:
            if registro.id == video_id:
                registro.status = novo_status
                self._escrever_videos(registros)
                return registro

        raise ErroCatalogo(f"video {video_id!r} nao encontrado em {self.caminho_videos}")

    def por_status(self, status: str) -> list[RegistroVideo]:
        return [v for v in self.videos() if v.status == status]

    def buscar(self, video_id: str) -> RegistroVideo:
        for registro in self.videos():
            if registro.id == video_id:
                return registro
        raise ErroCatalogo(f"video {video_id!r} nao encontrado em {self.caminho_videos}")

    def _escrever_videos(self, registros: list[RegistroVideo]) -> None:
        self.diretorio.mkdir(parents=True, exist_ok=True)

        # Grava num temporario no mesmo diretorio e troca de nome: os.replace e
        # atomico no mesmo filesystem, entao o log nunca fica meio escrito.
        fd, tmp = tempfile.mkstemp(dir=self.diretorio, prefix=".videos-", suffix=".csv")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
                escritor = csv.DictWriter(fh, fieldnames=COLUNAS_VIDEOS)
                escritor.writeheader()
                for registro in registros:
                    escritor.writerow({c: getattr(registro, c) for c in COLUNAS_VIDEOS})
            os.replace(tmp, self.caminho_videos)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
