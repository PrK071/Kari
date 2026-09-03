# Segurança do Kari

## Status de publicação

O runtime web já desliga bibliotecas locais e Sakura, mas o backend ainda não está
aprovado para exposição pública. Os bloqueadores atuais são:

- estado global do capítulo corrente;

## Classificação atual das rotas

| Classe | Rotas |
| --- | --- |
| PUBLIC | `/health`, `/api/capabilities`, catálogo, busca, metadados e providers configurados |
| AUTHENTICATED | `/api/auth/me` e logout |
| OWNER_ONLY | perfil, favoritos, biblioteca, mídia de perfil e integrações AniList/MyAnimeList |
| DESKTOP ONLY | biblioteca/import/assets/delete de HQ e light novel local; fontes Sakura |
| ADMIN | nenhuma rota administrativa formal existe |
| INTERNAL | nenhuma rota interna formal existe |

Busca, capítulos, leitura, sync de listas e lookup de autor são públicos hoje e
podem disparar I/O externo pesado. O primeiro limite é por janela deslizante:

| Grupo | Limite inicial | Dimensões |
| --- | --- | --- |
| Registro | 30/hora | IP e nome de login |
| Login | 30/5 minutos | IP e nome de login |
| OAuth/vínculo/sync | 10/10 minutos | IP, usuário e provedor quando disponíveis |
| Home, catálogo e busca | 60/minuto | IP e consulta |
| Plugins, metadados, capítulos, refresh e autor | 30/minuto | IP e fonte/recurso |
| Proxy/imagem do leitor | 120/minuto | IP e URL/índice |

O backend `memory` é deliberadamente limitado a uma instância. A interface
`RateLimitBackend` permite substituir o armazenamento antes de escalar
horizontalmente; múltiplas instâncias não podem usar limites independentes.

## Regras de autenticação alvo

- Toda mutação e leitura privada deve derivar o usuário da sessão, nunca confiar
  em um `profile_id` enviado pelo cliente.
- Novas senhas usam Argon2id e no mínimo 12 caracteres. Um login PBKDF2 legado
  válido faz rehash transparente sem invalidar o usuário.
- PostgreSQL persiste somente SHA-256 dos tokens opacos de alta entropia. O
  adaptador JSON preserva tokens legados somente no runtime local/desktop.
- Logout revoga a sessão atual; uma ação separada deve revogar todas as sessões.
- Tokens OAuth externos nunca entram no payload da API ou nos logs.
- O modelo atual continua usando Bearer em `localStorage`. A troca para cookie
  será atômica com proteção CSRF, `HttpOnly`, `Secure`, `SameSite` e CORS com
  credenciais; não deve existir uma etapa híbrida parcialmente protegida.
- `KARI_SESSION_TTL_HOURS` controla a validade entre 1 e 720 horas. Logout
  revoga somente a sessão apresentada e múltiplas sessões continuam possíveis.

## CORS e configuração

Produção exige `KARI_ALLOWED_ORIGINS` explícito, URLs HTTPS de frontend/backend e
rejeita wildcard. CORS não é controle de autenticação: clientes fora do browser
continuam capazes de chamar a API.

Secrets vivem apenas no ambiente da VPS. `.env` é ignorado; `.env.example`
contém somente nomes e placeholders. Nunca registrar senha, token, cookie,
`Authorization`, client secret ou URL que contenha credenciais.

## Próximos controles

O proxy de imagens valida DNS, rejeita endereços não públicos/credenciais,
revalida redirects, limita portas no runtime web, restringe o tamanho a 25 MB e
aceita somente formatos raster reconhecidos por assinatura. A VPS ainda deve
aplicar regras de egress como defesa adicional contra DNS rebinding.

As rotas de dados privados usam uma dependência FastAPI central para validar o
Bearer e comparar o `profile_id` com a identidade da sessão. No runtime web,
ausência ou invalidade do token resulta em 401 e acesso cruzado resulta em 403.
O runtime desktop preserva perfis anônimos apenas quando nenhum bearer é enviado.

1. IDs de sessão de leitura isolados e limites globais por scraper;
2. middleware de acesso com método, path normalizado, status e duração;
3. testes de CORS, auth, IDOR, SSRF, upload e ambos os runtimes.
