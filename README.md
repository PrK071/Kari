# Kari

Catálogo/leitor local de mangás e manhwas com back-end FastAPI e front-end React/Vite.

## Fontes

### Catalogo principal

- MangaDex
- Fliptru
- Nexus Mangas
- MangaGeek
- MangaKatana
- MangasBrasuka
- MangaLivre

Sakura Mangas entra sob demanda na busca, pois usa um navegador local
dedicado. One Piece Project e usado somente como fonte preferencial de One
Piece, com fallback para MangaLivre quando estiver indisponivel.

### Plugins

- HQ Now: busca e leitura de HQs, isolada do catalogo principal.
- HQ Local: importacao de CBZ, ZIP, CBR e PDF.
- Web Novels: Novel Mania, Central Novel, Tensura Fan e Pleiades Translations.
- Web Novel Local: importacao de EPUB, TXT e Markdown.

## HQ Local

Use `Plugins > HQs` para importar arquivos proprios. Cada arquivo vira
uma edicao/capitulo e usa mesmo leitor, historico e favoritos do Kari. Titulo e
numero podem ser informados no painel ou detectados pelo nome do arquivo.

Arquivos e indice ficam em `backend/.cache/hq_library/`, pasta ignorada pelo
Git. Paginas sao normalizadas para WebP; nenhum caminho local e exposto ao
navegador.

Rotas: `GET /api/hq/library`, `POST /api/hq/import`,
`DELETE /api/hq/{comic_id}` e `GET /api/hq/assets/{comic_id}/{issue_id}/{page}`.

## Web Novel Local

Use `Plugins > Web Novels` para importar `EPUB`, `TXT` ou `MD`. EPUB respeita ordem
do `spine`, metadados, autor e capa. TXT/Markdown sao divididos por headings ou
marcadores como Capitulo, Chapter, Prologo e Epilogo. Sem capa, Kari gera uma
capa WebP local.

Texto e indice ficam em `backend/.cache/light_novel_library/`, fora do Git. O
leitor textual possui brilho, tamanho de fonte, seletor e navegacao de capitulos;
historico e favoritos usam mesmo perfil.

Rotas: `GET /api/light-novels/library`, `POST /api/light-novels/import`,
`DELETE /api/light-novels/{novel_id}` e
`GET /api/light-novels/assets/{novel_id}/cover.webp`.

O menu `Plugins > Web Novels` tambem oferece catalogos remotos do Novel Mania,
Central Novel, Tensura Fan e Pleiades Translations, com busca, metadados, lista
de capitulos/volumes, texto e ilustracoes.
Essas fontes ficam isoladas do catalogo principal. Rotas:
`GET /api/plugins/novel-mania`, `GET /api/plugins/central-novel` e
`GET /api/plugins/tensura-fan` e `GET /api/plugins/pleiades-translations`.

## Rodar

Back-end:

```powershell
cd "C:\Users\User\Documents\Kari"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Front-end:

```powershell
cd "C:\Users\User\Documents\Kari\frontend"
npm run dev
```

Abra `http://127.0.0.1:5173`.

Login Google/Discord usa OAuth2 e cria ou reutiliza perfil local do Kari. Configure
credenciais no `.env` da raiz usando `.env.example` como base. Redirects:

- Google: `http://127.0.0.1:8000/api/auth/google/callback`
- Discord: `http://127.0.0.1:8000/api/auth/discord/callback`

Sem credenciais do provedor, botao correspondente fica oculto. Tokens externos
servem apenas durante callback; navegador recebe somente token de sessao do Kari.

## API Principal

- `GET /api/mangas`
- `GET /api/mangas?q=slime`
- `GET /api/chapters?url=...&title=...&lang=pt-br`
- `GET /api/chapter?url=...`
- `GET /api/image/{index}`

## Perfil (foto, background e contas)

O perfil e local (id salvo no navegador, dados em `backend/.cache/profiles.json`).

- Foto e background: enviados no painel de Perfil (arquivo ou URL). Uploads sao
  validados/normalizados via Pillow e salvos em `backend/static/profiles/<id>/`.
- Contas AniList/MyAnimeList: vinculo via OAuth2. Preencha as credenciais no
  `.env` da raiz (veja `.env.example`) e cadastre os apps com o redirect:
  - AniList: `http://127.0.0.1:8000/api/oauth/anilist/callback`
  - MyAnimeList: `http://127.0.0.1:8000/api/oauth/myanimelist/callback`

  Sem credenciais, o botao "Vincular" fica desabilitado. Tokens ficam so no
  servidor (`profiles.json`), nunca no payload da API.

