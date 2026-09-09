# PostgreSQL-first search benchmark

Data: 2026-09-07/08 (America/Sao_Paulo)

## Baseline

A busca quente anterior esperava a barreira de todos os providers: 2.347 ms p50
e 3.141 ms p95 internos. A consulta local em memoria/JSON levava cerca de 2 ms;
97,25% do tempo vinha do fan-out externo.

| Fonte | p50 (ms) | p95 (ms) | timeout/error | buscas com resultado util |
|---|---:|---:|---:|---:|
| MangaLivre | 636,37 | 1.567,03 | 0/21 | 21/21 |
| Fliptru | 1.347,13 | 2.322,26 | 0/21 | 10/21 |
| Nexus | 1.440,45 | 1.839,65 | 21/21 | 0/21 |
| MangasBrasuka | 1.451,86 | 2.195,53 | 21/21 | 0/21 |
| MangaDex | 1.580,49 | 2.421,39 | 0/21 | 21/21 |
| MangaKatana | 2.257,57 | 3.039,32 | 0/21 | 15/21 |

O cold start medido do Render Free foi 56,15 s no cliente, dos quais 3,315 s
eram trabalho interno e aproximadamente 52,84 s eram ativacao da plataforma.
Ele permanece separado deste trabalho; nao foi adicionado keepalive.

## Schema

`catalog_items` preserva titulo/aliases originais e mantem campos consultaveis
separados: identidade estavel, chave canonica, titulo normalizado, provider,
source identifier/URL, capa, generos, contagem/resumo de capitulos e timestamps
de primeira/ultima observacao e criacao/atualizacao. JSONB fica restrito a
aliases, generos, resumo de capitulos e payload complementar.

Unicidade e definida por `(provider, source_key)`, em que `source_key` deriva da
URL/identificador normalizado. A deduplicacao canonica por titulo ocorre acima
dessa identidade e nao confunde duas fontes distintas. Indices: B-tree em
`normalized_title` e `canonical_key`, composto para Home, GIN JSONB em aliases e
GIN `gin_trgm_ops` em `search_text`.

## Fluxo implementado

```text
request
  -> cache curto descartavel
  -> indice compacto residente em memoria
  -> catalog_items no PostgreSQL
  -> resposta local
  -> refresh externo posterior (somente se last_seen_at estiver vencido)
  -> upsert idempotente no PostgreSQL
```

Quando o PostgreSQL nao encontra nada, MangaLivre recebe um budget foreground de
1,2 s. Os demais providers rodam em background. MangaDex, Fliptru e
MangaKatana continuam disponiveis para descoberta; Nexus e MangasBrasuka foram
depriorizados, nao removidos. Circuit breaker em memoria abre depois de tres
falhas/timeouts ou cinco respostas vazias consecutivas, com cooldown de cinco
minutos. Buscas externas identicas usam single-flight.

Timeouts de socket `(connect, read)`:

| Provider | Timeout |
|---|---:|
| MangaLivre | (0,35 s, 0,75 s) |
| Fliptru | (0,60 s, 1,80 s) |
| MangaDex | (0,70 s, 2,20 s) |
| MangaKatana | (0,70 s, 2,60 s) |
| Nexus | (0,40 s, 1,10 s) |
| MangasBrasuka | (0,40 s, 1,30 s) |

Retries internos nao multiplicam o budget de Fliptru/MangasBrasuka na busca web.
Sakura e Playwright nao participam deste fluxo.

## PostgreSQL

O Neon recebeu a migration `20260907_0003`. O seed e os refreshes iniciais
popularam 69 registros. A busca faz uma unica ida ao banco, ordenando por match
normalizado exato, alias, prefixo e similaridade trigram. `EXPLAIN ANALYZE` para
`hunter x hunter` executou em 1,027 ms no servidor PostgreSQL; com o catalogo
pequeno o planner escolheu sequential scan. Com `enable_seqscan=off`, o plano
confirmou elegibilidade dos indices B-tree, GIN JSONB e GIN `gin_trgm_ops`.

Depois de colapsar a leitura de duas consultas para uma, o benchmark da
aplicacao contra o Neon real (fora da instancia Render) ficou:

| Query | app p50 (ms) | app p95 (ms) | resultados |
|---|---:|---:|---:|
| hunter x hunter | 34,27 | 53,94 | 3 |
| naruto | 80,02 | 153,60 | 18 |
| berserk | 61,34 | 76,50 | 13 |
| one piece | 107,22 | 121,47 | 18 |
| vinland saga | 38,83 | 54,23 | 4 |

