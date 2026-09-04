# Runbook de validação de staging do Kari

Pré-requisitos e comandos de instalação estão em `DEPLOYMENT.md`. Este documento
é o checklist executável das validações de staging; cada item do gate final
precisa de evidência registrada.

## Provisionamento mínimo (antes de começar)

1. VPS Ubuntu (Oracle Cloud ou equivalente) com 22/80/443 abertos e PostgreSQL
   interno acessível apenas por loopback/VCN privada.
2. Banco PostgreSQL dedicado de staging (`kari_staging`) + um segundo banco
   descartável para restore (`kari_staging_restore`).
3. Bucket Object Storage S3-compatible de staging com credencial limitada a
   esse bucket (list/create/delete) e origem pública HTTPS separada.
4. Projeto Vercel de staging (não o domínio definitivo) apontando para o
   repositório, root `frontend`, com `VITE_API_BASE_URL=https://<api-staging>`
   e `VITE_KARI_RUNTIME=web`.
5. Domínio HTTPS de staging para a API (Caddy emite/renova TLS).

Não usar dados reais do desktop: fixtures sintéticas para a migração JSON.

## Configuração base

```dotenv
KARI_ENV=staging
KARI_RUNTIME=web
KARI_PERSISTENCE_BACKEND=postgres
KARI_STORAGE_BACKEND=object_storage
DATABASE_URL=postgresql+psycopg://USUARIO:SENHA@127.0.0.1:5432/kari_staging
KARI_ALLOWED_ORIGINS=https://<frontend-staging>
KARI_TEST_POSTGRES_URL=postgresql+psycopg://USUARIO:SENHA@127.0.0.1:5432/kari_staging
```

## Fases e evidências

| # | Fase | Ação | Evidência esperada |
| --- | --- | --- | --- |
| 4 | PostgreSQL real | `alembic upgrade head`; `alembic current`; `alembic history` | schema aplicado, sem erro; downgrade só em banco descartável |
| 5 | Migração JSON | rodar `tools/migrate_identity.py` 2x com fixtures Unicode/campos opcionais | 2ª execução: 0 duplicatas; backup criado; falha parcial deixa origem intacta |
| 6 | Object Storage | upload/ler/substituir/deletar avatar A e B; objeto inexistente; arquivo inválido; grande demais | A não lê privado de B; sem path traversal; sem credencial em URL; 503/erro controlado com storage fora |
| 7 | VPS/firewall | `ufw`/security list com 22/80/443; 8000 e 5432 só internos | porta 8000 e 5432 inacessíveis externamente |
| 8 | Process manager | `systemctl start/stop/restart` + reboot + `journalctl` | volta sozinho; dados preservados; sem fallback para JSON |
| 9 | HTTPS | `curl https://api-staging/health`; redirect HTTP→HTTPS | TLS válido, sem self-signed |
| 10 | CORS real | origem staging → permitido; `evil.example` e `Origin: null` → bloqueado | sem wildcard |
| 11 | Vercel | build/deploy preview; refresh; assets; viewport móvel | carregamento OK; API alcançável |
| 12 | Auth E2E | registrar/logar/`/api/auth/me`/logout/re-login; senha errada; token inválido | banco guarda só digest; senha Argon2id |
| 13 | IDOR real | A→A e B→B permitidos; A→B e B→A 403 | perfil, favoritos, biblioteca, avatar, OAuth |
| 14 | Leitor | duas sessões intercaladas em capítulos distintos | páginas não misturam; `/api/reader-image/*` 404 em web |
| 15 | Rate limit | exceder login (30/5min), busca (60/min), proxy (120/min) | 429 com retry-after; recuperação após janela; chaves por IP não colidem |
| 16 | Concorrência | 5/10/20 requisições concorrentes no fan-out de busca | sem thread explosion; limites 12 globais/2 por fonte respeitados; falha controlada |
| 17 | Restart durante uso | criar dados → `systemctl restart kari` → validar tudo | nada perdido, sem corrupção |
| 18 | Falha Postgres | `systemctl stop postgresql` → chamar API → `start` | erro previsível sem connection string/stack trace; `/ready` reflete indisponibilidade |
| 19 | Falha storage | remover acesso ao bucket → chamar upload | sem crash total; sem write local silencioso; sem credencial exposta |
| 20 | Scrapers | matriz por fonte (search/metadata/chapters/reader) | só fontes permitidas; MangaGeek HTTP desabilitado; Sakura/DragonTea fora do web |
| 21 | SSRF/egress | proxy com `127.0.0.1`, `localhost`, `10/8`, `172.16/12`, `192.168/16`, `169.254.169.254`, IPv6 loopback, credentials em URL, redirect público→privado | tudo bloqueado |
| 22 | Logs | `journalctl` após todos os testes | sem senha/token/`Authorization`/`DATABASE_URL`/stack trace sensível |
| 23 | Backup | `pg_dump --format=custom` → restore em `kari_staging_restore` | contagens e integridade conferidas; backup sem senha na linha de comando |
| 24 | Rollback | documentar checkout N-1 + downgrade de schema quando existir | rollback de código ≠ downgrade de schema ≠ restore de dados |
| 25 | Testes completos | `pytest` com `KARI_TEST_POSTGRES_URL` + frontend | Postgres gate = PASS (não skipped); npm audit 0; builds web/desktop OK |

## Gate final

Todos os itens abaixo verdes para sair de staging:

```text
[ ] PostgreSQL real PASS
[ ] Alembic real PASS
[ ] migração JSON idempotente PASS
[ ] Object Storage real PASS
[ ] autenticação E2E PASS
[ ] IDOR E2E PASS
[ ] reader isolation PASS
[ ] restart persistence PASS
[ ] rate limiting PASS
[ ] bounded concurrency PASS
[ ] SSRF/egress PASS
[ ] CORS PASS
[ ] HTTPS PASS
[ ] logs sem secrets PASS
[ ] backup + restore PASS
[ ] frontend staging PASS
[ ] scrapers essenciais PASS
[ ] testes automatizados PASS
```

## Achados e commits

Cada correção de staging vira um commit separado e objetivo
(ex.: `fix(db): correct postgres session persistence`). Achados são
classificados como BLOCKER / HIGH / MEDIUM / LOW / OBSERVATION. Mudança
estrutural grande só após reportar e obter aprovação.

Veredito final em UMA destas formas: `STAGING FAILED`,
`STAGING PASSED — NOT READY FOR PRODUCTION` ou `READY FOR PRODUCTION`
(apenas com todos os gates verdes e nenhum BLOCKER).
