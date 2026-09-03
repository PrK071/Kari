# Segurança do Kari

## Status de publicação

O runtime web já desliga bibliotecas locais e Sakura, mas o backend ainda não está
aprovado para exposição pública. Os bloqueadores atuais são:

- rotas de perfil e integrações sem autorização por proprietário;
- estado global do capítulo corrente;
- usuários, sessões e tokens OAuth persistidos em JSON;
- ausência de rate limiting nos endpoints de autenticação e scraping.

## Classificação atual das rotas

| Classe | Rotas |
| --- | --- |
| PUBLIC | `/health`, `/api/capabilities`, catálogo, busca, metadados e providers configurados |
| AUTHENTICATED | `/api/auth/me` e logout (parcialmente); perfis ainda precisam ser migrados para esta classe |
| DESKTOP ONLY | biblioteca/import/assets/delete de HQ e light novel local; fontes Sakura |
| ADMIN | nenhuma rota administrativa formal existe |
| INTERNAL | nenhuma rota interna formal existe |

Busca, capítulos, leitura, sync de listas e lookup de autor são públicos hoje e
podem disparar I/O externo pesado. Eles precisam de rate limit e orçamento de
concorrência antes da publicação.

## Regras de autenticação alvo

- Toda mutação e leitura privada deve derivar o usuário da sessão, nunca confiar
  em um `profile_id` enviado pelo cliente.
- Senhas devem usar hash moderno com versão e possibilidade de rehash.
- O servidor deve persistir somente hash de tokens de sessão.
- Logout revoga a sessão atual; uma ação separada deve revogar todas as sessões.
- Tokens OAuth externos nunca entram no payload da API ou nos logs.
- Se cookies forem adotados, usar `HttpOnly`, `Secure` e `SameSite` compatível
  com os domínios reais. O modelo atual usa Bearer em `localStorage`.

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

1. autorização por dono e repositórios de persistência;
2. rate limiter com interface substituível por Redis/serviço externo;
3. IDs de sessão de leitura isolados e limites globais por scraper;
4. middleware de acesso com método, path normalizado, status e duração;
5. testes de CORS, auth, IDOR, SSRF, upload e ambos os runtimes.