`pool_pre_ping` tambem foi removido depois de um A/B local (41,17 -> 32,38 ms
p50 no lookup de Hunter x Hunter). Conexoes com mais de 240 s sao recicladas,
evitando o round-trip de validacao em todo checkout.

## Benchmark no Render aquecido

As primeiras chamadas abaixo usaram chaves de cache novas, com o processo
Render ja acordado e sem refresh externo concorrente. `Server-Timing` separa o
tempo interno do cliente:

| Query | primeiro cliente (ms) | primeiro interno (ms) | PostgreSQL (ms) | resultados |
|---|---:|---:|---:|---:|
| hunter x hunter | 615,47 | 469,08 | 467,82 | 3 |
| naruto | 849,35 | 701,38 | 698,37 | 18 |
| berserk | 631,10 | 481,12 | 472,70 | 13 |
| one piece | 737,78 | 587,16 | 583,85 | 18 |
| vinland saga | 507,82 | 355,27 | 354,16 | 4 |

Medianas da primeira busca: 631,10 ms no cliente, 481,12 ms internos e 472,70
ms em acesso/transformacao do resultado PostgreSQL. Isso e 3,7x mais rapido que
o baseline quente interno de 2.347 ms, mas ainda nao atinge 250 ms no primeiro
cache miss da instancia Free.

Buscas repetidas (`cached=true`):

| Query | cliente p50 (ms) | cliente p95 amostral (ms) | servidor p50 (ms) |
|---|---:|---:|---:|
| hunter x hunter | 157,78 | 660,55 | 0,49 |
| naruto | 160,17 | 182,09 | 0,89 |
| berserk | 178,17 | 183,53 | 2,21 |
| one piece | 158,22 | 184,91 | 1,01 |
| vinland saga | 157,26 | 160,42 | 0,46 |

A mediana entre as queries repetidas e 158,22 ms, abaixo da meta de 250 ms.
O outlier de cliente em Hunter x Hunter nao aparece no tempo interno (0,49 ms)
e pertence ao trajeto publico/edge, nao ao endpoint.

O contraste entre 34--107 ms p50 fora do Render e 355--698 ms de PostgreSQL no
Render confirma impacto relevante da instancia/regiao no cache miss. Nenhum
upgrade foi feito. O proximo teste recomendado e comparar regioes equivalentes
Render/Neon ou uma instancia de benchmark sem sleep, mantendo exatamente o
mesmo codigo e consulta; so entao atribuir ganho a um plano pago.

## Home

A Home consulta `catalog_items.is_home_ready` e monta as secoes a partir do
PostgreSQL. `catalog.json` permanece apenas como fallback de transicao/cache
descartavel e nao e mais a fonte persistente do Kari Web. O refresh enriquece e
faz upsert no banco em background.

## Observabilidade

Os logs estruturados preservam `search_total_ms`, `postgres_search_ms`,
`local_results`, `external_refresh_ms`, tempos/erros/timeouts por provider,
`cache_hit` e `background_refresh_started`. O endpoint tambem publica apenas
duracoes agregadas no header `Server-Timing`; nenhum termo pesquisado e exposto.

## Verificacao

- Alembic upgrade no Neon: PASS.
- Alembic upgrade/downgrade em SQLite isolado: PASS.
- PostgreSQL real em schema isolado, incluindo alias Unicode: PASS.
- `manga_dataset`: somente ingestao offline validada; registros sem URL de obra
  sao observavelmente ignorados.
- Suite Python: 102 passed + 16 subtests passed (PostgreSQL real incluido).
- Frontend: 15 passed.
- Vite production build: PASS (1.853 modules transformed).

## Indice residente e cold-start percebido (2026-09-09)

O PostgreSQL continua sendo a fonte de verdade. No startup, uma unica leitura de
`catalog_items` constroi um snapshot compacto; buscas conhecidas e a Home passam
a ler esse snapshot. Miss do indice ainda faz fallback PostgreSQL e resultados
persistidos por scrapers so entram na RAM depois do commit. Falha de rebuild nao
altera o snapshot anterior nem o banco.

Estado medido no Render: 490 registros, 1.015.863 bytes (~992 KiB) de RAM e
176,25 ms para projetar, indexar e medir o snapshot depois da leitura SQL. No
ambiente local, a mesma operacao levou 52--56 ms. Exact title/alias usa mapas;
prefix/contains faz varredura simples e fuzzy so roda se os estagios anteriores
nao encontrarem nada.

### Benchmark aquecido