Depois de vincular, use `Sincronizar` no perfil. Kari importa listas de manga,
ignora itens descartados, casa titulos com obras legiveis do catalogo e mescla
resultados nos favoritos sem remover favoritos locais. MyAnimeList renova token
automaticamente quando refresh token estiver disponivel.

Rotas: `PUT /api/profiles/{id}/avatar`, `PUT /api/profiles/{id}/background`
(corpo `{"url": ...}` ou `{"data": "data:image/...;base64,..."}`, corpo vazio
limpa), `POST /api/profiles/{id}/link/{provider}`,
`GET /api/oauth/{provider}/callback`, `DELETE /api/profiles/{id}/link/{provider}`,
`GET /api/profiles/{id}/link/status` e `POST /api/profiles/{id}/sync/{provider}`.

## Notas

- Capas e imagens de personagens ficam remotas/proxy, sem salvar permanente no PC.
- Cache de leitura é temporário por capítulo.
- Busca tenta fontes PT-BR completas antes de fallback internacional.
- Debug MangasBrasuka: `tools/debug/mangasbrasuka_scraper.py`.
- Saída local `downloads_brasuka/` fica ignorada pelo Git.

## Sakura Mangas (`blob:`)

### Arquitetura e seguranca

Sakura usa um Chromium dedicado porque o leitor entrega as paginas como `blob:`
e pode apresentar desafio Cloudflare. O Kari nao tenta burlar CAPTCHA: a
verificacao ocorre na janela normal do navegador, pelo proprio usuario.

1. `tools/start_sakura_browser.py` abre Chrome, Brave ou Edge com perfil
   dedicado e CDP preso a `127.0.0.1:9333`.
2. O backend aceita CDP somente em `127.0.0.1`, `localhost` ou `::1`; qualquer
   endereco remoto e rejeitado.
3. Playwright conecta ao perfil local, abre uma pagina Sakura por vez e fecha
   somente a pagina criada. Ele nunca fecha o navegador que contem a sessao.
4. Um script de pagina observa os `blob:` gerados pelo leitor. Os bytes sao
   extraidos, validados como imagem, deduplicados por SHA-256 e gravados apenas
   no cache temporario do capitulo.
5. React recebe paginas pelo endpoint local `/api/image/{index}`. URLs `blob:`,
   cookies, headers e caminhos do perfil nunca chegam ao navegador do Kari.

O perfil dedicado preserva cookies e `cf_clearance` localmente. CDP, perfil,
cache, proxy e chaves de solver ficam fora do Git. Nunca exponha a porta `9333`
na rede nem reutilize o perfil Sakura para navegacao pessoal.

### Configuracao e diagnostico

Opcionalmente, crie `.env` a partir de `.env.example`:

```powershell
Copy-Item .env.example .env
```

- `SAKURA_PROXY`: proxy usado por navegador, requisicoes HTTP e solver. Use o
  mesmo IP durante toda sessao; `cf_clearance` fica vinculado ao IP.
- `CAPSOLVER_API_KEY` ou `TWOCAPTCHA_API_KEY`: resolucao automatica opcional.
  Sem chave, conclua desafio manualmente na janela do navegador.
- `SAKURA_BASE_URL`, `SAKURA_CDP_URL`, `SAKURA_PROFILE_DIR` e
  `SAKURA_CHALLENGE_TIMEOUT`: sobrescrevem valores padrao quando necessario.

Para adicionar obra Sakura na home:

```powershell
python tools/add_sakura_manga.py "nome da obra"
python tools/add_sakura_manga.py --show
python tools/add_sakura_manga.py --remove "https://sakuramangas.org/obras/slug-da-obra"
```

Diagnostico de proxy e acesso:

```powershell
python tools/sakura_requisitor.py --preflight --proxy "http://usuario:senha@host:porta"
```

Se aparecer `1020`, `403` ou desafio Cloudflare, confirme que bridge esta
aberto, resolva desafio no navegador e mantenha mesmo proxy/IP. O proxy deve
ser residencial ou movel e com sessao sticky: `cf_clearance` fica vinculado ao
IP. Nunca coloque proxy, API key, cookies ou perfil navegador no Git; `.env`,
`.sakura-browser-profile/`, caches e credenciais ja estao ignorados.

Antes do backend, abra navegador normal dedicado e conclua Cloudflare
manualmente na primeira execução:

```powershell
python tools/start_sakura_browser.py
```

Mantenha a janela aberta. O backend conecta apenas em `127.0.0.1:9333`,
pesquisa obras, lista capitulos e deixa o leitor original gerar as imagens. Os
bytes dos `blob:` sao copiados para cache temporario e servidos ao React por
`/api/image/{index}`. O perfil `.sakura-browser-profile/` preserva a sessao.
