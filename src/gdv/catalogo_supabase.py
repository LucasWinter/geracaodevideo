"""Catalogo com Supabase (Postgres) como fonte de verdade.

Mesma superficie de `catalogo.Catalogo`, para que o motor nao perceba a
diferenca. Usado pelo site e, quando `GDV_BACKEND=supabase`, tambem pela CLI —
os dois precisam ver exatamente o mesmo log, senao a janela anti-repeticao de um
nao enxerga o que o outro gerou.

Autenticacao: anon key + login por e-mail/senha, com RLS ligada no banco. Nao ha
service-role key em lugar nenhum.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any

from .catalogo import ErroCatalogo
from .modelos import STATUS_VIDEO, Parametro, Produto, RegistroVideo

TABELA_PRODUTOS = "produtos"
TABELA_VIDEOS = "videos"
TABELA_PARAMETROS = "parametros"


def criar_cliente(
    url: str | None = None,
    chave: str | None = None,
    email: str | None = None,
    senha: str | None = None,
) -> Any:
    """Cria o cliente Supabase e autentica, se houver credenciais.

    Sem login a RLS bloqueia tudo — o erro apareceria como "nenhum produto",
    que e confuso. Por isso a falta de credencial e reportada aqui.
    """
    try:
        from supabase import create_client
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise ErroCatalogo(
            'biblioteca supabase nao instalada; rode: pip install -e "."'
        ) from exc

    url = url or os.environ.get("SUPABASE_URL", "").strip()
    chave = chave or os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not chave:
        raise ErroCatalogo(
            "SUPABASE_URL e SUPABASE_ANON_KEY sao obrigatorios com GDV_BACKEND=supabase; "
            "veja o .env.example"
        )

    cliente = create_client(url, chave)

    email = email if email is not None else os.environ.get("SUPABASE_EMAIL", "").strip()
    senha = senha if senha is not None else os.environ.get("SUPABASE_SENHA", "").strip()
    if not email or not senha:
        raise ErroCatalogo(
            "SUPABASE_EMAIL e SUPABASE_SENHA sao obrigatorios: a RLS exige sessao autenticada"
        )

    try:
        cliente.auth.sign_in_with_password({"email": email, "password": senha})
    except Exception as exc:
        raise ErroCatalogo(f"falha ao autenticar no Supabase: {exc}") from exc

    return cliente


class CatalogoSupabase:
    def __init__(self, cliente: Any) -> None:
        self.cliente = cliente

    @property
    def rotulo(self) -> str:
        return "Supabase"

    # ---------------------------------------------------------------- produtos

    def produtos(self) -> list[Produto]:
        linhas = self._selecionar(TABELA_PRODUTOS, ordem="sku")
        return [self._para_produto(linha) for linha in linhas]

    def produtos_ativos(self) -> list[Produto]:
        return [p for p in self.produtos() if p.status == "ativo"]

    def salvar_produto(self, produto: Produto) -> None:
        """Upsert pelo SKU. Nao existe no backend CSV — o site e quem usa."""
        self._executar(
            lambda: self.cliente.table(TABELA_PRODUTOS)
            .upsert(
                {
                    "sku": produto.sku,
                    "nome": produto.nome,
                    "categoria": produto.categoria,
                    "preco": produto.preco,
                    "margem": produto.margem,
                    "link_shop": produto.link_shop,
                    "pasta_drive": produto.pasta_drive,
                    "angulos": produto.angulos,
                    "status": produto.status,
                }
            )
            .execute()
        )

    def buscar_produto(self, sku: str) -> Produto | None:
        linhas = self._selecionar(TABELA_PRODUTOS, filtros={"sku": sku})
        return self._para_produto(linhas[0]) if linhas else None

    @staticmethod
    def _para_produto(linha: dict[str, Any]) -> Produto:
        return Produto(
            sku=linha["sku"],
            nome=linha["nome"],
            categoria=(linha["categoria"] or "").lower(),
            preco=float(linha["preco"] or 0),
            margem=float(linha["margem"] or 0),
            link_shop=linha.get("link_shop") or "",
            pasta_drive=linha.get("pasta_drive") or "",
            angulos=list(linha.get("angulos") or []),
            status=linha["status"],
        )

    # -------------------------------------------------------------- parametros

    def parametros(self) -> list[Parametro]:
        return [
            self._para_parametro(l) for l in self._selecionar(TABELA_PARAMETROS, ordem="id")
        ]

    def salvar_parametro(self, parametro: Parametro) -> None:
        """Upsert por (tipo, eixo, chave) — a mesma chave unica do banco.

        Nao existe no backend CSV: parametro e criado pelo painel, igual produto.
        """
        self._executar(
            lambda: self.cliente.table(TABELA_PARAMETROS)
            .upsert(
                {
                    "tipo": parametro.tipo,
                    "eixo": parametro.eixo,
                    "chave": parametro.chave,
                    "texto": parametro.texto,
                    "en": parametro.en,
                    "categorias": list(parametro.categorias),
                    "extras": parametro.extras,
                },
                on_conflict="tipo,eixo,chave",
            )
            .execute()
        )

    def remover_parametro(self, tipo: str, eixo: str, chave: str) -> None:
        self._executar(
            lambda: self.cliente.table(TABELA_PARAMETROS)
            .delete()
            .eq("tipo", tipo)
            .eq("eixo", eixo)
            .eq("chave", chave)
            .execute()
        )

    @staticmethod
    def _para_parametro(linha: dict[str, Any]) -> Parametro:
        return Parametro(
            tipo=linha["tipo"],
            eixo=linha.get("eixo") or "",
            chave=linha["chave"],
            texto=linha["texto"],
            en=linha.get("en") or "",
            categorias=tuple(linha.get("categorias") or []),
            extras=dict(linha.get("extras") or {}),
        )

    # ------------------------------------------------------------------ videos

    def videos(self) -> list[RegistroVideo]:
        """Ordem crescente, igual ao CSV append-only: o mais recente por ultimo."""
        return [self._para_video(l) for l in self._selecionar(TABELA_VIDEOS, ordem="id")]

    def hashes_recentes(self, n: int = 30) -> list[str]:
        linhas = self._executar(
            lambda: self.cliente.table(TABELA_VIDEOS)
            .select("combinacao_hash")
            .order("criado_em", desc=True)
            .order("id", desc=True)
            .limit(n)
            .execute()
        )
        return [l["combinacao_hash"] for l in reversed(linhas) if l.get("combinacao_hash")]

    def contagem_por_sku(self) -> Counter[str]:
        linhas = self._executar(
            lambda: self.cliente.table(TABELA_VIDEOS).select("sku").execute()
        )
        return Counter(l["sku"] for l in linhas)

    def proximo_id(self) -> int:
        """A coluna e identity; isto e so para exibir o proximo numero esperado."""
        linhas = self._executar(
            lambda: self.cliente.table(TABELA_VIDEOS)
            .select("id")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        return (int(linhas[0]["id"]) + 1) if linhas else 1

    def registrar(self, registros: list[RegistroVideo]) -> None:
        if not registros:
            return

        for registro in registros:
            if registro.status not in STATUS_VIDEO:
                raise ErroCatalogo(
                    f"status invalido {registro.status!r}; esperado um de {STATUS_VIDEO}"
                )

        # `id` fica de fora de proposito: quem numera e a identity do Postgres,
        # senao dois clientes gerando ao mesmo tempo colidiriam.
        self._executar(
            lambda: self.cliente.table(TABELA_VIDEOS)
            .insert(
                [
                    {
                        "sku": r.sku,
                        "data": r.data,
                        "combinacao_hash": r.combinacao_hash,
                        "blocos": json.loads(r.blocos_json or "{}"),
                        "gancho": r.gancho,
                        "legenda": r.legenda,
                        "hashtags": r.hashtags,
                        "prompt": r.prompt,
                        "arquivo": r.arquivo,
                        "status": r.status,
                    }
                    for r in registros
                ]
            )
            .execute()
        )

    def marcar_status(self, video_id: str, novo_status: str) -> RegistroVideo:
        if novo_status not in STATUS_VIDEO:
            raise ErroCatalogo(
                f"status invalido {novo_status!r}; esperado um de {STATUS_VIDEO}"
            )

        linhas = self._executar(
            lambda: self.cliente.table(TABELA_VIDEOS)
            .update({"status": novo_status})
            .eq("id", video_id)
            .execute()
        )
        if not linhas:
            raise ErroCatalogo(f"video {video_id!r} nao encontrado no Supabase")
        return self._para_video(linhas[0])

    def por_status(self, status: str) -> list[RegistroVideo]:
        return [
            self._para_video(l)
            for l in self._selecionar(TABELA_VIDEOS, filtros={"status": status}, ordem="id")
        ]

    def buscar(self, video_id: str) -> RegistroVideo:
        linhas = self._selecionar(TABELA_VIDEOS, filtros={"id": video_id})
        if not linhas:
            raise ErroCatalogo(f"video {video_id!r} nao encontrado no Supabase")
        return self._para_video(linhas[0])

    @staticmethod
    def _para_video(linha: dict[str, Any]) -> RegistroVideo:
        blocos = linha.get("blocos") or {}
        return RegistroVideo(
            id=str(linha["id"]),
            sku=linha["sku"],
            data=str(linha["data"]),
            combinacao_hash=linha.get("combinacao_hash") or "",
            blocos_json=json.dumps(blocos, sort_keys=True, ensure_ascii=False),
            gancho=linha.get("gancho") or "",
            legenda=linha.get("legenda") or "",
            hashtags=linha.get("hashtags") or "",
            prompt=linha.get("prompt") or "",
            arquivo=linha.get("arquivo") or "",
            status=linha["status"],
            views="" if linha.get("views") is None else str(linha["views"]),
            gmv="" if linha.get("gmv") is None else str(linha["gmv"]),
        )

    # ------------------------------------------------------------------- infra

    def _selecionar(
        self,
        tabela: str,
        filtros: dict[str, Any] | None = None,
        ordem: str | None = None,
    ) -> list[dict[str, Any]]:
        def consultar():
            consulta = self.cliente.table(tabela).select("*")
            for coluna, valor in (filtros or {}).items():
                consulta = consulta.eq(coluna, valor)
            if ordem:
                consulta = consulta.order(ordem)
            return consulta.execute()

        return self._executar(consultar)

    @staticmethod
    def _executar(acao) -> list[dict[str, Any]]:
        """Traduz falha de rede/RLS em ErroCatalogo, que a CLI ja sabe reportar."""
        try:
            resposta = acao()
        except ErroCatalogo:
            raise
        except Exception as exc:
            raise ErroCatalogo(f"consulta ao Supabase falhou: {exc}") from exc

        return list(getattr(resposta, "data", None) or [])
