"""Cliente Supabase falso, em memoria — os testes nao tocam a rede.

Implementa so o pedaco do encadeamento postgrest que `CatalogoSupabase` usa:
table().select()/insert()/update()/upsert().eq().order().limit().execute().
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any


@dataclass
class Resposta:
    data: list[dict[str, Any]]


class ConsultaFalsa:
    def __init__(self, tabela: "TabelaFalsa", operacao: str, carga: Any = None) -> None:
        self.tabela = tabela
        self.operacao = operacao
        self.carga = carga
        self.filtros: list[tuple[str, Any]] = []
        self.ordens: list[tuple[str, bool]] = []
        self.limite: int | None = None

    def eq(self, coluna: str, valor: Any) -> "ConsultaFalsa":
        self.filtros.append((coluna, valor))
        return self

    def order(self, coluna: str, desc: bool = False) -> "ConsultaFalsa":
        self.ordens.append((coluna, desc))
        return self

    def limit(self, n: int) -> "ConsultaFalsa":
        self.limite = n
        return self

    def _casam(self, linha: dict[str, Any]) -> bool:
        # Compara como texto: o PostgREST aceita eq('id', '3') com coluna bigint.
        return all(str(linha.get(coluna)) == str(valor) for coluna, valor in self.filtros)

    def execute(self) -> Resposta:
        if self.operacao == "insert":
            return Resposta(self.tabela.inserir(self.carga))
        if self.operacao == "upsert":
            return Resposta(self.tabela.aplicar_upsert(self.carga))
        if self.operacao == "update":
            alvos = [l for l in self.tabela.linhas if self._casam(l)]
            for linha in alvos:
                linha.update(self.carga)
            return Resposta([dict(l) for l in alvos])

        linhas = [dict(l) for l in self.tabela.linhas if self._casam(l)]
        for coluna, desc in reversed(self.ordens):
            linhas.sort(key=lambda l: l.get(coluna), reverse=desc)
        if self.limite is not None:
            linhas = linhas[: self.limite]
        return Resposta(linhas)


class TabelaFalsa:
    def __init__(self, nome: str, chave: str) -> None:
        self.nome = nome
        self.chave = chave
        self.linhas: list[dict[str, Any]] = []
        self.sequencia = itertools.count(1)

    def select(self, *_: Any) -> ConsultaFalsa:
        return ConsultaFalsa(self, "select")

    def insert(self, carga: Any) -> ConsultaFalsa:
        return ConsultaFalsa(self, "insert", carga)

    def upsert(self, carga: Any) -> ConsultaFalsa:
        return ConsultaFalsa(self, "upsert", carga)

    def update(self, carga: Any) -> ConsultaFalsa:
        return ConsultaFalsa(self, "update", carga)

    def inserir(self, carga: Any) -> list[dict[str, Any]]:
        itens = carga if isinstance(carga, list) else [carga]
        criadas = []
        for item in itens:
            linha = dict(item)
            if self.chave == "id":
                # Espelha a identity do Postgres: quem numera e o banco.
                linha["id"] = next(self.sequencia)
                linha.setdefault("criado_em", f"2026-08-12T00:00:{linha['id']:02d}Z")
            self.linhas.append(linha)
            criadas.append(dict(linha))
        return criadas

    def aplicar_upsert(self, carga: Any) -> list[dict[str, Any]]:
        itens = carga if isinstance(carga, list) else [carga]
        salvas = []
        for item in itens:
            existente = next(
                (l for l in self.linhas if l.get(self.chave) == item.get(self.chave)), None
            )
            if existente:
                existente.update(item)
                salvas.append(dict(existente))
            else:
                self.linhas.append(dict(item))
                salvas.append(dict(item))
        return salvas


class ClienteFalso:
    def __init__(self) -> None:
        self.tabelas = {
            "produtos": TabelaFalsa("produtos", "sku"),
            "videos": TabelaFalsa("videos", "id"),
        }

    def table(self, nome: str) -> TabelaFalsa:
        return self.tabelas[nome]
