from __future__ import annotations

from pathlib import Path

import pytest

from gdv import diagnostico as diag
from gdv.catalogo import Catalogo

CABECALHO = "sku,nome,categoria,preco,margem,link_shop,pasta_drive,angulos,status\n"


def _por_nome(checagens, nome):
    return next(c for c in checagens if c.nome == nome)


def _escrever_produtos(dados: Path, *linhas: str) -> None:
    (dados / "produtos.csv").write_text(CABECALHO + "".join(linhas), encoding="utf-8")


# -------------------------------------------------------------------- ffmpeg

def test_ffmpeg_ausente_e_erro(monkeypatch):
    monkeypatch.setattr(diag.shutil, "which", lambda _: None)

    checagem = diag.checar_ffmpeg()

    assert checagem.nivel == diag.ERRO
    assert "ffmpeg" in checagem.dica


def test_ffmpeg_presente_e_ok(monkeypatch):
    monkeypatch.setattr(diag.shutil, "which", lambda nome: f"/usr/bin/{nome}")

    assert diag.checar_ffmpeg().nivel == diag.OK


def test_dica_de_ffmpeg_segue_a_plataforma(monkeypatch):
    monkeypatch.setattr(diag.shutil, "which", lambda _: None)

    monkeypatch.setattr(diag.sys, "platform", "win32")
    assert "winget" in diag.checar_ffmpeg().dica

    monkeypatch.setattr(diag.sys, "platform", "darwin")
    assert "brew" in diag.checar_ffmpeg().dica

    monkeypatch.setattr(diag.sys, "platform", "linux")
    assert "apt" in diag.checar_ffmpeg().dica


# ------------------------------------------------------------------ produtos

def test_sem_produto_ativo_e_erro(dados):
    _escrever_produtos(dados, "X-1,Pausado,casa,10,0.3,,link,frontal,pausado\n")

    checagem = _por_nome(diag.checar_produtos(Catalogo(dados)), "produtos")

    assert checagem.nivel == diag.ERRO
    assert "status=ativo" in checagem.mensagem


def test_csv_quebrado_e_erro(dados):
    (dados / "produtos.csv").write_text("sku,nome\nX-1,So Isso\n", encoding="utf-8")

    assert _por_nome(diag.checar_produtos(Catalogo(dados)), "produtos").nivel == diag.ERRO


def test_skus_de_exemplo_geram_aviso(dados, catalogo):
    checagens = diag.checar_produtos(catalogo)

    assert _por_nome(checagens, "produtos").nivel == diag.OK
    assert _por_nome(checagens, "catalogo preenchido").nivel == diag.AVISO


def test_catalogo_real_nao_avisa_de_exemplo(dados):
    _escrever_produtos(dados, "MEU-1,Produto Real,casa,10,0.3,,https://drive/x,frontal,ativo\n")

    nomes = [c.nome for c in diag.checar_produtos(Catalogo(dados))]

    assert "catalogo preenchido" not in nomes


def test_produto_sem_pasta_drive_gera_aviso(dados):
    _escrever_produtos(dados, "MEU-1,Sem Foto,casa,10,0.3,,,frontal,ativo\n")

    checagem = _por_nome(diag.checar_produtos(Catalogo(dados)), "fotos")

    assert checagem.nivel == diag.AVISO
    assert "MEU-1" in checagem.mensagem


# -------------------------------------------------------------------- blocos

def test_matriz_real_passa(dados, catalogo):
    checagens = diag.checar_blocos(dados / "blocos.yaml", catalogo)

    assert _por_nome(checagens, "blocos").nivel == diag.OK


def test_blocos_ausente_e_erro(dados, catalogo):
    assert _por_nome(
        diag.checar_blocos(dados / "nao_existe.yaml", catalogo), "blocos"
    ).nivel == diag.ERRO


def test_espaco_apertado_gera_aviso(dados, catalogo):
    """Janela grande demais para o espaco disponivel avisa antes de estourar."""
    checagens = diag.checar_blocos(dados / "blocos.yaml", catalogo, janela=100_000)

    apertados = [c for c in checagens if c.nome.startswith("espaco combinatorio")]

    assert apertados
    assert all(c.nivel == diag.AVISO for c in apertados)


