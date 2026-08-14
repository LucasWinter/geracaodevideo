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

Três telas: **Briefing** (gerar o dia e copiar os prompts), **Catálogo** (cadastrar produtos) e
**Matriz** (ver os blocos, só leitura).

### Senha

Duas telas, para dois casos diferentes:

- **`/senha`** — troca a senha de quem já está logado. É o caminho do time: o admin cria a conta com
  uma senha padrão, a pessoa entra e troca. **Não depende de e-mail**, então funciona sempre.
- **`/recuperar`** — envia o link de redefinição pelo Supabase, para quem esqueceu a senha. O link cai
  em `/redefinir`, que lê o token do **fragmento** da URL (`#access_token=…`) via JS, porque fragmento
  não é enviado ao servidor.

A tela de recuperação **sempre responde a mesma coisa**, exista o e-mail ou não. Isso é deliberado:
uma mensagem do tipo "esse e-mail não está cadastrado" transformaria a página num verificador de quem
tem conta na empresa. O "só serve para usuário da base" é garantido pelo servidor — o Supabase só
envia para quem existe — não pela mensagem na tela.

Dois ajustes no painel do Supabase, sem os quais o link do e-mail não funciona:

1. **Authentication → URL Configuration** — `Site URL` e `Redirect URLs` precisam incluir
   `https://<seu-dominio>/redefinir`. Sem isso o Supabase recusa o redirecionamento.
2. **Authentication → Emails** — o SMTP embutido do plano gratuito é para teste e limita a poucos
   e-mails por hora. Para um time pequeno costuma bastar; se o link parar de chegar, é esse limite.
   A saída é configurar um SMTP próprio, ou simplesmente usar `/senha`, que não manda e-mail nenhum.

### A aba Parâmetros

O `data/blocos.yaml` continua sendo a base versionada. A tabela `parametros` no Supabase soma valores
a ela, e a aba **Parâmetros** é onde o time acrescenta sem editar arquivo nem refazer deploy. Três
tipos:

| tipo | onde aparece |
|---|---|
| `eixo` | novo valor sorteável num dos seis eixos — multiplica o espaço de combinações |
| `categoria` | opção no campo Categoria do cadastro de produto |
| `angulo` | caixa de seleção em "Ângulos que você tem" |

`mesclar()` (em `src/gdv/blocos.py`) é quem soma os dois, e **tanto o site quanto a CLI passam por
ela**. Se só um dos dois enxergasse os parâmetros novos, `hash_combinacao()` daria valores diferentes
nos dois lugares e a janela anti-repetição de um deixaria de ver o que o outro gerou.

Regras que valem a pena saber:

- **O YAML ganha em caso de id repetido.** Ele é revisado no repositório; a tabela qualquer um edita.
- **A chave é derivada do texto e nunca muda.** `"varanda ao entardecer"` vira `varanda_ao_entardecer`
  e entra no hash igual a um id do YAML. Recriar um valor removido com o mesmo texto gera a mesma
  chave — a anti-repetição vai reconhecê-lo, não é combinação nova.
- **Remover só afeta sorteios futuros.** O hash guardado no log é texto, não referência: vídeo antigo
  continua válido.
- **Eixo desconhecido é ignorado, não quebra.** Um eixo removido do código não pode derrubar a geração
  do dia.

### O formulário de produto

Os campos são agrupados pelo efeito que têm, porque nem todos têm um. Vale saber onde cada um chega:

| campo | onde é usado |
|---|---|
| `sku` | nome do arquivo do vídeo (`BLS-001_2026-08-13_a1b2c3d4.mp4`) |
| `nome` | vai literal para o prompt e pode aparecer no gancho |
| `categoria` | filtra quais valores de bloco podem ser sorteados |
| `preco` | prompt do Veo, gancho (`{preco}`) e listagem do catálogo |
| `angulos` | chega ao redator como `angulos_disponiveis` |
| `pasta_drive` | link no briefing; o `gdv doctor` avisa quando falta |
| `link_shop` | link no briefing — é o que a legenda chama de "vitrine" |
| `margem` | **nada.** É gravada e nunca lida pelo pipeline |

