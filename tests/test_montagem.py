"""Camada 5 sem ffmpeg instalado: valida a lista de argumentos construida."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdv.montagem import (
    ALTURA,
    LARGURA,
    ErroMontagem,
    construir_comando,
    escapar_drawtext,
    localizar_clipes,
    parametros_variacao,
    selecionar_trilha,
)


@pytest.fixture
def variacao():
    return parametros_variacao("abc123def456")


def _valor_de(comando: list[str], flag: str) -> str:
    return comando[comando.index(flag) + 1]


def test_variacao_e_deterministica():
    assert parametros_variacao("abc") == parametros_variacao("abc")
    assert parametros_variacao("abc") != parametros_variacao("abd")


def test_variacao_fica_na_faixa_impercebivel():
    for chave in (f"h{i}" for i in range(200)):
        v = parametros_variacao(chave)
        assert 0.97 <= v.velocidade <= 1.03
        assert 0.01 <= v.crop <= 0.03
        assert 0.97 <= v.contraste <= 1.03
        assert 0.97 <= v.saturacao <= 1.05


def test_comando_exporta_vertical_e_limpa_metadados(variacao, tmp_path):
    comando = construir_comando([tmp_path / "a.mp4"], tmp_path / "out.mp4", variacao)
    filtro = _valor_de(comando, "-filter_complex")

    assert f"scale={LARGURA}:{ALTURA}" in filtro
    assert f"crop={LARGURA}:{ALTURA}" in filtro
    assert _valor_de(comando, "-map_metadata") == "-1"
    assert _valor_de(comando, "-movflags") == "+faststart"
    assert _valor_de(comando, "-pix_fmt") == "yuv420p"
    assert comando[-1] == str(tmp_path / "out.mp4")


def test_comando_aplica_a_diferenciacao(variacao, tmp_path):
    comando = construir_comando([tmp_path / "a.mp4"], tmp_path / "out.mp4", variacao)
    filtro = _valor_de(comando, "-filter_complex")

    assert f"setpts=PTS/{variacao.velocidade}" in filtro
    assert f"contrast={variacao.contraste}" in filtro
    assert f"saturation={variacao.saturacao}" in filtro
    assert f"crop=iw*{1 - variacao.crop:.4f}" in filtro


def test_multiplos_clipes_sao_concatenados(variacao, tmp_path):
    clipes = [tmp_path / "a_1.mp4", tmp_path / "a_2.mp4"]
    comando = construir_comando(clipes, tmp_path / "out.mp4", variacao)
    filtro = _valor_de(comando, "-filter_complex")

    assert comando.count("-i") == 2
    assert "[v0][v1]concat=n=2:v=1:a=0[vc]" in filtro


def test_clipe_mudo_nao_pede_audio_no_concat(variacao, tmp_path):
    comando = construir_comando(
        [tmp_path / "a.mp4"], tmp_path / "out.mp4", variacao, audio_original=False
    )
    filtro = _valor_de(comando, "-filter_complex")

    assert ":a=0[vc]" in filtro
    assert "-an" in comando


def test_audio_original_e_ajustado_junto_com_a_velocidade(variacao, tmp_path):
    comando = construir_comando(
        [tmp_path / "a.mp4"], tmp_path / "out.mp4", variacao, audio_original=True
    )
    filtro = _valor_de(comando, "-filter_complex")

    assert ":a=1[vc][ac]" in filtro
    assert f"atempo={variacao.velocidade}" in filtro
    assert "-an" not in comando


def test_trilha_em_clipe_mudo_vira_a_faixa_de_audio(variacao, tmp_path):
    trilha = tmp_path / "musica.mp3"
    comando = construir_comando(
        [tmp_path / "a.mp4"], tmp_path / "out.mp4", variacao,
        trilha=trilha, audio_original=False,
    )

    assert str(trilha) in comando
    assert "-shortest" in comando
    assert _valor_de(comando, "-c:a") == "aac"


def test_trilha_com_audio_original_e_mixada_por_baixo(variacao, tmp_path):
    comando = construir_comando(
        [tmp_path / "a.mp4"], tmp_path / "out.mp4", variacao,
        trilha=tmp_path / "musica.mp3", audio_original=True,
    )
    filtro = _valor_de(comando, "-filter_complex")

    assert "volume=0.25" in filtro
    assert "amix=inputs=2:duration=first" in filtro


def test_gancho_vira_overlay_quando_a_fonte_existe(variacao, tmp_path):
    fonte = tmp_path / "fonte.ttf"
    fonte.write_bytes(b"")

    comando = construir_comando(
        [tmp_path / "a.mp4"], tmp_path / "out.mp4", variacao,
        gancho="POV: achei por R$ 89,90", fonte=str(fonte),
    )
    filtro = _valor_de(comando, "-filter_complex")

    assert "drawtext=" in filtro
    assert r"POV\: achei por R$ 89\,90" in filtro


def test_sem_fonte_o_video_sai_sem_overlay_em_vez_de_falhar(variacao, tmp_path):
    comando = construir_comando(
        [tmp_path / "a.mp4"], tmp_path / "out.mp4", variacao,
        gancho="qualquer coisa", fonte=str(tmp_path / "inexistente.ttf"),
    )

    assert "drawtext=" not in _valor_de(comando, "-filter_complex")


def test_escape_protege_os_metacaracteres_do_drawtext():
    assert escapar_drawtext("a:b'c%d,e") == r"a\:b\'c\%d\,e"


def test_sem_clipe_e_erro_claro(variacao, tmp_path):
    with pytest.raises(ErroMontagem, match="nenhum clipe"):
        construir_comando([], tmp_path / "out.mp4", variacao)


def test_localiza_clipe_unico(tmp_path):
    (tmp_path / "BLS-001_2026-08-12_abc.mp4").write_bytes(b"")

    achados = localizar_clipes(tmp_path, "BLS-001_2026-08-12_abc.mp4")

    assert [p.name for p in achados] == ["BLS-001_2026-08-12_abc.mp4"]


def test_localiza_clipes_numerados_em_ordem(tmp_path):
    for i in (2, 1):
        (tmp_path / f"BLS-001_2026-08-12_abc_{i}.mp4").write_bytes(b"")

    achados = localizar_clipes(tmp_path, "BLS-001_2026-08-12_abc.mp4")

    assert [p.name for p in achados] == [
        "BLS-001_2026-08-12_abc_1.mp4",
        "BLS-001_2026-08-12_abc_2.mp4",
    ]


def test_clipe_ausente_diz_como_nomear(tmp_path):
    with pytest.raises(ErroMontagem, match="Salve o download do Flow"):
        localizar_clipes(tmp_path, "BLS-001_2026-08-12_abc.mp4")


def test_trilha_rotaciona_entre_videos_consecutivos(tmp_path):
    trilhas = [tmp_path / f"{n}.mp3" for n in ("a", "b", "c")]

    escolhidas = [selecionar_trilha(trilhas, str(i)).name for i in range(1, 7)]

    assert escolhidas == ["b.mp3", "c.mp3", "a.mp3", "b.mp3", "c.mp3", "a.mp3"]


def test_sem_trilha_disponivel_devolve_none():
    assert selecionar_trilha([], "1") is None


def test_montagem_exige_ffmpeg(monkeypatch):
    import gdv.montagem as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: None)

    with pytest.raises(ErroMontagem, match="apt install ffmpeg"):
        mod.verificar_ffmpeg()
