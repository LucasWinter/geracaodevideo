"""Contrato do catalogo.

Duas implementacoes o satisfazem: `catalogo.Catalogo` (CSV local) e
`catalogo_supabase.CatalogoSupabase` (Postgres). O motor — briefing, sorteio,
montagem — depende so deste protocolo, nunca de uma delas.
"""

from __future__ import annotations

from collections import Counter
from typing import Protocol, runtime_checkable

from .modelos import Produto, RegistroVideo


@runtime_checkable
class ProtocoloCatalogo(Protocol):
    @property
    def rotulo(self) -> str:
        """Onde os dados vivem, para mensagens ao usuario (caminho ou 'Supabase')."""
        ...

    def produtos(self) -> list[Produto]: ...

    def produtos_ativos(self) -> list[Produto]: ...

    def videos(self) -> list[RegistroVideo]: ...

    def hashes_recentes(self, n: int = 30) -> list[str]: ...

    def contagem_por_sku(self) -> Counter[str]: ...

    def proximo_id(self) -> int: ...

    def registrar(self, registros: list[RegistroVideo]) -> None: ...

    def marcar_status(self, video_id: str, novo_status: str) -> RegistroVideo: ...

    def por_status(self, status: str) -> list[RegistroVideo]: ...

    def buscar(self, video_id: str) -> RegistroVideo: ...
