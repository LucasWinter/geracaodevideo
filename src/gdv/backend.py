"""Escolha do backend de catalogo: CSV local ou Supabase."""

from __future__ import annotations

import os
from pathlib import Path

from .catalogo import Catalogo
from .protocolo import ProtocoloCatalogo

BACKENDS = ("csv", "supabase")


def backend_ativo(ambiente: dict[str, str] | None = None) -> str:
    ambiente = os.environ if ambiente is None else ambiente
    return (ambiente.get("GDV_BACKEND") or "csv").strip().lower()


def abrir_catalogo(
    dados: Path | str = "data",
    ambiente: dict[str, str] | None = None,
) -> ProtocoloCatalogo:
    escolhido = backend_ativo(ambiente)

    if escolhido == "supabase":
        # Import tardio: quem usa CSV nao precisa da lib instalada.
        from .catalogo_supabase import CatalogoSupabase, criar_cliente

        return CatalogoSupabase(criar_cliente())

    if escolhido != "csv":
        raise ValueError(f"GDV_BACKEND={escolhido!r} invalido; use um de {BACKENDS}")

    return Catalogo(dados)
