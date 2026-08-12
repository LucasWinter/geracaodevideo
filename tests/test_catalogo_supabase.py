"""O adapter Supabase precisa honrar o mesmo contrato do backend CSV."""

from __future__ import annotations

import random

import pytest

from gdv import briefing as mod_briefing
from gdv.catalogo import ErroCatalogo
from gdv.catalogo_supabase import CatalogoSupabase
from gdv.modelos import Produto, RegistroVideo
from gdv.protocolo import ProtocoloCatalogo
from gdv.redator import RedatorTemplate

from falsos import ClienteFalso


@pytest.fixture
def cat() -> CatalogoSupabase:
    catalogo = CatalogoSupabase(ClienteFalso())
    catalogo.salvar_produto(
        Produto("BLS-001", "Bolsa", "bolsas", 89.9, 0.42, "", "https://drive/1", ["frontal"], "ativo")
    )
    catalogo.salvar_produto(
        Produto("CAS-014", "Caneca", "casa", 39.9, 0.55, "", "https://drive/2", ["topo"], "ativo")
    )
    return catalogo


def _registro(hash_: str, sku: str = "BLS-001", status: str = "briefado") -> RegistroVideo:
    return RegistroVideo(
        id="0",  # ignorado: quem numera e a identity do Postgres
        sku=sku,
        data="2026-08-12",
        combinacao_hash=hash_,
        blocos_json='{"camera": "orbit_lento"}',
        gancho="g",
        legenda="l",
        hashtags="#x",
        prompt="a slow orbit around the product",
        arquivo=f"{sku}_2026-08-12_{hash_}.mp4",
        status=status,
    )


def test_satisfaz_o_protocolo(cat):
    assert isinstance(cat, ProtocoloCatalogo)


def test_le_produtos_e_filtra_inativos(cat):
    cat.salvar_produto(Produto("OFF-1", "Pausado", "casa", 1, 0, "", "", [], "pausado"))

    assert len(cat.produtos()) == 3
    assert [p.sku for p in cat.produtos_ativos()] == ["BLS-001", "CAS-014"]


def test_upsert_atualiza_em_vez_de_duplicar(cat):
    cat.salvar_produto(
        Produto("BLS-001", "Bolsa Nova", "bolsas", 99.9, 0.5, "", "https://drive/1", [], "ativo")
    )

    assert len(cat.produtos()) == 2
    assert cat.buscar_produto("BLS-001").nome == "Bolsa Nova"
    assert cat.buscar_produto("BLS-001").preco == 99.9


def test_produto_inexistente_devolve_none(cat):
    assert cat.buscar_produto("NAO-EXISTE") is None


def test_banco_numera_os_videos(cat):
    cat.registrar([_registro("aaa"), _registro("bbb")])

    assert [v.id for v in cat.videos()] == ["1", "2"]
    assert cat.proximo_id() == 3


def test_proximo_id_em_catalogo_vazio(cat):
    assert cat.proximo_id() == 1


def test_prompt_sobrevive_a_ida_e_volta(cat):
    cat.registrar([_registro("aaa")])

    assert cat.videos()[0].prompt == "a slow orbit around the product"


def test_blocos_json_sobrevive_a_ida_e_volta(cat):
    cat.registrar([_registro("aaa")])

    assert cat.videos()[0].blocos_json == '{"camera": "orbit_lento"}'


def test_hashes_recentes_respeita_a_janela(cat):
    cat.registrar([_registro(f"h{i:03d}") for i in range(1, 41)])

    recentes = cat.hashes_recentes(30)

    assert len(recentes) == 30
    assert recentes[-1] == "h040"  # mais recente por ultimo, igual ao CSV
    assert "h010" not in recentes


def test_contagem_por_sku(cat):
    cat.registrar([_registro("a"), _registro("b"), _registro("c", sku="CAS-014")])

    assert cat.contagem_por_sku() == {"BLS-001": 2, "CAS-014": 1}


def test_marcar_status_persiste(cat):
    cat.registrar([_registro("aaa")])

    assert cat.marcar_status("1", "montado").status == "montado"
    assert cat.buscar("1").status == "montado"


def test_status_invalido_e_recusado(cat):
    cat.registrar([_registro("aaa")])

    with pytest.raises(ErroCatalogo, match="status invalido"):
        cat.marcar_status("1", "publicado")


def test_registrar_com_status_invalido_e_recusado(cat):
    with pytest.raises(ErroCatalogo, match="status invalido"):
        cat.registrar([_registro("aaa", status="quase")])


def test_video_inexistente_e_erro_claro(cat):
    with pytest.raises(ErroCatalogo, match="nao encontrado"):
        cat.buscar("999")


def test_por_status_filtra(cat):
    cat.registrar([_registro("a"), _registro("b")])
    cat.marcar_status("2", "gerado")

    assert [v.id for v in cat.por_status("gerado")] == ["2"]
    assert [v.id for v in cat.por_status("briefado")] == ["1"]


def test_registrar_lista_vazia_nao_faz_nada(cat):
    cat.registrar([])

    assert cat.videos() == []


def test_falha_de_rede_vira_erro_catalogo(cat, monkeypatch):
    def explodir(*_, **__):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(cat.cliente, "table", explodir)

    with pytest.raises(ErroCatalogo, match="consulta ao Supabase falhou"):
        cat.produtos()


# -------------------------------------------------------------- integracao

def test_briefing_completo_roda_sobre_o_supabase(cat, matriz):
    """O motor nao deve perceber a troca de backend."""
    itens, registros = mod_briefing.montar(
        cat, matriz, RedatorTemplate(matriz.termos_proibidos),
        data="2026-08-12", qtd=4, rng=random.Random(42),
    )
    cat.registrar(registros)

    assert len(cat.videos()) == 4
    assert len({v.combinacao_hash for v in cat.videos()}) == 4
    assert all(v.prompt for v in cat.videos())


def test_dia_seguinte_nao_repete_combinacao(cat, matriz):
    redator = RedatorTemplate(matriz.termos_proibidos)

    _, ontem = mod_briefing.montar(
        cat, matriz, redator, data="2026-08-12", qtd=5, rng=random.Random(1)
    )
    cat.registrar(ontem)

    _, hoje = mod_briefing.montar(
        cat, matriz, redator, data="2026-08-13", qtd=5, rng=random.Random(1)
    )

    assert not {r.combinacao_hash for r in hoje} & {r.combinacao_hash for r in ontem}