A margem é digitada em **porcentagem** no formulário e guardada como fração (`42` → `0.42`). O modelo
e o banco não mudaram; só a exibição, porque "fração, ex.: 0.42" era o campo que mais gerava dúvida.

### Campos com lista fixa

Onde o valor válido é um conjunto conhecido, o formulário oferece a lista em vez de texto livre.
Isso não é enfeite: **categoria errada não dá erro nenhum**. Um valor de bloco com `categorias:` só
entra no sorteio quando casa exatamente, então digitar `bolsa` no singular apenas encolhe a variação,
em silêncio, e você só percebe quando os vídeos começam a parecer iguais.

- **Categoria** — as categorias citadas em `data/blocos.yaml` mais as que já existem no catálogo.
  A opção `outra…` libera um campo de texto: dá para cadastrar um produto de categoria nova na hora,
  mas ela só passa a puxar blocos restritos depois de ser citada no `blocos.yaml`.
- **Ângulos** — caixas de seleção com a lista de `ANGULOS_SUGERIDOS` (`src/gdv/modelos.py`), mais um
  campo livre para o que estiver fora dela. A lista não valida nada; é só a sugestão do formulário.
- **Status** e **quantidade de vídeos** — listas fechadas.

### Rodar local

```bash
pip install -e ".[dev]"
uvicorn web.main:app --reload
```

### Mobile

O painel é usado no celular, entre outras coisas para copiar prompt na frente do computador que roda
o Flow. O que isso exige, e que quebra fácil sem querer:

- **Campo de formulário nunca abaixo de 16px.** O Safari do iPhone dá zoom sozinho ao focar um campo
  com fonte menor, e a pessoa perde o enquadramento da página. `.formulario input` fixa `1rem`
  justamente por isso — herdar a fonte do `<label>` (0.9rem) reintroduz o problema.
- **Tabela vira lista de cartões abaixo de 640px.** Sete colunas nunca couberam em 390px, e a rolagem
  lateral escondia Status e Fotos, que são o que se olha. O rótulo de cada célula vem do
  `data-rotulo`, então uma coluna nova precisa do atributo para aparecer no celular.
- **`[hidden]` precisa de `!important`.** O `display` do autor ganha do `display:none` que o navegador
  dá ao atributo; sem isso, `.formulario label { display: grid }` deixava o campo de nova categoria
  visível mesmo escondido no HTML.

### Deploy

O projeto roda no **modo framework Python** da Vercel. Isso não é uma escolha de configuração: a doc
da Vercel diz que declarar dependências em `pyproject.toml` *"enables automatic framework detection"*,
e ter `fastapi` em `[project].dependencies` já basta para ativá-lo.

Nesse modo a Vercel resolve **um** entrypoint ASGI e serve o app inteiro por ele — não existe
diretório `api/` com funções. São três peças, e as três precisam existir:

```jsonc
// vercel.json — sem isto, um projeto com preset "Other" não roda detecção
{ "framework": "fastapi" }                  // e o build sai vazio em ~50ms
```

```toml
# pyproject.toml
[tool.vercel]
entrypoint = "main:app"     # main.py na raiz: um dos nomes que a detecção procura
```

`main.py` na raiz só reexporta o app de `web/asgi.py`; a lógica fica lá.

**As duas coisas andam juntas.** Se alguém mover `fastapi` de volta para um extra, a Vercel volta ao
modo clássico e o entrypoint deixa de ser encontrado. Se alguém recriar `api/index.py` com um bloco
`functions` no `vercel.json`, o build falha em 1 segundo com *"pattern doesn't match any Serverless
Functions"* — foi assim que este projeto perdeu três deploys.

Variáveis obrigatórias no projeto da Vercel:

| Variável | Para quê |
|---|---|
| `SUPABASE_URL` | endereço do projeto |
| `SUPABASE_ANON_KEY` | chave pública; a RLS é quem protege os dados |
| `GEMINI_API_KEY` | opcional — sem ela o site redige por template |

`SUPABASE_EMAIL` e `SUPABASE_SENHA` **não** vão para a Vercel: no site quem autentica é você, pelo
formulário de login. Elas só existem no `.env` local, para a CLI.