Foram feitas 15 buscas com chaves de cache HTTP novas (tres por titulo) e depois
15 repeticoes, sempre com o Render acordado. `index_hit=true` e
`postgres;dur=0.00` ocorreram em todos os misses conhecidos.

| Metrica | Antes (PostgreSQL por miss) | Depois (RAM por miss) | Delta |
|---|---:|---:|---:|
| cliente p50 | 631,10 ms | 138,79 ms | -78,0% |
| servidor p50 | 481,12 ms | 0,51 ms | -99,9% |
| PostgreSQL p50 no request | 472,70 ms | 0,00 ms | -100% |
| indice RAM p50 | n/a | 0,03 ms | n/a |
| cliente p95 amostral | n/a | 250,27 ms | n/a |
| servidor p95 amostral | n/a | 0,86 ms | n/a |

Smoke test no ultimo deploy (uma amostra por query):

| Query | cliente (ms) | servidor (ms) | indice RAM (ms) | PostgreSQL (ms) |
|---|---:|---:|---:|---:|
| hunter x hunter | 140,80 | 0,84 | 0,05 | 0,00 |
| naruto | 138,75 | 0,52 | 0,02 | 0,00 |
| berserk | 139,56 | 0,51 | 0,02 | 0,00 |
| one piece | 137,84 | 0,74 | 0,04 | 0,00 |
| vinland saga | 141,02 | 0,54 | 0,03 | 0,00 |

Repeticoes ficaram em 139,55 ms p50 no cliente e 0,29 ms p50 no servidor. Em
10 requests simultaneos, o servidor continuou em ~0,30 ms p50, mas o cliente
subiu para ~1,98 s p50. Portanto essa degradacao concorrente esta no caminho
publico/instancia Free, nao no algoritmo de busca ou no Neon.

### IndexedDB publico

O frontend salva um snapshot separado com whitelist estrita: id, titulo,
aliases, provider/source e URL publica da obra, capa publica, generos e resumo de
capitulos. Tokens, perfil, historico, favoritos, credenciais e URLs assinadas sao
descartados. O snapshot atual tem 490 itens e ~300.037 bytes em JSON. A busca
local sobre ele levou em media 6,47 ms em 1.000 execucoes no Node local.

Em visita recorrente, a Home e a busca podem mostrar o ultimo catalogo enquanto
o Render acorda; a interface sinaliza explicitamente que exibe dados salvos e
esta reconectando. A leitura IndexedDB real varia por navegador e nao foi usada
como numero de benchmark. Depois que qualquer request ao backend conclui, a
sincronizacao de `/api/catalog-index` ocorre em background.

### Startup real

`startup_metrics` agora separa as fases e pode ser consultado em
`/api/diagnostics/startup`. Duas inicializacoes reais de deploy no Render deram
17,19 s e 21,90 s ate o app pronto. A amostra mais recente:

| Fase | Duracao |
|---|---:|
| imports Python | 14.507,10 ms |
| config | 0,14 ms |
| scraper runtime | 0,07 ms |
| repository/engine | 1.311,16 ms |
| Object Storage | 0,01 ms |
| MangaReader | 85,05 ms |
| restante do setup de modulo | 2.703,86 ms |
| primeira leitura PostgreSQL | 3.114,82 ms |
| rebuild/medicao do indice | 176,25 ms |
| app pronto (cumulativo) | 21.898,51 ms |

O baseline externo de ~53 s inclui tanto provisionamento/roteamento do Render
quanto o startup Python. Comparando amostras nao simultaneas, o residuo de
plataforma fica aproximadamente em 31--36 s; isso e estimativa, nao uma nova
medicao controlada de spin-down. Nenhum keepalive foi adicionado.

PyMuPDF, Playwright e boto3/client B2 agora sao carregados somente no primeiro
uso. Em tres imports locais, Playwright/PyMuPDF/boto3 permaneceram ausentes de
`sys.modules`; Object Storage caiu de 508,08 ms para 0,01 ms no caminho de
startup local. As operacoes desktop/PDF/browser e B2 preservam as interfaces e
foram verificadas depois da mudanca. Nao foi tentado lazy-load do `reader_server`
inteiro: o ganho adicional nao justificaria o risco estrutural para desktop.

### Verificacao desta fase

- Python: 116 passed + 16 subtests passed.
- Frontend: 19 passed.
- Vite production build: PASS (1.854 modulos transformados).
- `/health`, `/ready`, `/api/home` e `/api/catalog-index` no Render: HTTP 200.
- PostgreSQL/Neon, B2, providers, schema e hospedagem: inalterados.
