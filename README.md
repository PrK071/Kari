# Kari

Catálogo/leitor local de mangás e manhwas com back-end FastAPI e front-end React/Vite.

## Fontes Ativas

- MangaDex
- MangaLivre
- MangasBrasuka
- Toomics
- One Piece Project
- DragonTea
- MangaKatana
- ReadFull
- NovelToon
- YumoMangas
- Sakura Mangas

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

Rotas: `PUT /api/profiles/{id}/avatar`, `PUT /api/profiles/{id}/background`
(corpo `{"url": ...}` ou `{"data": "data:image/...;base64,..."}`, corpo vazio
limpa), `POST /api/profiles/{id}/link/{provider}`,
`GET /api/oauth/{provider}/callback`, `DELETE /api/profiles/{id}/link/{provider}`,
`GET /api/profiles/{id}/link/status`.

## Notas

- Capas e imagens de personagens ficam remotas/proxy, sem salvar permanente no PC.
- Cache de leitura é temporário por capítulo.
- Busca tenta fontes PT-BR completas antes de fallback internacional.
- Debug MangasBrasuka: `tools/debug/mangasbrasuka_scraper.py`.
- Saída local `downloads_brasuka/` fica ignorada pelo Git.

## Sakura Mangas (`blob:`)

Antes do backend, abra navegador normal dedicado e conclua Cloudflare
manualmente na primeira execução:

```powershell
python tools/start_sakura_browser.py
```

Mantenha a janela aberta. O backend conecta apenas em `127.0.0.1:9333`,
pesquisa obras, lista capítulos e deixa o leitor original gerar as imagens. Os
bytes dos `blob:` são copiados para cache temporária e servidos ao React por
`/api/reader-image/{index}`. O perfil `.sakura-browser-profile/` preserva a sessão.
