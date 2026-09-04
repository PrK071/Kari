# Deploy do Kari

## Pré-requisitos e bloqueio atual

Arquitetura prevista: Vercel para o frontend e uma VPS Ubuntu/Oracle Cloud para
o FastAPI atrás de HTTPS. Não exponha a API publicamente até os bloqueadores de
`SECURITY.md` serem resolvidos. As instruções abaixo servem para staging privado
e para a configuração final após esses gates.

## Frontend na Vercel

Importe o repositório e configure:

- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: `dist`
- `VITE_API_BASE_URL=https://api.DOMINIO_DO_KARI`
- `VITE_KARI_RUNTIME=web`

Não grave o domínio real no código. O frontend atual não usa rotas client-side,
portanto não precisa de `vercel.json`. Se React Router for introduzido, adicione
então um rewrite de SPA e um teste de refresh de rota.

## Backend na VPS

Exemplo de instalação em Ubuntu:

```bash
sudo apt update
sudo apt install -y python3-venv git caddy
sudo useradd --system --create-home --shell /usr/sbin/nologin kari
sudo mkdir -p /opt/kari /var/lib/kari/data /var/lib/kari/static
sudo chown -R kari:kari /opt/kari /var/lib/kari
sudo -u kari git clone https://github.com/PrK071/Kari.git /opt/kari/app
sudo -u kari python3 -m venv /opt/kari/venv
sudo -u kari /opt/kari/venv/bin/pip install -r /opt/kari/app/requirements.txt
```

Crie `/etc/kari.env`, legível somente pelo usuário do serviço:

```dotenv
KARI_ENV=production
KARI_RUNTIME=web
KARI_PERSISTENCE_BACKEND=postgres
KARI_BACKEND_URL=https://api.DOMINIO_DO_KARI
KARI_FRONTEND_URL=https://DOMINIO_DO_KARI
KARI_ALLOWED_ORIGINS=https://DOMINIO_DO_KARI
KARI_DATA_DIR=/var/lib/kari/data
KARI_STATIC_DIR=/var/lib/kari/static
DATABASE_URL=postgresql+psycopg://USUARIO:SENHA@HOST:5432/kari
KARI_SECRET_KEY=GERAR_FORA_DO_GIT_COM_PELO_MENOS_32_CARACTERES
KARI_SESSION_TTL_HOURS=720
KARI_RATE_LIMIT_BACKEND=memory
KARI_STORAGE_BACKEND=filesystem
```

Crie o schema explicitamente antes de iniciar a API. O startup não executa DDL:

```bash
cd /opt/kari/app
set -a
source /etc/kari.env
set +a
/opt/kari/venv/bin/alembic upgrade head
```

## Migração dos dados JSON

Depois do `alembic upgrade head`, execute a cópia legada explicitamente com o
diretório que contém `users.json`, `profiles.json` e `tokens.json`:

```bash
cd /opt/kari/app
set -a
source /etc/kari.env
set +a
/opt/kari/venv/bin/python -m tools.migrate_identity \
  --source-dir /CAMINHO/DO/KARI_DATA_DIR \
  --backup-dir /var/backups/kari/json-migration
```

A ferramenta cria backup com hashes antes de ler os registros, faz upserts
idempotentes e imprime somente contagens/erros sem valores sensíveis. Ela nunca
remove ou altera os JSON de origem. Código de saída diferente de zero indica
migração parcial ou inválida; corrija a origem/schema e repita o mesmo comando.
Proteja o backup como credencial, pois ele contém hashes de senha, tokens de
sessão legados e possivelmente tokens OAuth.

Confirme que `users_migrated`, `profiles_migrated` e `sessions_migrated`
correspondem às respectivas contagens `*_found` e que `errors` está vazio antes
de trocar o serviço para `KARI_PERSISTENCE_BACKEND=postgres`.

No staging, configure um banco PostgreSQL descartável em
`KARI_TEST_POSTGRES_URL` e execute `python -m pytest -q`. O teste cria um schema
com nome aleatório, valida profiles/users/sessions no adaptador real e remove
esse schema no final. Sem a variável, o gate é marcado como ignorado; isso não
constitui validação de PostgreSQL.

O filesystem ainda não deve ser tratado como storage persistente multiusuário
para avatares e backgrounds; essa migração permanece separada do banco.

Serviço `/etc/systemd/system/kari.service`:

```ini
[Unit]
Description=Kari FastAPI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kari
Group=kari
WorkingDirectory=/opt/kari/app
EnvironmentFile=/etc/kari.env
ExecStart=/opt/kari/venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Use um único worker na implantação inicial porque o rate limiter e caches são
locais ao processo. O leitor web já descarta estado mutável antes de responder e
o processo limita scrapers globalmente e por fonte, mas múltiplos workers teriam
limites independentes. Antes de escalar horizontalmente, implemente um
`RateLimitBackend` e um coordenador de trabalho compartilhados e execute os
testes de políticas contra eles.

Para uploads de avatar/background, use um bucket dedicado compatível com S3 e
configure `KARI_STORAGE_BACKEND=object_storage` mais as seis variáveis
`KARI_OBJECT_STORAGE_*` do `.env.example`. `KARI_OBJECT_STORAGE_PUBLIC_BASE_URL`
deve apontar para a origem pública HTTPS do bucket/CDN. Restrinja a credencial do
serviço a listar, criar e excluir objetos somente nesse bucket. Se o backend
continuar em `filesystem`, uploads web permanecem indisponíveis por segurança.

Proxy `/etc/caddy/Caddyfile`:

```caddyfile
api.DOMINIO_DO_KARI {
    reverse_proxy 127.0.0.1:8000
}
```

Ativação e diagnóstico:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kari caddy
curl https://api.DOMINIO_DO_KARI/health
curl https://api.DOMINIO_DO_KARI/ready
sudo journalctl -u kari -f
```

A porta 8000 deve aceitar tráfego apenas de loopback. Libere externamente apenas
80/443 e deixe o Caddy emitir/renovar TLS.

## Atualização e rollback

Antes de atualizar, faça backup consistente do PostgreSQL e da mídia persistente.
Caches não entram no backup. Registre o commit atual, instale dependências e
reinicie somente depois dos testes:

```bash
cd /opt/kari/app
git fetch origin
git checkout COMMIT_TESTADO
/opt/kari/venv/bin/pip install -r requirements.txt
/opt/kari/venv/bin/python -m unittest discover -s tests -v
sudo systemctl restart kari
```

Para rollback, faça checkout do commit anterior, execute o downgrade de schema
correspondente quando existir, reinstale dependências e reinicie. Restaure banco
ou Object Storage somente em caso de migração de dados incompatível e após
confirmar o alvo do backup.
