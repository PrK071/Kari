# Kari

O Kari é uma plataforma de descoberta, organização e leitura de mangás,
manhwas, HQs e web novels. Ele reúne obras de diferentes fontes em uma interface
única, mantém histórico e favoritos por perfil e também permite importar uma
biblioteca diretamente do computador ou celular.

## Acesse o Kari

**[Abrir o Kari](https://kari-phi.vercel.app)**

> O backend utiliza o plano gratuito do Render. Depois de um período sem uso, a
> primeira abertura pode demorar enquanto a instância é iniciada. Usuários
> recorrentes ainda conseguem consultar o último catálogo público salvo no
> navegador durante a reconexão.

## Principais recursos

- Busca unificada por título e aliases, incluindo acentos e variações Unicode.
- Catálogo PostgreSQL-first com índice residente em memória para respostas
  rápidas de obras conhecidas.
- Leitor de capítulos com suporte a diferentes fontes e idiomas.
- Histórico, favoritos e biblioteca persistidos por perfil.
- Cadastro tradicional e autenticação com Discord.
- Vinculação e sincronização com AniList e MyAnimeList.
- Avatar, background do perfil e background da Home.
- Importação de HQs e web novels pelo navegador usando IndexedDB.
- Importação desktop de CBZ, ZIP, CBR, PDF, EPUB, TXT e Markdown.
- Cache público do catálogo no navegador, sem dados privados ou credenciais.

## Arquitetura web

```text
Navegador
  ├── React + Vite na Vercel
  ├── IndexedDB: catálogo público e biblioteca importada
  └── FastAPI no Render
        ├── índice descartável em RAM
        ├── PostgreSQL Neon: fonte de verdade
        ├── Backblaze B2: mídia persistente de perfis
        └── providers externos atualizados em background
```

Uma busca conhecida percorre:

```text
cache curto → índice em RAM → resposta
```

Quando a obra não está no índice, o Kari usa PostgreSQL como fallback e só
depois consulta providers externos dentro de um orçamento limitado. Resultados
novos são persistidos no PostgreSQL e atualizam o índice somente após o commit.

## Fontes e plugins

O catálogo integra fontes como:

- MangaLivre
- MangaDex
- Fliptru
- MangaKatana
- Nexus Mangás
- Mangás Brasuka
- MangaGeek

Plugins adicionais oferecem:

- HQ Now para busca e leitura de HQs.
- Novel Mania, Central Novel, Tensura Fan e Pleiades Translations para web
  novels.
- HQ Local e Web Novel Local no desktop.
- Sakura Mangás sob demanda no ambiente desktop, usando navegador local
  dedicado. Sakura e Playwright permanecem desativados no backend web.

## Tecnologias

### Backend

- Python e FastAPI
- SQLAlchemy e Alembic
- PostgreSQL/Neon
- Backblaze B2 via API compatível com S3
- Argon2 para senhas
- OAuth2 para integrações externas

### Frontend

- React
- Vite
- TanStack Query
- Tailwind CSS
- IndexedDB

## Executar localmente

### Backend

```powershell
git clone https://github.com/PrK071/Kari.git
cd Kari
python -m venv .venv
./.venv/Scripts/Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m alembic upgrade head
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

### Frontend

Em outro terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Abra `http://127.0.0.1:5173`.

As variáveis do backend estão documentadas em `.env.example`. O frontend usa
`frontend/.env.example`, incluindo `VITE_API_BASE_URL` quando a API não estiver
na mesma origem.

## OAuth local

Cadastre somente as integrações que deseja utilizar:

- Discord: `http://127.0.0.1:8000/api/auth/discord/callback`
- Google: `http://127.0.0.1:8000/api/auth/google/callback`
- AniList: `http://127.0.0.1:8000/api/oauth/anilist/callback`
- MyAnimeList: `http://127.0.0.1:8000/api/oauth/myanimelist/callback`

Credenciais ficam no `.env` e nunca devem ser commitadas. Tokens externos são
armazenados somente pelo backend; o navegador recebe apenas a sessão do Kari.

## Testes

Backend:

```powershell
python -m pytest -q
```

Frontend:

```powershell
cd frontend
npm test
npm run build
```

## Documentação técnica

- [Segurança e persistência](docs/SECURITY.md)
- [Benchmark e arquitetura de busca](docs/SEARCH_PERFORMANCE.md)
- [Deploy](docs/DEPLOYMENT.md)
- [Arquitetura web](docs/WEB_ARCHITECTURE.md)

## Observações

- PostgreSQL é a fonte de verdade do catálogo web; caches em RAM, filesystem e
  IndexedDB são descartáveis.
- O Kari não utiliza keepalive artificial para evitar o spin-down do Render.
- Playwright, Sakura e bibliotecas desktop não participam do caminho crítico da
  busca web.
- Nunca exponha a porta CDP usada pelo plugin Sakura na rede nem reutilize seu
  perfil dedicado para navegação pessoal.
