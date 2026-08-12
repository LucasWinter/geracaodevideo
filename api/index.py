"""Entrypoint ASGI para a Vercel.

A Vercel serve funcoes a partir de /api. O pacote `gdv` vive em src/, entao
entra no sys.path antes do import do app — necessario quando o projeto nao foi
instalado no ambiente da funcao.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
for caminho in (RAIZ, RAIZ / "src"):
    if str(caminho) not in sys.path:
        sys.path.insert(0, str(caminho))

try:
    from web.main import app
except ModuleNotFoundError as exc:  # pragma: no cover - so acontece em deploy quebrado
    # Sem isto, a unica pista seria um ModuleNotFoundError cru no log da Vercel,
    # que nao diz se faltou dependencia ou se o arquivo nao entrou no bundle.
    existentes = sorted(p.name for p in RAIZ.iterdir()) if RAIZ.is_dir() else []
    raise RuntimeError(
        f"nao consegui importar o painel: falta o modulo '{exc.name}'.\n"
        f"Se for uma dependencia (fastapi, jinja2, supabase, google.genai), confira se ela esta "
        f"em [project].dependencies do pyproject.toml — a Vercel nao instala extras.\n"
        f"Se for 'web' ou 'gdv', o arquivo nao entrou no bundle: confira includeFiles no "
        f"vercel.json.\n"
        f"Raiz={RAIZ} contem {existentes}"
    ) from exc

__all__ = ["app"]
