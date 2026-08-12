from __future__ import annotations

import pytest
import yaml

from gdv import blocos as mod_blocos
from gdv.catalogo import Catalogo, ErroCatalogo
from gdv.modelos import EIXOS, RegistroVideo


def _registro(id_: str, hash_: str, status: str = "briefado") -> RegistroVideo:
    return RegistroVideo(
        id=id_,
        sku="BLS-001",
        data="2026-08-12",
        combinacao_hash=hash_,
        blocos_json="{}",
        gancho="g",
        legenda="l",
        hashtags="#x",
        arquivo=f"BLS-001_2026-08-12_{hash_}.mp4",
        status=status,
    )


def test_le_produtos_e_filtra_inativos(catalogo, dados):
    linhas = (dados / "produtos.csv").read_text(encoding="utf-8").rstrip().split("\n")
    linhas.append("XXX-99,Produto Pausado,casa,10.00,0.3,,,frontal,pausado")
    (dados / "produtos.csv").write_text("\n".join(linhas) + "\n", encoding="utf-8")

    assert len(catalogo.produtos()) == 3
    assert [p.sku for p in catalogo.produtos_ativos()] == ["BLS-001", "CAS-014"]


def test_angulos_viram_lista(catalogo):
    bolsa = next(p for p in catalogo.produtos() if p.sku == "BLS-001")

    assert bolsa.angulos == ["frontal", "lateral", "interior", "detalhe-fecho"]


def test_registrar_faz_append_e_ids_seguem(catalogo):
    catalogo.registrar([_registro("1", "aaa")])
    catalogo.registrar([_registro(str(catalogo.proximo_id()), "bbb")])

    assert [v.id for v in catalogo.videos()] == ["1", "2"]
    assert catalogo.proximo_id() == 3


def test_hashes_recentes_respeita_a_janela(catalogo):
    catalogo.registrar([_registro(str(i), f"h{i:03d}") for i in range(1, 41)])

    recentes = catalogo.hashes_recentes(30)

    assert len(recentes) == 30
    assert recentes[-1] == "h040"
    assert "h010" not in recentes


def test_marcar_status_persiste(catalogo):
    catalogo.registrar([_registro("1", "aaa")])
    catalogo.marcar_status("1", "montado")

    assert Catalogo(catalogo.diretorio).buscar("1").status == "montado"


def test_status_invalido_e_recusado(catalogo):
    catalogo.registrar([_registro("1", "aaa")])

    with pytest.raises(ErroCatalogo, match="status invalido"):
        catalogo.marcar_status("1", "publicado")


def test_video_inexistente_e_erro_claro(catalogo):
    with pytest.raises(ErroCatalogo, match="nao encontrado"):
        catalogo.buscar("999")


def test_matriz_real_carrega_todos_os_eixos(matriz):
    assert set(matriz.eixos) == set(EIXOS)
    assert all(len(v) >= 2 for v in matriz.eixos.values())
    assert matriz.termos_proibidos


def test_ritmo_expoe_clipes_e_duracao(matriz):
    continuo = next(v for v in matriz.eixos["ritmo"] if v.id == "continuo_8s")

    assert continuo.extras["clipes"] == 1
    assert continuo.extras["duracao"] == 8


def test_id_duplicado_falha_no_load(tmp_path, dados):
    conteudo = yaml.safe_load((dados / "blocos.yaml").read_text(encoding="utf-8"))
    conteudo["eixos"]["camera"][1]["id"] = conteudo["eixos"]["camera"][0]["id"]
    alvo = tmp_path / "blocos.yaml"
    alvo.write_text(yaml.safe_dump(conteudo, allow_unicode=True), encoding="utf-8")

    with pytest.raises(mod_blocos.ErroBlocos, match="id duplicado"):
        mod_blocos.carregar(alvo)


def test_eixo_com_um_valor_falha_no_load(tmp_path, dados):
    conteudo = yaml.safe_load((dados / "blocos.yaml").read_text(encoding="utf-8"))
    conteudo["eixos"]["camera"] = conteudo["eixos"]["camera"][:1]
    alvo = tmp_path / "blocos.yaml"
    alvo.write_text(yaml.safe_dump(conteudo, allow_unicode=True), encoding="utf-8")

    with pytest.raises(mod_blocos.ErroBlocos, match="pelo menos 2 valores"):
        mod_blocos.carregar(alvo)


def test_eixo_ausente_falha_no_load(tmp_path, dados):
    conteudo = yaml.safe_load((dados / "blocos.yaml").read_text(encoding="utf-8"))
    del conteudo["eixos"]["iluminacao"]
    alvo = tmp_path / "blocos.yaml"
    alvo.write_text(yaml.safe_dump(conteudo, allow_unicode=True), encoding="utf-8")

    with pytest.raises(mod_blocos.ErroBlocos, match="eixos ausentes"):
        mod_blocos.carregar(alvo)
