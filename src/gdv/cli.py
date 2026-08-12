"""Entrypoint da CLI: `gdv`."""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from datetime import date
from pathlib import Path

from . import blocos as mod_blocos
from . import briefing as mod_briefing
from . import diagnostico as mod_diagnostico
from . import montagem as mod_montagem
from .catalogo import Catalogo, ErroCatalogo
from .modelos import STATUS_VIDEO
from .redator import criar_redator
from .sorteio import EspacoCombinatorioEsgotado


def _carregar_dotenv(caminho: Path = Path(".env")) -> None:
    """Le o .env sem depender de python-dotenv. Nao sobrescreve o ambiente."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))


def cmd_briefing(args: argparse.Namespace) -> int:
    catalogo = Catalogo(args.dados)
    matriz = mod_blocos.carregar(Path(args.dados) / "blocos.yaml")
    redator = criar_redator(matriz.termos_proibidos, forcar_template=args.sem_llm)

    data = args.data or date.today().isoformat()
    rng = random.Random(args.seed) if args.seed is not None else random.Random()

    itens, registros = mod_briefing.montar(
        catalogo=catalogo,
        matriz=matriz,
        redator=redator,
        data=data,
        qtd=args.qtd,
        rng=rng,
        janela=args.janela,
    )

    texto = mod_briefing.renderizar(itens, registros, data)

    if args.dry_run:
        print(texto)
        print(f"[dry-run] nada gravado em {catalogo.caminho_videos}", file=sys.stderr)
        return 0

    caminho = mod_briefing.salvar(texto, data, args.saida)
    catalogo.registrar(registros)
    print(f"Briefing: {caminho}")
    print(f"{len(registros)} vídeos registrados em {catalogo.caminho_videos}")
    return 0


def cmd_montar(args: argparse.Namespace) -> int:
    catalogo = Catalogo(args.dados)

    if args.id:
        alvos = [catalogo.buscar(args.id)]
    else:
        alvos = catalogo.por_status("gerado")
        if not alvos:
            print("Nenhum vídeo com status 'gerado'.", file=sys.stderr)
            print("Marque os que você já baixou do Flow: gdv status <id> gerado", file=sys.stderr)
            return 1

    falhas = 0
    for registro in alvos:
        try:
            arquivo = mod_montagem.montar_video(
                registro,
                entrada=args.entrada,
                saida=args.saida,
                trilhas=args.trilhas,
                fonte=os.environ.get("GDV_FONTE"),
            )
        except mod_montagem.ErroMontagem as exc:
            print(f"[{registro.id}] {exc}", file=sys.stderr)
            falhas += 1
            continue

        catalogo.marcar_status(registro.id, "montado")
        print(f"[{registro.id}] {arquivo}")

    return 1 if falhas else 0


def cmd_status(args: argparse.Namespace) -> int:
    registro = Catalogo(args.dados).marcar_status(args.id, args.novo_status)
    print(f"[{registro.id}] {registro.arquivo} -> {registro.status}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    checagens = mod_diagnostico.diagnosticar(
        dados=args.dados,
        entrada=args.entrada,
        saida=args.saida,
        trilhas=args.trilhas,
    )

    marca = {
        mod_diagnostico.OK: "[ok]   ",
        mod_diagnostico.AVISO: "[aviso]",
        mod_diagnostico.ERRO: "[erro] ",
    }

    for checagem in checagens:
        print(f"{marca[checagem.nivel]} {checagem.nome}: {checagem.mensagem}")
        if checagem.dica and checagem.nivel != mod_diagnostico.OK:
            print(f"          → {checagem.dica}")

    erros = sum(1 for c in checagens if c.nivel == mod_diagnostico.ERRO)
    avisos = sum(1 for c in checagens if c.nivel == mod_diagnostico.AVISO)

    print()
    if erros:
        print(f"{erros} erro(s) e {avisos} aviso(s). Os erros impedem o pipeline de rodar.")
        return 1

    if avisos:
        print(f"{avisos} aviso(s), nenhum erro. Dá para rodar; os avisos são qualidade.")
    else:
        print("Tudo pronto.")
    return 0


def cmd_catalogo(args: argparse.Namespace) -> int:
    catalogo = Catalogo(args.dados)
    contagens = catalogo.contagem_por_sku()

    print(f"{'SKU':<12} {'CAT':<12} {'PREÇO':>9} {'VÍDEOS':>7}  NOME")
    for produto in catalogo.produtos():
        marca = "" if produto.status == "ativo" else f"  [{produto.status}]"
        print(
            f"{produto.sku:<12} {produto.categoria:<12} "
            f"{produto.preco:>9.2f} {contagens.get(produto.sku, 0):>7}  "
            f"{produto.nome}{marca}"
        )

    videos = catalogo.videos()
    if videos:
        resumo = {s: sum(1 for v in videos if v.status == s) for s in STATUS_VIDEO}
        print("\n" + "  ".join(f"{s}: {n}" for s, n in resumo.items() if n))
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gdv",
        description="Pipeline de geração de vídeos para TikTok Shop.",
    )
    parser.add_argument("--dados", default="data", help="diretório do catálogo (padrão: data)")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra logs de diagnóstico")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("briefing", help="sorteia e redige os vídeos do dia")
    p.add_argument("--qtd", type=int, default=5, help="quantos vídeos (padrão: 5)")
    p.add_argument("--data", help="data do briefing em YYYY-MM-DD (padrão: hoje)")
    p.add_argument("--seed", type=int, help="fixa o sorteio — reproduz um briefing")
    p.add_argument(
        "--janela",
        type=int,
        default=mod_briefing.JANELA_PADRAO,
        help="quantas combinações recentes evitar (padrão: 30)",
    )
    p.add_argument("--sem-llm", action="store_true", help="redige por template, sem chamar o Gemini")
    p.add_argument("--dry-run", action="store_true", help="imprime sem gravar no catálogo")
    p.add_argument("--saida", default="saida/briefing", help="onde salvar o markdown")
    p.set_defaults(func=cmd_briefing)

    p = sub.add_parser("montar", help="monta, diferencia e exporta os vídeos gerados")
    p.add_argument("--id", help="monta só este vídeo (padrão: todos com status 'gerado')")
    p.add_argument("--entrada", default="entrada", help="clipes baixados do Flow")
    p.add_argument("--saida", default="saida/videos", help="destino dos arquivos finais")
    p.add_argument("--trilhas", default="assets/audio", help="pasta de trilhas")
    p.set_defaults(func=cmd_montar)

    p = sub.add_parser("status", help="atualiza o status de um vídeo")
    p.add_argument("id")
    p.add_argument("novo_status", choices=STATUS_VIDEO)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="checa o que falta para o pipeline rodar")
    p.add_argument("--entrada", default="entrada")
    p.add_argument("--saida", default="saida")
    p.add_argument("--trilhas", default="assets/audio")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("catalogo", help="lista produtos e o resumo do log")
    p.set_defaults(func=cmd_catalogo)

    return parser


def main(argv: list[str] | None = None) -> int:
    _carregar_dotenv()
    args = construir_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        return args.func(args)
    except (ErroCatalogo, mod_blocos.ErroBlocos, EspacoCombinatorioEsgotado, ValueError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