Pendência conhecida: `maxDuration` não está configurado. Em app de framework Python ele usa o caminho
do entrypoint resolvido, e vale confirmar esse caminho num build verde antes de mexer.

### Quando uma página der erro

O painel não responde mais "Internal Server Error" em branco. Falha de banco vira uma página que
mostra a mensagem do Supabase; qualquer outra exceção vira uma página com o tipo e a mensagem. O
traceback fica no log da função — a página nunca mostra valor de variável de ambiente.

**`/saude` é o primeiro lugar a olhar**, e é público de propósito: serve justamente quando o login não
funciona. Além dos módulos e arquivos, ele agora reporta `tabelas`, testando cada tabela como
anônimo. Ler o resultado:

| resposta | significa |
|---|---|
| `"ok"` | tabela existe e o PostgREST a conhece |
| erro citando `42501` ou RLS | tabela existe; a RLS negou porque a chamada é anônima — **correto** |
| erro citando `PGRST205` / "schema cache" | o PostgREST não enxerga a tabela |

O último caso é o que derrubou o painel quando a tabela `parametros` foi criada: o PostgREST mantém um
cache do schema e não o recarrega sozinho na hora. A correção é `notify pgrst, 'reload schema';` no SQL
Editor do Supabase.

Duas lições que o código agora fixa:

- **Tabela nova é opcional até prova em contrário.** `parametros_seguros()` captura a falha e o site
  segue com a matriz do `blocos.yaml`, mostrando um aviso. Uma tabela recém-criada não pode derrubar o
  formulário de produto, a matriz e a geração do briefing de uma vez.
- **O erro tem que aparecer no navegador.** Sem log acessível, um 500 mudo custa um ciclo inteiro de
  ida e volta para descobrir o que já era conhecido do servidor.

### Quando o deploy quebrar

Abra **`/saude`** — rota pública que responde JSON dizendo o que falta: quais módulos importam, se
`templates`, `static` e `blocos.yaml` entraram no bundle da função, e quais variáveis de ambiente
estão definidas. Ela reporta só booleanos, nunca o valor de uma variável.

É o caminho mais curto para diagnosticar um `FUNCTION_INVOCATION_FAILED` sem caçar log.

Duas armadilhas já pagas por este projeto:

- **A Vercel instala a partir do `pyproject.toml` e não instala extras.** Toda dependência do site
  tem que estar em `[project].dependencies`; em `[project.optional-dependencies]` o build passa e a
  função morre no primeiro import.
- **Falha de build em ~1 segundo não é build, é config rejeitada** — acontece antes de instalar
  qualquer coisa. Vá direto ao log do *build* (não ao de runtime): ele nomeia o problema.
- **`Build Completed in [46ms]` com 404 em tudo significa que nada foi construído.** Sem
  `"framework": "fastapi"` no `vercel.json`, um projeto importado com preset "Other" não roda
  detecção nenhuma e publica saída vazia.

### Criar usuários

Pelo painel do Supabase: **Authentication → Users → Add user**. Ou pelo `signUp` da API.

**Nunca por `INSERT` direto em `auth.users`.** Este projeto pagou esse erro duas vezes, e a linha
criada por SQL parece perfeita — existe, tem senha, está confirmada, aparece na listagem — e mesmo
assim nada funciona. São duas faltas distintas:

1. **Sem linha em `auth.identities`**, o login por e-mail falha com "Invalid login credentials".
2. **Colunas de token em `NULL`** (`confirmation_token`, `recovery_token`, `email_change`,
   `email_change_token_new`, ...). O GoTrue lê essas colunas em `string` do Go, não em ponteiro:
   `NULL` estoura o scan e vira **"Database error loading user"** — no login e ao tentar excluir o
   usuário pelo painel. A API sempre grava string vazia, nunca `NULL`.

Diagnóstico das duas de uma vez:

```sql
select u.email,
       (select count(*) from auth.identities i where i.user_id = u.id) as identities,
       (u.confirmation_token is null or u.recovery_token is null
        or u.email_change is null or u.email_change_token_new is null) as tokens_nulos
from auth.users u;
```

`identities = 0` ou `tokens_nulos = true` explicam qualquer falha de autenticação.
(`phone` em `NULL` é normal para usuário de e-mail.)

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
