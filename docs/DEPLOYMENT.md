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

Use um único worker enquanto existir estado global de leitura e caches em
memória. Isso não corrige concorrência entre usuários; apenas evita divergência
adicional entre processos.

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
