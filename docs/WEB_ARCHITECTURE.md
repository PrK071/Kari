# Arquitetura Web do Kari

## Estado e objetivo

O frontend React/Vite deve ser publicado na Vercel e consumir, por HTTPS, um
FastAPI persistente em uma VPS. O backend permanece responsável por integrações,
scrapers e cache. PostgreSQL será a fonte de verdade dos dados de usuário; mídia
persistente poderá usar Object Storage.

```text
Usuario -> Vercel (React/Vite) -> HTTPS -> VPS (FastAPI)
                                         |-> PostgreSQL
                                         |-> fontes remotas e cache
                                         `-> Object Storage (quando habilitado)
```

O FastAPI não foi projetado para Vercel Functions. O subprojeto
`manga_dataset` também não faz parte do request path da API: ele é um coletor
offline com SQLite e upload para Hugging Face.

## Runtimes e capabilities

O backend usa `KARI_RUNTIME=web|desktop`. Desenvolvimento preserva `desktop` por
compatibilidade; produção assume `web` quando a variável não é informada. O
launcher `kari_desktop.py` define `desktop` explicitamente.

`GET /api/capabilities` expõe somente flags não sensíveis para a interface:

| Capability | Web | Desktop |
| --- | --- | --- |
| Fontes remotas | sim | sim |
| Conta/perfil | sim, após hardening | sim |
| Biblioteca de HQ local | não | sim |
| Importação CBZ/CBR/ZIP/PDF | não | sim |
| Importação EPUB/TXT/Markdown | não | sim |
| Sakura/CDP | não | sim |

No runtime web, o backend retorna 404 para recursos locais. Esconder controles no
frontend é apenas apresentação; o guard do backend é a barreira de segurança.

## Persistência alvo

`KARI_PERSISTENCE_BACKEND=json|postgres` seleciona o adaptador. Desenvolvimento
e desktop usam JSON por padrão; produção exige PostgreSQL, `DATABASE_URL` e uma
`KARI_SECRET_KEY` de pelo menos 32 caracteres. O startup da API nunca cria ou
altera tabelas: schema é versionado exclusivamente pelo Alembic.

O schema relacional separa usuários, sessões, perfis, favoritos, biblioteca e
contas OAuth. Tokens de sessão são persistidos somente como SHA-256 do token
opaco de alta entropia; credenciais OAuth são cifradas com chave derivada do
segredo da aplicação.

A migração deve manter adaptadores JSON para o uso local enquanto introduz
interfaces pequenas para `User`, `Session`, `Profile`, `Favorite` e `History`.
A implementação PostgreSQL deve entrar nesta ordem:

1. usuários e identidades externas;
2. sessões, guardando hash do token e expiração indexada;
3. perfis;
4. favoritos e biblioteca;
5. histórico sincronizado.

IDs devem ser estáveis, timestamps em UTC e relações protegidas por chaves
estrangeiras. Migrações de schema precisam de upgrade e downgrade testados. Os
arquivos `catalog.json`, `chapters.json` e capas baixadas são caches
reconstruíveis e não precisam ir para PostgreSQL.

Avatar e backgrounds são dados persistentes. Antes de produção pública, o acesso
a eles deve passar por uma interface de storage com implementações filesystem
(local) e Object Storage. HQs e novels importadas pelo usuário continuam locais
enquanto a capability web estiver desligada.

## Restrições conhecidas

O runtime web usa capítulos autocontidos e descarta o estado mutável depois de
montar cada resposta. Fontes que dependem de arquivos/cache corrente são
rejeitadas em web e continuam no desktop. Scrapers possuem limites globais e por
fonte, além de deduplicação das chamadas idênticas em voo. O backend ainda não
deve ser publicado antes de resolver mídia persistente e concluir os demais
gates de `SECURITY.md`.
