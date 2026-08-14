from __future__ import annotations

import pytest
import yaml

from gdv import blocos as mod_blocos
from gdv.catalogo import Catalogo, ErroCatalogo
from gdv.modelos import EIXOS, Parametro, RegistroVideo


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


def test_categorias_da_matriz_sao_as_que_o_formulario_oferece(dados):
    """E a lista do <select> de categoria; sem ela a grafia vira adivinhacao."""
    matriz = mod_blocos.carregar(dados / "blocos.yaml")

    categorias = matriz.categorias()

    assert categorias == tuple(sorted(categorias))  # ordenada e sem repeticao
    assert "bolsas" in categorias
    # Toda categoria citada por algum valor precisa aparecer, senao um bloco
    # ficaria inalcancavel pelo formulario.
    citadas = {c for vals in matriz.eixos.values() for v in vals for c in v.categorias}
    assert set(categorias) == citadas


def test_categorias_vazias_quando_nenhum_bloco_restringe(tmp_path, dados):
    conteudo = yaml.safe_load((dados / "blocos.yaml").read_text(encoding="utf-8"))
    for valores in conteudo["eixos"].values():
        for valor in valores:
            valor.pop("categorias", None)
    alvo = tmp_path / "blocos.yaml"
    alvo.write_text(yaml.safe_dump(conteudo, allow_unicode=True), encoding="utf-8")

    assert mod_blocos.carregar(alvo).categorias() == ()


# ------------------------------------------------------------- parametros

def _parametro(**kwargs) -> Parametro:
    base = dict(tipo="eixo", eixo="cenario", chave="varanda", texto="varanda", en="balcony")
    return Parametro(**{**base, **kwargs})


def test_mesclar_soma_valor_ao_eixo(dados):
    matriz = mod_blocos.carregar(dados / "blocos.yaml")
    antes = len(matriz.eixos["cenario"])

    mesclada = mod_blocos.mesclar(matriz, [_parametro()])

    assert len(mesclada.eixos["cenario"]) == antes + 1
    assert mesclada.eixos["cenario"][-1].id == "varanda"
    assert mesclada.eixos["cenario"][-1].en == "balcony"


def test_mesclar_multiplica_o_espaco_combinatorio(dados):
    """E o efeito que importa: um valor a mais nao soma, multiplica."""
    matriz = mod_blocos.carregar(dados / "blocos.yaml")
    espaco = matriz.espaco("bolsas")

    mesclada = mod_blocos.mesclar(matriz, [_parametro()])

    assert mesclada.espaco("bolsas") > espaco


def test_yaml_ganha_de_parametro_com_id_repetido(dados):
    """O YAML e revisado no repositorio; a tabela qualquer um edita."""
    matriz = mod_blocos.carregar(dados / "blocos.yaml")
    existente = matriz.eixos["cenario"][0]

    mesclada = mod_blocos.mesclar(
        matriz, [_parametro(chave=existente.id, texto="outro", en="other")]
    )

    valores = [v for v in mesclada.eixos["cenario"] if v.id == existente.id]
    assert len(valores) == 1
    assert valores[0].texto == existente.texto


def test_mesclar_ignora_eixo_desconhecido(dados):
    """Eixo removido do codigo nao pode derrubar a geracao do dia."""
    matriz = mod_blocos.carregar(dados / "blocos.yaml")

    mesclada = mod_blocos.mesclar(matriz, [_parametro(eixo="eixo_que_nao_existe")])

    assert set(mesclada.eixos) == set(matriz.eixos)


def test_mesclar_ignora_parametro_que_nao_e_de_eixo(dados):
    matriz = mod_blocos.carregar(dados / "blocos.yaml")

    mesclada = mod_blocos.mesclar(
        matriz, [_parametro(tipo="categoria", eixo="", chave="petshop", texto="petshop")]
    )

    assert mesclada.eixos == matriz.eixos


def test_mesclar_sem_ingles_cai_para_o_portugues(dados):
    """Prompt pior que o ideal ainda e melhor que valor vazio no sorteio."""
    matriz = mod_blocos.carregar(dados / "blocos.yaml")

    mesclada = mod_blocos.mesclar(matriz, [_parametro(en="")])

    assert mesclada.eixos["cenario"][-1].en == "varanda"


def test_chave_derivada_e_estavel_e_sem_acento():
    assert mod_blocos.chave_de("Varanda ao Entardecer") == "varanda_ao_entardecer"
    assert mod_blocos.chave_de("café — luz de manhã") == "cafe_luz_de_manha"
    assert mod_blocos.chave_de("!!!") == ""


def test_catalogo_csv_nao_tem_parametros(catalogo):
    """No modo offline a matriz e exatamente o blocos.yaml."""
    assert catalogo.parametros() == []
