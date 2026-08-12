"""Entrypoint ASGI para a Vercel.

A Vercel serve funcoes a partir de /api. O pacote `gdv` vive em src/, entao
entra no sys.path antes do import do app.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
for caminho in (RAIZ, RAIZ / "src"):
    if str(caminho) not in sys.path:
        sys.path.insert(0, str(caminho))

from web.main import app  # noqa: E402

__all__ = ["app"]
