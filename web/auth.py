"""Sessao do painel, via Supabase Auth.

O token do usuario e repassado ao PostgREST em cada requisicao, entao a RLS
avalia as consultas como aquele usuario. O servidor nao guarda estado: a sessao
inteira vive nos cookies httpOnly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response

from gdv.catalogo_supabase import CatalogoSupabase

COOKIE_ACESSO = "gdv_at"
COOKIE_REFRESH = "gdv_rt"

# 30 dias: o access token expira antes disso, mas o refresh renova sozinho.
DURACAO_COOKIE = 60 * 60 * 24 * 30


class ErroAutenticacao(Exception):
    pass


@dataclass(frozen=True)
class Sessao:
    access_token: str
    refresh_token: str
    email: str


def _config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    chave = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not chave:
        raise ErroAutenticacao(
            "O painel ainda não foi configurado: faltam SUPABASE_URL e "
            "SUPABASE_ANON_KEY. Quem administra o sistema precisa defini-las."
        )
    return url, chave


def _novo_cliente() -> Any:
    from supabase import create_client

    url, chave = _config()
    return create_client(url, chave)


def entrar(email: str, senha: str) -> Sessao:
    cliente = _novo_cliente()
    try:
        resposta = cliente.auth.sign_in_with_password({"email": email, "password": senha})
    except Exception as exc:
        raise ErroAutenticacao("E-mail ou senha incorretos. Confira e tente de novo.") from exc

    sessao = getattr(resposta, "session", None)
    if not sessao or not sessao.access_token:
        raise ErroAutenticacao("E-mail ou senha incorretos. Confira e tente de novo.")

    return Sessao(
        access_token=sessao.access_token,
        refresh_token=sessao.refresh_token or "",
        email=getattr(resposta.user, "email", email),
    )


def pedir_recuperacao(email: str, url_retorno: str) -> None:
    """Dispara o e-mail de redefinicao do Supabase.

    Nao devolve nada e nao distingue e-mail cadastrado de inexistente — de
    proposito. Responder "esse e-mail nao existe" transformaria a tela num
    verificador de quem tem conta. O Supabase so envia para quem existe, entao a
    exigencia de "so para usuarios cadastrados" ja esta atendida pelo servidor.
    """
    cliente = _novo_cliente()
    try:
        cliente.auth.reset_password_for_email(email, {"redirect_to": url_retorno})
    except Exception:
        # Falha de SMTP tambem fica silenciosa aqui pelo mesmo motivo; o log da
        # Vercel guarda o erro para quem for depurar.
        pass


def redefinir_com_token(acesso: str, refresh: str, nova_senha: str) -> Sessao:
    """Troca a senha usando o token que veio no link do e-mail."""
    cliente = _novo_cliente()
    try:
        cliente.auth.set_session(acesso, refresh or "")
        resposta = cliente.auth.update_user({"password": nova_senha})
    except Exception as exc:
        raise ErroAutenticacao(
            "Este link de recuperação expirou ou já foi usado. Peça um novo "
            "e-mail de recuperação."
        ) from exc

    usuario = getattr(resposta, "user", None)
    return Sessao(acesso, refresh, getattr(usuario, "email", "") or "")


def trocar_senha(acesso: str, refresh: str, nova_senha: str) -> None:
    """Troca a senha de quem ja esta logado.

    E o caminho do time: o admin cria a conta com senha padrao, a pessoa entra e
    troca aqui — sem depender de e-mail, que no Supabase gratuito tem limite
    apertado de envio.
    """
    cliente = _novo_cliente()
    try:
        cliente.auth.set_session(acesso, refresh or "")
        cliente.auth.update_user({"password": nova_senha})
    except Exception as exc:
        raise ErroAutenticacao(f"Não consegui trocar a senha: {exc}") from exc


def gravar_cookies(resposta: Response, sessao: Sessao) -> None:
    comum = {
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "max_age": DURACAO_COOKIE,
        "path": "/",
    }
    resposta.set_cookie(COOKIE_ACESSO, sessao.access_token, **comum)
    resposta.set_cookie(COOKIE_REFRESH, sessao.refresh_token, **comum)


def limpar_cookies(resposta: Response) -> None:
    resposta.delete_cookie(COOKIE_ACESSO, path="/")
    resposta.delete_cookie(COOKIE_REFRESH, path="/")


def catalogo_da_requisicao(request: Request) -> CatalogoSupabase | None:
    """Catalogo autenticado como o usuario do cookie, ou None se nao ha sessao."""
    acesso = request.cookies.get(COOKIE_ACESSO)
    refresh = request.cookies.get(COOKIE_REFRESH)
    if not acesso:
        return None

    try:
        cliente = _novo_cliente()
        cliente.auth.set_session(acesso, refresh or "")
        # Garante o Authorization no PostgREST mesmo se a versao da lib nao
        # propagar sozinha — sem isso a RLS trataria tudo como anonimo.
        cliente.postgrest.auth(acesso)
    except Exception:
        return None  # token expirado ou invalido: middleware manda para /login

    return CatalogoSupabase(cliente)
