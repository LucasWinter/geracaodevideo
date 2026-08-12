# geracaodevideo

Pipeline para gerar vídeos de produto para TikTok Shop em volume, sem que eles saiam todos com
a mesma cara.

O problema que isso resolve: pedir "gere 5 prompts variados" para um LLM produz convergência — os
prompts saem parecidos, os vídeos saem parecidos, e a conta perde alcance. Aqui a responsabilidade é
invertida: **uma matriz combinatória sorteia a estrutura criativa e o LLM só redige o que foi
sorteado**. Cada combinação é hasheada e conferida contra as 30 últimas, então nada se repete.

## O que é automático e o que não é

| Etapa | Como |
|---|---|
| Catálogo de produtos e log de vídeos | `data/*.csv` |
| Sorteio da combinação + anti-repetição | `gdv briefing` |
| Redação do prompt, gancho, legenda, hashtags | `gdv briefing` (Gemini, com fallback offline) |
| Frame inicial no Nano Banana | **manual** |
| Geração do vídeo no Flow | **manual** |
| Montagem, diferenciação e export 1080×1920 | `gdv montar` |
| Postagem | **manual**, pelo TikTok Studio |

Frame e geração continuam manuais de propósito: são os passos que exigem olho humano, e crédito de
vídeo é caro para queimar sem validar o frame antes. A postagem é manual porque a Content Posting API
exige app aprovado e normalmente não permite anexar o produto do Shop ao vídeo — que é onde está o
dinheiro.

## Instalação

```bash
pip install -e ".[dev]"          # núcleo + testes
pip install -e ".[gemini]"       # opcional: redação via Gemini
cp .env.example .env             # e preencha GEMINI_API_KEY
```

Sem `GEMINI_API_KEY` o pipeline continua funcionando: o redator cai para o modo template,
determinístico e offline. `gdv montar` exige `ffmpeg` e `ffprobe` no PATH
(`sudo apt install ffmpeg`).

## Fluxo diário

```bash
# 1. de manhã — gera o briefing e registra os 5 vídeos no log
gdv briefing --qtd 5

# 2. abra saida/briefing/AAAA-MM-DD.md. Para cada vídeo:
#    - monte o frame inicial no Nano Banana: foto real do produto no cenário indicado
#    - valide o frame antes de gastar crédito de vídeo
#    - cole o prompt no Flow, gere, e baixe para entrada/ com o nome indicado no briefing

# 3. marque o que já baixou
gdv status 1 gerado

# 4. monte tudo que está pronto
gdv montar

# 5. suba os arquivos de saida/videos/ no TikTok Studio, anexe o produto, agende
gdv status 1 postado
```

### Comandos

```
gdv briefing [--qtd 5] [--data AAAA-MM-DD] [--seed N] [--janela 30] [--sem-llm] [--dry-run]
gdv montar   [--id ID] [--entrada entrada] [--saida saida/videos] [--trilhas assets/audio]
gdv status   ID {briefado,gerado,montado,postado}
gdv catalogo
```

`--dry-run` imprime sem gravar no log. `--seed` fixa o sorteio: mesmo seed, mesmo briefing — é assim
que você reproduz um dia.

## Os arquivos que você vai editar

**`data/produtos.csv`** — um SKU por linha. Só `status=ativo` entra no sorteio. `pasta_drive` é o link
da pasta com 4–8 fotos limpas do produto; `angulos` é separado por `;`.

> Sem foto de produto boa, nada disso funciona. Foto de fornecedor com marca d'água ou fundo poluído
> derruba a qualidade do image-to-video.

**`data/blocos.yaml`** — a matriz combinatória. É o arquivo mais importante do projeto: adicionar um
valor aqui multiplica o espaço de combinações sem tocar em código. Seis eixos (gancho/POV, cenário,
câmera, iluminação, detalhe em close, ritmo), cada valor com `id` (entra no hash), `texto` (PT) e `en`
(compõe o prompt do Veo).

Dois cuidados:
- **Não renomeie um `id` já usado.** O histórico de anti-repetição aponta para ele; renomear faz a
  combinação voltar a ser sorteada.
- Use `categorias:` para restringir um valor — sem isso, "provando no espelho" seria sorteado para uma
  caneca.

**`data/videos.csv`** — log append-only, escrito pelo pipeline. As colunas `views` e `gmv` estão
reservadas para o loop de feedback e hoje ficam vazias.

## Claims proibidos

`termos_proibidos` em `blocos.yaml` é aplicado como filtro determinístico sobre tudo que o redator
produz — prompt, gancho e legenda. Se o Gemini escorregar num claim de eficácia, a saída é descartada
e o template assume. Instrução de prompt não é controle; a lista é.

Produtos de cuidados pessoais têm restrição de claim no TikTok Shop. Nada de "clareia em 7 dias".

## Diferenciação na montagem

`gdv montar` é o que protege a conta de fingerprint duplicado. Por vídeo: velocidade 0,97–1,03×, crop
1–3%, curva de cor leve, overlay do gancho, trilha rotacionada de `assets/audio/`, metadados limpos,
export H.264 1080×1920 com `+faststart`.

A variação é derivada do hash da combinação, **não** de `random`: reprocessar o mesmo vídeo dá
exatamente o mesmo arquivo.

Ritmo de 2 clipes: salve como `SKU_data_hash_1.mp4` e `_2.mp4`; o pipeline concatena. Clipe único
mantém o nome do briefing.

## Testes

```bash
pytest
```

Os testes da camada 5 conferem a lista de argumentos do ffmpeg sem executá-lo, então rodam em máquina
sem ffmpeg instalado.

## Próximos passos (fora do escopo desta versão)

- **Loop de feedback:** alimentar `views`/`gmv` a cada 3 dias e ponderar os blocos vencedores. O
  sorteador já aceita `pesos` (`sorteio._escolher`); falta só a ingestão das métricas. Sem esse
  feedback, você só escala aleatoriedade.
- Automação do frame inicial (Nano Banana / Flow "Ingredients to Video").
- Adapter Google Sheets: a interface é `catalogo.Catalogo`, nenhum outro módulo abre CSV.
- Agendamento do briefing por cron ou GitHub Actions.