def test_espaco_folgado_nao_avisa(dados, catalogo):
    checagens = diag.checar_blocos(dados / "blocos.yaml", catalogo, janela=30)

    assert not [c for c in checagens if c.nome.startswith("espaco combinatorio")]


# ----------------------------------------------------------------------- llm

def test_sem_chave_avisa_modo_template():
    checagem = diag.checar_llm({})

    assert checagem.nivel == diag.AVISO
    assert "template" in checagem.mensagem


def test_chave_sem_lib_avisa(monkeypatch):
    monkeypatch.setattr(diag, "find_spec", lambda _: None)

    checagem = diag.checar_llm({"GEMINI_API_KEY": "abc"})

    assert checagem.nivel == diag.AVISO
    assert "gemini" in checagem.dica


def test_chave_com_lib_e_ok(monkeypatch):
    monkeypatch.setattr(diag, "find_spec", lambda _: object())

    assert diag.checar_llm({"GEMINI_API_KEY": "abc"}).nivel == diag.OK


# --------------------------------------------------------------------- fonte

def test_fonte_ausente_avisa_que_o_gancho_some(monkeypatch):
    monkeypatch.setattr(diag.mod_montagem, "FONTES_PADRAO", ())

    checagem = diag.checar_fonte({})

    assert checagem.nivel == diag.AVISO
    assert "SEM o gancho" in checagem.mensagem
    assert "GDV_FONTE" in checagem.dica


def test_gdv_fonte_inexistente_e_nomeada_no_aviso(monkeypatch):
    monkeypatch.setattr(diag.mod_montagem, "FONTES_PADRAO", ())

    checagem = diag.checar_fonte({"GDV_FONTE": "/nao/existe.ttf"})

    assert "/nao/existe.ttf" in checagem.mensagem


def test_fonte_encontrada_e_ok(tmp_path, monkeypatch):
    fonte = tmp_path / "f.ttf"
    fonte.write_bytes(b"")
    monkeypatch.setattr(diag.mod_montagem, "FONTES_PADRAO", (str(fonte),))

    assert diag.checar_fonte({}).nivel == diag.OK


# ------------------------------------------------------------------- trilhas

def test_sem_trilha_avisa(tmp_path):
    checagem = diag.checar_trilhas(tmp_path)

    assert checagem.nivel == diag.AVISO
    assert "sem musica" in checagem.mensagem


def test_com_trilha_e_ok(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"")

    assert diag.checar_trilhas(tmp_path).nivel == diag.OK


def test_diretorio_inexistente_ainda_e_gravavel_pelo_pai(tmp_path):
    assert diag.checar_diretorio_gravavel("saida", tmp_path / "nao" / "existe").nivel == diag.OK


# ---------------------------------------------------------------- integracao

def test_diagnostico_completo_roda_e_reporta_niveis_validos(dados, tmp_path):
    checagens = diag.diagnosticar(
        dados=dados,
        entrada=tmp_path / "entrada",
        saida=tmp_path / "saida",
        trilhas=tmp_path / "audio",
        ambiente={},
    )

    assert checagens
    assert all(c.nivel in (diag.OK, diag.AVISO, diag.ERRO) for c in checagens)


def test_tem_erro_ignora_avisos():
    avisos = [diag.Checagem("x", diag.AVISO, "m")]

    assert not diag.tem_erro(avisos)
    assert diag.tem_erro(avisos + [diag.Checagem("y", diag.ERRO, "m")])


def test_doctor_sai_1_com_erro(dados, monkeypatch, capsys):
    from gdv.cli import main

    monkeypatch.setattr(diag.shutil, "which", lambda _: None)

    assert main(["--dados", str(dados), "doctor"]) == 1
    assert "[erro]" in capsys.readouterr().out


def test_doctor_sai_0_quando_so_ha_avisos(dados, tmp_path, monkeypatch, capsys):
    from gdv.cli import main

    monkeypatch.setattr(diag.shutil, "which", lambda nome: f"/usr/bin/{nome}")

    codigo = main([
        "--dados", str(dados), "doctor",
        "--entrada", str(tmp_path / "e"),
        "--saida", str(tmp_path / "s"),
        "--trilhas", str(tmp_path / "t"),
    ])

    saida = capsys.readouterr().out
    assert codigo == 0
    assert "[erro]" not in saida
    assert "[aviso]" in saida
