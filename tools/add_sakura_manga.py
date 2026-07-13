"""Pesquisa uma obra no Sakura Mangas e a adiciona ao catalogo da home.

Ao inves de crawlear TODAS as obras (o que derruba o IP no Cloudflare), este
script deixa voce buscar uma obra especifica pelo nome, escolher qual, e grava
ela em backend/.cache/custom_catalog.json. A home mescla esse arquivo ao
CURATED_CATALOG automaticamente (recarrega pelo mtime, sem reiniciar).

Pre-requisito: o browser-bridge do Sakura precisa estar aberto e com o
Cloudflare resolvido:

    python tools/start_sakura_browser.py

Uso:
    # modo interativo: busca e pergunta qual escolher
    python tools/add_sakura_manga.py "leviathan"

    # escolhe direto o resultado N (sem perguntar)
    python tools/add_sakura_manga.py "leviathan" --index 1

    # adiciona por URL direta (sem busca)
    python tools/add_sakura_manga.py --url https://sakuramangas.org/obras/leviathan-kuroi-shiro/

    # lista o que ja foi adicionado / remove por URL
    python tools/add_sakura_manga.py --show
    python tools/add_sakura_manga.py --remove https://sakuramangas.org/obras/leviathan-kuroi-shiro/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import backend.main as app  # noqa: E402


def _load() -> list[dict]:
    path = app.CUSTOM_CATALOG_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    path = app.CUSTOM_CATALOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _entry_from_result(result: dict, section: str | None) -> dict:
    genres = [g for g in (result.get("genres") or []) if str(g or "").strip()]
    return {
        "title": str(result.get("title") or "").strip(),
        "aliases": [str(result.get("title") or "").strip()],
        "url": str(result.get("url") or "").strip(),
        "poster": str(result.get("poster") or "").strip(),
        "provider": "sakura",
        "source": "sakura",
        "language": "pt-br",
        "section": section or (genres[0] if genres else "Sakura Mangas"),
        "genres": genres[:6],
    }


def _add_entry(entry: dict) -> str:
    if not entry.get("url") or not entry.get("title"):
        raise SystemExit("Obra invalida (sem url/titulo).")
    entries = _load()
    for existing in entries:
        if str(existing.get("url") or "").strip() == entry["url"]:
            existing.update(entry)
            _save(entries)
            return "atualizada"
    entries.append(entry)
    _save(entries)
    return "adicionada"


def _resolve_from_url(url: str, section: str | None) -> dict:
    """Raspa a obra pela URL p/ preencher titulo/poster/generos."""
    manga, _chapters = app.reader._sakura_scrape_manga(url)
    return {
        "title": str(manga.get("title") or "").strip(),
        "aliases": [str(manga.get("title") or "").strip()],
        "url": url.strip(),
        "poster": str(manga.get("poster") or "").strip(),
        "provider": "sakura",
        "source": "sakura",
        "language": "pt-br",
        "section": section or (list(manga.get("genres") or [None])[0] or "Sakura Mangas"),
        "genres": [g for g in (manga.get("genres") or []) if str(g or "").strip()][:6],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Busca e adiciona uma obra do Sakura Mangas a home.")
    parser.add_argument("keyword", nargs="?", default="", help="Nome da obra para buscar no Sakura.")
    parser.add_argument("--url", default="", help="Adiciona direto por URL da obra (pula a busca).")
    parser.add_argument("--index", type=int, default=None, help="Escolhe o resultado N da busca (sem perguntar).")
    parser.add_argument("--section", default=None, help="Secao/categoria na home (padrao: 1o genero).")
    parser.add_argument("--limit", type=int, default=10, help="Quantidade de resultados na busca.")
    parser.add_argument("--show", action="store_true", help="Lista as obras ja adicionadas.")
    parser.add_argument("--remove", default="", help="Remove uma obra adicionada pela URL.")
    args = parser.parse_args()

    if args.show:
        entries = _load()
        if not entries:
            print("Nenhuma obra adicionada ainda.")
            return 0
        for i, e in enumerate(entries, 1):
            print(f"{i:2}. {e.get('title')}  [{e.get('provider')}]  {e.get('url')}")
        return 0

    if args.remove:
        entries = _load()
        kept = [e for e in entries if str(e.get("url") or "").strip() != args.remove.strip()]
        if len(kept) == len(entries):
            print("URL nao encontrada no catalogo customizado.")
            return 1
        _save(kept)
        print(f"Removida. Restam {len(kept)} obra(s).")
        return 0

    if args.url:
        print(f"Raspando obra por URL: {args.url}")
        try:
            entry = _resolve_from_url(args.url, args.section)
        except Exception as exc:
            print(f"Falha ao raspar: {exc}")
            print("Dica: abra o bridge com  python tools/start_sakura_browser.py  e resolva o Cloudflare.")
            return 2
        status = _add_entry(entry)
        print(f"[{status}] {entry['title']}  ->  {entry['url']}")
        print("Pronto. Recarregue a home (o backend mescla sozinho pelo mtime).")
        return 0

    if not args.keyword.strip():
        parser.error("Informe um nome para buscar, ou use --url / --show / --remove.")

    print(f"Buscando '{args.keyword}' no Sakura Mangas...")
    try:
        res = app.reader.search_sakura(args.keyword, limit=args.limit)
    except Exception as exc:
        print(f"Falha na busca: {exc}")
        print("Dica: abra o bridge com  python tools/start_sakura_browser.py  e resolva o Cloudflare.")
        return 2

    results = res.get("results") or []
    if not results:
        print("Nenhum resultado. Tente outro termo.")
        return 1

    print(f"\n{len(results)} resultado(s):")
    for i, r in enumerate(results, 1):
        badges = ", ".join(r.get("genres") or [])[:60]
        print(f"{i:2}. {r.get('title')}  ({r.get('status') or '-'})  {badges}")
        print(f"     {r.get('url')}")

    if args.index is not None:
        choice = args.index
    else:
        try:
            raw = input("\nEscolha o numero (Enter=1, q=cancelar): ").strip()
        except EOFError:
            raw = ""
        if raw.lower() == "q":
            print("Cancelado.")
            return 0
        choice = int(raw) if raw else 1

    if not (1 <= choice <= len(results)):
        print("Indice invalido.")
        return 1

    entry = _entry_from_result(results[choice - 1], args.section)
    status = _add_entry(entry)
    print(f"\n[{status}] {entry['title']}  ->  {entry['url']}")
    print("Pronto. Recarregue a home (o backend mescla sozinho pelo mtime).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
