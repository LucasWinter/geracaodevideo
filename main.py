"""Entrypoint no caminho convencional da Vercel.

A deteccao de framework procura o app num punhado de nomes conhecidos —
`main`, `app`, `index`, `server`, na raiz ou em `src/`. Um modulo em `web/` nao
esta nessa lista, e apontar `[tool.vercel] entrypoint` para ele nao foi
suficiente: o deploy subia sem funcao nenhuma e respondia 404 em toda rota.

Este arquivo existe so para ficar onde a Vercel olha. A logica toda continua em
`web/asgi.py`, que traz o fallback de diagnostico, e em `web/main.py`.
"""

from web.asgi import app

__all__ = ["app"]
