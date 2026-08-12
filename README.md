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
| Catálogo de produtos e log de vídeos | Supabase (ou `data/*.csv`) |
| Cadastro de produtos | painel web |
| Sorteio da combinação + anti-repetição | painel web ou `gdv briefing` |
| Redação do prompt, gancho, legenda, hashtags | idem (Gemini, com fallback offline) |
| Frame inicial no Nano Banana | **manual** |
| Geração do vídeo no Flow | **manual** |
| Montagem, diferenciação e export 1080×1920 | `gdv montar` — **local** |
| Postagem | **manual**, pelo TikTok Studio |

Frame e geração continuam manuais de propósito: são os passos que exigem olho humano, e crédito de
vídeo é caro para queimar sem validar o frame antes. A postagem é manual porque a Content Posting API
exige app aprovado e normalmente não permite anexar o produto do Shop ao vídeo — que é onde está o
dinheiro.

## Instalação

Precisa de Python 3.10+ e, para a montagem, do `ffmpeg`.

```bash
# 1. ffmpeg (traz o ffprobe junto)
winget install Gyan.FFmpeg      # Windows — reabra o terminal depois, para o PATH atualizar
brew install ffmpeg             # macOS
sudo apt install ffmpeg         # Linux

# 2. o pacote
pip install -e ".[dev]"

# 3. configuração
cp .env.example .env            # Supabase, GEMINI_API_KEY e GDV_FONTE

# 4. confira o que ainda falta
gdv doctor
```

`gdv doctor` é o comando que responde "está pronto?". Ele checa ffmpeg, catálogo, matriz de blocos,
chave do Gemini, fonte do overlay, trilhas e permissão de escrita — e diz o que fazer em cada item
que não passou. Sai com código 1 se houver algo que **impede** o pipeline de rodar; avisos (coisas
opcionais, como não ter trilha) não travam.

Duas coisas são opcionais e degradam em silêncio se você não configurar:

- Sem `GEMINI_API_KEY`, o redator cai para o modo template — determinístico e offline.
- Sem `GDV_FONTE` apontando para uma fonte existente, **os vídeos saem sem o gancho na tela**. No
  Windows use `C:\Windows\Fonts\arialbd.ttf`. O `doctor` avisa quando isso acontece.

## Fluxo diário

O painel web cobre a parte diária; o terminal cobre a montagem, que precisa de
ffmpeg e por isso não roda em serverless.

```
1. abra o painel  →  "Gerar briefing de hoje"
2. para cada card: monte o frame inicial no Nano Banana com a foto real do
   produto no cenário indicado, valide o frame, cole o prompt no Flow, gere,
   e baixe para entrada/ com o nome que o card mostra
3. no terminal:  gdv status 1 gerado
4. no terminal:  gdv montar
5. suba saida/videos/ no TikTok Studio, anexe o produto, agende
6. no terminal:  gdv status 1 postado
```

O painel tem três telas: **Briefing** (cards do dia com botão de copiar prompt,
gancho e legenda), **Catálogo** (cadastro de produtos) e **Matriz** (a matriz de
blocos, só leitura).

Prefere terminal? `gdv briefing --qtd 5` faz o mesmo, contra o mesmo banco.

### Comandos

```
gdv doctor   [--entrada entrada] [--saida saida] [--trilhas assets/audio]
gdv briefing [--qtd 5] [--data AAAA-MM-DD] [--seed N] [--janela 30] [--sem-llm] [--dry-run]
gdv montar   [--id ID] [--entrada entrada] [--saida saida/videos] [--trilhas assets/audio]
gdv status   ID {briefado,gerado,montado,postado}
gdv catalogo
```

`--dry-run` imprime sem gravar no log. `--seed` fixa o sorteio: mesmo seed, mesmo briefing — é assim
que você reproduz um dia.

## O painel web

FastAPI + Jinja2 na Vercel, com Supabase (Postgres + Auth) como fonte de verdade.
Sem npm, sem build step.

O site reusa o motor da CLI — `gdv.sorteio`, `gdv.blocos`, `gdv.redator` — em vez
de reimplementar o sorteio em JavaScript. Isso não é preferência de linguagem:
`hash_combinacao()` precisa dar exatamente o mesmo valor nos dois lugares, senão a
janela anti-repetição de um não enxerga o que o outro gerou.

### Rodar local

```bash
pip install -e ".[dev]"
uvicorn web.main:app --reload
```

### Deploy

O `vercel.json` já manda tudo para `api/index.py` e fixa `maxDuration: 60` — a
geração dispara N chamadas ao Gemini em paralelo, e sequencial estouraria o limite.

Variáveis obrigatórias no projeto da Vercel:

| Variável | Para quê |
|---|---|
| `SUPABASE_URL` | endereço do projeto |
| `SUPABASE_ANON_KEY` | chave pública; a RLS é quem protege os dados |
| `GEMINI_API_KEY` | opcional — sem ela o site redige por template |

`SUPABASE_EMAIL` e `SUPABASE_SENHA` **não** vão para a Vercel: no site quem
autentica é você, pelo formulário de login. Elas só existem no `.env` local, para
a CLI.

### Quando o deploy quebrar

Abra **`/saude`** — rota pública que responde JSON dizendo o que falta: quais módulos importam, se
`templates`, `static` e `blocos.yaml` entraram no bundle da função, e quais variáveis de ambiente
estão definidas. Ela reporta só booleanos, nunca o valor de uma variável.

É o caminho mais curto para diagnosticar um `FUNCTION_INVOCATION_FAILED` sem caçar log.

Uma armadilha já paga por este projeto: **a Vercel instala a partir do `pyproject.toml` e não instala
extras**. Toda dependência que o site precisa tem que estar em `[project].dependencies` — se voltar
para `[project.optional-dependencies]`, o build passa e a função morre no primeiro import.

### Segurança

RLS ligada nas duas tabelas, com acesso apenas para `authenticated` — visitante
anônimo não lê nada, e margem, GMV e link de fornecedor são privados. Não existe
service-role key em lugar nenhum do projeto. A sessão vive em cookies httpOnly +
Secure.

## Os arquivos que você vai editar

**Produtos** — pela tela de Catálogo do painel. Só `status=ativo` entra no sorteio, e `pasta_drive`
é o link da pasta com 4–8 fotos limpas do produto.

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

**Log de vídeos** — escrito pelo pipeline, nunca à mão. As colunas `views` e `gmv` estão reservadas
para o loop de feedback e hoje ficam vazias.

Os arquivos em `data/*.csv` só são usados com `GDV_BACKEND=csv`, o modo offline.

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
