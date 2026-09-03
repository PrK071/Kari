from __future__ import annotations

import copy
import time
import mimetypes
import html
import json
import logging
import os
import base64
import binascii
import secrets
import hashlib
import io
import random
import re
import socket
import threading
import tempfile
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse

import requests
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from requests.adapters import HTTPAdapter
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import load_settings
from backend.concurrency import BoundedWorkCoordinator
from backend.network_security import UnsafeRemoteURLError, validate_public_http_url
from backend.rate_limit import MemoryRateLimitBackend, RateLimiter, RateLimitPolicy
from backend.persistence import (
    ProfileRepository,
    SessionRepository,
    UserRepository,
    build_repositories,
)

from schemas import (
    HomeResponse,
    MangaHomeItem,
    MangaSearchItem,
    SearchResponse,
)

from reader_server import (
    ANILIST_GRAPHQL_URL,
    DEFAULT_HEADERS,
    MangaReader,
    fuzzy_match_score,
    normalize_match_text,
)
from plugins.hq_local import (
    MAX_UPLOAD_BYTES as HQ_MAX_UPLOAD_BYTES,
    SUPPORTED_EXTENSIONS as HQ_SUPPORTED_EXTENSIONS,
)
from plugins.light_novel_local import (
    MAX_UPLOAD_BYTES as NOVEL_MAX_UPLOAD_BYTES,
    SUPPORTED_EXTENSIONS as NOVEL_SUPPORTED_EXTENSIONS,
)

try:  # Imagens de perfil (avatar/background) sao validadas/normalizadas via Pillow.
    from PIL import Image, UnidentifiedImageError
except Exception:  # noqa: BLE001 - Pillow e requisito, mas nao quebra o import se faltar.
    Image = None
    UnidentifiedImageError = Exception

# Credenciais OAuth (AniList/MyAnimeList) vivem no .env da raiz do projeto.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # noqa: BLE001 - sem dotenv o app ainda roda (OAuth fica desligado).
    pass


settings = load_settings()
rate_limit_backend = MemoryRateLimitBackend()
rate_limiter = RateLimiter(rate_limit_backend)
scraper_coordinator = BoundedWorkCoordinator(
    max_global=settings.scraper_max_concurrency,
    max_per_source=settings.scraper_max_per_source,
)

REGISTER_RATE_LIMIT = RateLimitPolicy(limit=30, window_seconds=60 * 60)
LOGIN_RATE_LIMIT = RateLimitPolicy(limit=30, window_seconds=5 * 60)
SEARCH_RATE_LIMIT = RateLimitPolicy(limit=60, window_seconds=60)
EXPENSIVE_RATE_LIMIT = RateLimitPolicy(limit=30, window_seconds=60)
IMAGE_RATE_LIMIT = RateLimitPolicy(limit=120, window_seconds=60)
OAUTH_RATE_LIMIT = RateLimitPolicy(limit=10, window_seconds=10 * 60)


CATALOG_CACHE_TTL_SECONDS = 30 * 60
SEARCH_CACHE_TTL_SECONDS = 5 * 60
SOURCE_RESOLUTION_CACHE_TTL_SECONDS = 10 * 60
CHAPTER_COUNT_CACHE_TTL_SECONDS = 20 * 60
CHAPTERS_CACHE_TTL_SECONDS = 10 * 60
CHAPTER_PAYLOAD_CACHE_TTL_SECONDS = 30 * 60
MANGA_META_CACHE_TTL_SECONDS = 12 * 60 * 60
IMAGE_CACHE_TTL_SECONDS = 15 * 60
IMAGE_CACHE_MAX_ITEMS = 1000
REMOTE_IMAGE_MAX_BYTES = 25 * 1024 * 1024
REMOTE_IMAGE_MAX_REDIRECTS = 3
ANILIST_CACHE_TTL_SECONDS = 12 * 60 * 60
KITSU_CACHE_TTL_SECONDS = 12 * 60 * 60
MANGAUPDATES_CACHE_TTL_SECONDS = 24 * 60 * 60
MANGAUPDATES_API_URL = "https://api.mangaupdates.com/v1"
MANGAUPDATES_REQUEST_GAP_SECONDS = 0.5
MANGAUPDATES_REQUEST_ATTEMPTS = 3
TRANSLATION_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_LIMIT = 80
SOURCE_SEARCH_TIMEOUT_SECONDS = 4.0
# Sakura passa pelo navegador (CDP) e nao cabe no batch rapido de 4s. Quando a
# busca nao acha a obra nas fontes rapidas, roda um passe DEDICADO no Sakura com
# este orcamento maior (obra especifica pedida em tempo real).
SAKURA_LIVE_SEARCH_TIMEOUT_SECONDS = 20.0
SEARCH_MAX_PER_SOURCE = 18
SOURCE_RESOLUTION_TIMEOUT_SECONDS = 5.0
CATALOG_SNAPSHOT_TTL_SECONDS = 6 * 60 * 60
KARI_DATA_DIR = Path(
    os.environ.get("KARI_DATA_DIR")
    or (Path(__file__).resolve().parent / ".cache")
).resolve()
KARI_DATA_DIR.mkdir(parents=True, exist_ok=True)

CATALOG_SNAPSHOT_PATH = KARI_DATA_DIR / "catalog.json"
CATALOG_SNAPSHOT_VERSION = 12
CHAPTER_AUDIT_FAILURE_TTL_SECONDS = 5 * 60
# Timeout POR FONTE no audit de capitulos da home. Uma fonte que trava (browser
# offline, provider lento, Cloudflare) vira falha registrada em vez de segurar o
# worker e travar o audit inteiro (deixando cards eternamente "Verificando").
CHAPTER_AUDIT_TARGET_TIMEOUT_SECONDS = 15.0
# Teto do lote inteiro: mesmo que alguma fonte ignore o timeout individual, o
# audit sempre termina e libera a flag global p/ o proximo ciclo.
CHAPTER_AUDIT_BATCH_TIMEOUT_SECONDS = 60.0
PARTNER_CATALOG_LIMIT = 24

# Capitulos basicos cacheados em disco (id/numero/titulo/lingua) -> rota local,
# sem fetch externo a cada clique. TTL longo; sobrevive a restart.
CHAPTERS_DISK_TTL_SECONDS = 24 * 60 * 60
CHAPTERS_CACHE_VERSION = 6
CHAPTERS_SNAPSHOT_PATH = KARI_DATA_DIR / "chapters.json"
PROFILES_STORE_PATH = KARI_DATA_DIR / "profiles.json"
# Contas de usuario (cadastro/login) e sessoes. Local, senha com PBKDF2.
USERS_STORE_PATH = KARI_DATA_DIR / "users.json"
AUTH_TOKENS_PATH = KARI_DATA_DIR / "tokens.json"
PBKDF2_ITERATIONS = 200_000
_password_hasher = PasswordHasher()

repositories = build_repositories(
    backend=settings.persistence_backend,
    database_url=settings.database_url,
    secret_key=settings.secret_key,
    users_path=lambda: USERS_STORE_PATH,
    profiles_path=lambda: PROFILES_STORE_PATH,
    sessions_path=lambda: AUTH_TOKENS_PATH,
)
profile_repository: ProfileRepository = repositories.profiles
user_repository: UserRepository = repositories.users
session_repository: SessionRepository = repositories.sessions
# Obras adicionadas manualmente (ex.: via tools/add_sakura_manga.py). Mescladas
# ao CURATED_CATALOG p/ aparecerem na home como as fontes fixas.
CUSTOM_CATALOG_PATH = KARI_DATA_DIR / "custom_catalog.json"
PIECEPROJECT_OUTAGE_TTL_SECONDS = 60
ONE_PIECE_PIECEPROJECT_URL = "https://www.pieceproject.xyz/"
ONE_PIECE_MANGALIVRE_URL = "https://mangalivre.blog/manga/one-piece/"

# Resiliencia da busca de capitulos: retry com backoff exponencial + rotacao de UA.
CHAPTERS_FETCH_ATTEMPTS = 2
CHAPTERS_BACKOFF_BASE = 2.0
CHAPTERS_BACKOFF_START = 0.35

DIRECT_IMAGE_HOSTS = {
    "uploads.mangadex.org",
    "cdn.mugiverso.com",
    "51.79.78.152",
    "static.hq-now.com",
    "media.fliptru.com.br",
    "assets.novelmania.com.br",
    "i.imgur.com",
    "i.ibb.co",
    "i.postimg.cc",
}

logger = logging.getLogger("mangatemp")
logger.setLevel(settings.log_level)


def _safe_error(exc: Exception) -> str:
    """Retorna somente a classe; mensagens externas podem conter tokens/URLs."""
    return type(exc).__name__


def _enforce_rate_limit(
    request: Request,
    scope: str,
    policy: RateLimitPolicy,
    *,
    user_id: str = "",
    resource: str = "",
) -> None:
    client_ip = request.client.host if request.client else "unknown"
    dimensions = {"ip": client_ip}
    if user_id:
        dimensions["user"] = user_id
    if resource:
        dimensions["resource"] = resource
    decision = rate_limiter.check(scope, policy, dimensions)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Muitas requisicoes. Tente novamente mais tarde.",
            headers={"Retry-After": str(decision.retry_after)},
        )


def _scraper_source_name(source: str) -> str:
    provider = _guess_provider({"url": source})
    return provider or (urlparse(source).hostname or source or "unknown")

# User-Agents reais p/ rotacionar e fugir de filtros antibot/rate-limit.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

# Arquivos estaticos servidos pelo FastAPI (capas baixadas na raspagem ficam aqui,
# eliminando o proxy de imagem em tempo de execucao na home).
STATIC_DIR = Path(
    os.environ.get("KARI_STATIC_DIR")
    or (Path(__file__).resolve().parent / "static")
).resolve()
COVERS_DIR = STATIC_DIR / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)

# Imagens de perfil (avatar/background) enviadas pelo usuario ficam salvas
# localmente em static/profiles/<profile_id>/ e servidas por /static/profiles/...
PROFILE_MEDIA_DIR = STATIC_DIR / "profiles"
PROFILE_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Backgrounds pre-definidos (o leitor escolhe). Qualquer imagem/video colocado
# em static/backgrounds/ aparece automaticamente no seletor do perfil.
BACKGROUNDS_DIR = STATIC_DIR / "backgrounds"
BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
BACKGROUND_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
BACKGROUND_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}

# Limites de upload de imagem de perfil (bytes) e dimensao maxima (px).
PROFILE_AVATAR_MAX_BYTES = 4 * 1024 * 1024
PROFILE_BACKGROUND_MAX_BYTES = 8 * 1024 * 1024
PROFILE_AVATAR_MAX_DIM = 512
PROFILE_BACKGROUND_MAX_DIM = 2560
# Background da HOME e uma imagem de tela cheia; preserva ate 4K e aceita ate 24MB.
PROFILE_HOME_BACKGROUND_MAX_BYTES = 24 * 1024 * 1024
PROFILE_HOME_BACKGROUND_MAX_DIM = 3840
# Background animado (mp4/webm/gif). Salvo bruto, sem reprocessar.
PROFILE_VIDEO_MAX_BYTES = 64 * 1024 * 1024

# ---------------------------------------------------------------------------
# OAuth de contas externas (AniList / MyAnimeList).
# Credenciais vem do .env; sem elas o vinculo fica desabilitado (HTTP 503).
# ---------------------------------------------------------------------------
BACKEND_BASE_URL = settings.backend_url
FRONTEND_BASE_URL = settings.frontend_url

ANILIST_CLIENT_ID = os.environ.get("ANILIST_CLIENT_ID", "").strip()
ANILIST_CLIENT_SECRET = os.environ.get("ANILIST_CLIENT_SECRET", "").strip()
ANILIST_AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
ANILIST_TOKEN_URL = "https://anilist.co/api/v2/oauth/token"

MAL_CLIENT_ID = os.environ.get("MAL_CLIENT_ID", "").strip()
MAL_CLIENT_SECRET = os.environ.get("MAL_CLIENT_SECRET", "").strip()
MAL_AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
MAL_TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
MAL_USER_URL = "https://api.myanimelist.net/v2/users/@me"
MAL_MANGA_LIST_URL = "https://api.myanimelist.net/v2/users/@me/mangalist"

# Discord como metodo de LOGIN/CADASTRO (cria/entra numa conta local).
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL = "https://discord.com/api/users/@me"


# Google como metodo de LOGIN/CADASTRO (cria/entra numa conta local).
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _discord_configured() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)

def _discord_redirect_uri() -> str:
    return f"{BACKEND_BASE_URL}/api/auth/discord/callback"


def _google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _google_redirect_uri() -> str:
    return f"{BACKEND_BASE_URL}/api/auth/google/callback"

OAUTH_PROVIDERS = ("anilist", "myanimelist")
OAUTH_STATE_TTL_SECONDS = 10 * 60
PROVIDER_LABELS = {"anilist": "AniList", "myanimelist": "MyAnimeList"}

def _oauth_redirect_uri(provider: str) -> str:
    return f"{BACKEND_BASE_URL}/api/oauth/{provider}/callback"

def _oauth_provider_configured(provider: str) -> bool:
    if provider == "anilist":
        return bool(ANILIST_CLIENT_ID and ANILIST_CLIENT_SECRET)
    if provider == "myanimelist":
        return bool(MAL_CLIENT_ID)  # MAL aceita client publico (secret opcional).
    return False


# Placeholder "Sem Capa" servido quando a obra nao tem capa local valida.
# Fica em static/ (fora de covers/) p/ nao ser limpo junto com o cache de capas.
PLACEHOLDER_PATH = STATIC_DIR / "placeholder.svg"
PLACEHOLDER_URL = "/static/placeholder.svg"
_PLACEHOLDER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="320" height="460" viewBox="0 0 320 460">
  <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#27272a"/><stop offset="1" stop-color="#161618"/></linearGradient></defs>
  <rect width="320" height="460" fill="url(#g)"/>
  <g fill="none" stroke="#52525b" stroke-width="6" stroke-linecap="round" stroke-linejoin="round">
    <rect x="100" y="140" width="120" height="160" rx="12"/>
    <path d="M130 140 V300 M170 140 V300"/>
  </g>
  <text x="160" y="360" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif"
        font-size="30" font-weight="700" fill="#a1a1aa">Sem Capa</text>
</svg>
"""


def _ensure_placeholder() -> None:
    try:
        if not PLACEHOLDER_PATH.exists():
            PLACEHOLDER_PATH.write_text(_PLACEHOLDER_SVG, encoding="utf-8")
    except Exception:
        pass


_ensure_placeholder()

MANGADEX_GENRES = {
    "Acao": "Action",
    "Aventura": "Adventure",
    "Comedia": "Comedy",
    "Drama": "Drama",
    "Fantasia": "Fantasy",
    "Gore": "Gore",
    "Thriller": "Thriller",
    "Sobrenatural": "Supernatural",
    "Misterio": "Mystery",
    "Psicologico": "Psychological",
    "Romance": "Romance",
    "Terror": "Horror",
    "Isekai": "Isekai",
    "Sci-Fi": "Sci-Fi",
    "Slice of Life": "Slice of Life",
}

SOURCE_LABELS = {
    "hq_local": "HQ Local",
    "hq_now": "HQ Now",
    "fliptru": "Fliptru",
    "light_novel_local": "Light Novel Local",
    "novel_mania": "Novel Mania",
    "central_novel": "Central Novel",
    "tensura_fan": "Tensura Fan",
    "pleiades_translations": "Pleiades Translations",
    "mangadex": "MangaDex",
    "mangalivre": "MangaLivre",
    "mangasbrasuka": "MangasBrasuka",
    "pieceproject": "One Piece Project",
    "toomics": "Toomics",
    "anilist": "AniList",
    "nexus": "Nexus Mangas",
    "mangageek": "MangaGeek",
    "mangakatana": "MangaKatana",
    "sakura": "Sakura Mangas",
}

SEARCH_SOURCES = ["fliptru", "nexus", "mangageek", "sakura", "mangakatana", "mangasbrasuka", "mangalivre", "mangadex"]
PT_COMPLETE_SOURCES = ["fliptru", "nexus", "mangageek", "sakura", "mangasbrasuka", "mangalivre"]
ACTIVE_CATALOG_SOURCES = ["MangaDex", "Fliptru", "Nexus Mangas", "MangaGeek", "MangaKatana", "MangasBrasuka", "MangaLivre"]
SEARCH_CACHE_VERSION = 10
SEARCH_COVER_RECOVERY_LIMIT = 4

SOURCE_RELIABILITY = {
    "hq_local": 1.0,
    "hq_now": 0.96,
    "fliptru": 0.95,
    "light_novel_local": 1.0,
    "novel_mania": 0.96,
    "central_novel": 0.95,
    "tensura_fan": 0.98,
    "pleiades_translations": 0.96,
    "pieceproject": 0.99,
    "sakura": 0.98,
    "nexus": 0.98,
    "mangageek": 0.96,
    "mangakatana": 0.94,
    "mangasbrasuka": 0.97,
    "mangalivre": 0.90,
    "toomics": 0.78,
    "mangadex": 0.72,
}

# Obras cuja fonte preferida foi confirmada manualmente. Mantem lista externa e
# leitura no mesmo provider, mesmo se um catalogo antigo ainda tiver MangaDex.
SYNC_TITLE_SOURCE_OVERRIDES = {
    "homunculus": {
        "source": "MangaKatana",
        "provider": "mangakatana",
        "source_url": "https://mangakatana.com/manga/homunculus.13059",
        "chapter_languages": ["en"],
    },
    "gantz g": {
        "source": "MangaKatana",
        "provider": "mangakatana",
        "source_url": "https://mangakatana.com/manga/gantzg.473",
        "chapter_languages": ["en"],
    },
    "saint seiya the lost canvas meiou shinwa": {
        "source": "MangaGeek",
        "provider": "mangageek",
        "source_url": "mangageek://manga/4288",
        "chapter_languages": ["pt-br"],
    },
}


def _confirmed_source_override(title: str) -> dict | None:
    override = SYNC_TITLE_SOURCE_OVERRIDES.get(normalize_match_text(str(title or "")))
    return dict(override) if isinstance(override, dict) else None

SPARSE_CHAPTER_THRESHOLD = 8
MIN_SOURCE_RELEVANCE = 0.45

CURATED_CATALOG = [
    {
        "title": "Tensei Shitara Slime Datta Ken",
        "aliases": [
            "Tensei Shitara Slime Datta Ken",
            "Tensei Shitara Slime Datta Ken Manga",
            "That Time I Got Reincarnated as a Slime",
            "Slime Datta Ken",
            "Tensei Slime",
        ],
        "url": "https://mangasbrasuka.com.br/manga/tensei-shitara-slime-datta-ken/",
        "poster": "https://cdn.mugiverso.com/mangasbrasuka/wp-content/uploads/2026/02/that-time-i-got-reincarnated-as-a-slime-22-capa.webp",
        "provider": "mangasbrasuka",
        "section": "Fantasia",
        "genres": ["Aventura", "Fantasia", "Comedia"],
    },
    {
        "title": "Soul Eater",
        "aliases": ["Soul Eater"],
        "url": "https://mangalivre.blog/manga/soul-eater/",
        "poster": "https://mangalivre.blog/wp-content/uploads/2025/04/ae5b4ce8-a50d-4bbb-9cd6-7456b97fdecd.jpg.512.jpg",
        "provider": "mangalivre",
        "section": "Acao",
        "genres": ["Acao", "Fantasia", "Comedia"],
    },
    {
        "title": "Moby Dick",
        "aliases": ["Moby Dick", "Moby-Dick"],
        "url": "https://mangasbrasuka.com.br/manga/moby-dick/",
        "poster": "https://cdn.mugiverso.com/mangasbrasuka/wp-content/uploads/2026/02/Moby-Dick.webp",
        "provider": "mangasbrasuka",
        "section": "Drama",
        "genres": ["Drama", "Acao", "Manhwa"],
    },
    {
        "title": "One Piece",
        "aliases": ["One Piece"],
        "url": ONE_PIECE_PIECEPROJECT_URL,
        "poster": "https://i.ibb.co/NnFxkGJ/manga1130.jpg",
        "provider": "pieceproject",
        "section": "Aventura",
        "genres": ["Acao", "Aventura", "Comedia"],
    },
    {
        "title": "One Punch-Man",
        "aliases": ["One Punch-Man", "One Punch Man"],
        "url": "https://mangasbrasuka.com.br/manga/one-punch-man/",
        "provider": "mangasbrasuka",
        "section": "Acao",
        "genres": ["Acao", "Comedia", "Super-heroi"],
    },
    {
        "title": "Revenge of the Baskerville Bloodhound",
        "aliases": [
            "Revenge of the Baskerville Bloodhound",
            "A Vinganca do Cao de Caca dos Baskerville",
            "Vinganca do Cao de Caca dos Baskerville",
        ],
        "url": "https://mangasbrasuka.com.br/manga/a-vinganca-do-cao-de-caca-dos-baskerville/",
        "provider": "mangasbrasuka",
        "section": "Acao",
        "genres": ["Acao", "Fantasia", "Reencarnacao"],
    },
]


@dataclass
class CacheEntry:
    saved_at: float
    data: dict


@dataclass
class ImageCacheEntry:
    saved_at: float
    content: bytes
    media_type: str


reader = MangaReader(
    SimpleNamespace(
        librewolf_path=None,
        show_browser=False,
        timeout=12,
        readfull_api_url="https://readfullapi.herokuapp.com",
        dragontea_browser="edge",
    )
)


DESKTOP_ONLY_PROVIDERS = {"hq_local", "light_novel_local", "sakura"}


def _provider_enabled(provider: str) -> bool:
    normalized = provider.strip().casefold()
    if not settings.is_web:
        return True
    if normalized in DESKTOP_ONLY_PROVIDERS:
        return False
    if normalized == "mangageek":
        return urlparse(reader.mangageek_api_base).scheme.casefold() == "https"
    return True


def _require_desktop_capability() -> None:
    if settings.is_web:
        raise HTTPException(status_code=404, detail="Recurso disponivel somente no Kari local.")


def _ensure_source_allowed(source_url: str) -> None:
    if not settings.is_web:
        return
    if (
        reader.hq_plugin.is_source(source_url)
        or reader.light_novel_plugin.is_source(source_url)
        or reader._is_sakura_source(source_url)
        or (reader._is_mangageek_source(source_url) and not _provider_enabled("mangageek"))
    ):
        _require_desktop_capability()


def _tcp_port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _sakura_search_available() -> bool:
    # Sakura precisa de navegador/CDP. Sem isso, search vira abertura pesada de browser.
    if settings.is_web:
        return False
    cdp_url = str(getattr(reader, "sakura_cdp_url", "") or "").strip()
    if not cdp_url:
        return False
    parsed = urlparse(cdp_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return _tcp_port_open(host, port)


def _pt_complete_sources() -> list[str]:
    return [source for source in PT_COMPLETE_SOURCES if _provider_enabled(source)]


def _search_sources() -> list[str]:
    sources = [source for source in SEARCH_SOURCES if _provider_enabled(source)]
    if not _sakura_search_available():
        sources = [source for source in sources if source != "sakura"]
    return sources


def _search_source_limit(limit: int) -> int:
    return min(max(limit, 8), SEARCH_MAX_PER_SOURCE)


def _number_value(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _matching_chapter_url(chapters_payload: dict, chapter_number: str | float | None) -> str:
    wanted = _number_value(chapter_number)
    if wanted is None:
        return ""
    for chapter in chapters_payload.get("chapters") or []:
        number = _number_value(chapter.get("number"))
        if number is not None and abs(number - wanted) < 0.0001:
            return str(chapter.get("url") or "")
    return ""


def _coordinated_chapter_metadata(
    chapter_url: str,
    *,
    include_neighbors: bool = True,
) -> dict:
    return scraper_coordinator.run(
        _scraper_source_name(chapter_url),
        f"chapter-open:{chapter_url}:{include_neighbors}",
        lambda: reader.chapter_metadata(
            chapter_url,
            cache_pages=False,
            include_source_urls=True,
            include_neighbors=include_neighbors,
            retain_state=not settings.is_web,
        ),
    )


def _open_fallback_chapter(
    fallback_source_url: str,
    lang: str,
    chapter_number: str | float | None,
    original_source: str,
    original_error: Exception,
) -> dict | None:
    fallback_source = unquote(fallback_source_url or "").strip()
    if not fallback_source or fallback_source == original_source:
        return None
    chapters_payload = _resilient_list_chapters(
        fallback_source,
        lang,
        str(chapter_number or ""),
    )
    selected_url = _matching_chapter_url(chapters_payload, chapter_number)
    if not selected_url:
        return None
    payload = _coordinated_chapter_metadata(selected_url)
    payload["fallback"] = {
        "from": original_source,
        "to": fallback_source,
        "reason": str(original_error),
        "chapter_number": str(chapter_number or ""),
    }
    return payload


catalog_cache: CacheEntry | None = None
catalog_refresh_lock = threading.Lock()
catalog_refreshing = False
search_cache: dict[str, CacheEntry] = {}
source_resolution_cache: dict[str, CacheEntry] = {}
source_outage_cache: dict[str, CacheEntry] = {}
chapter_count_cache: dict[str, CacheEntry] = {}
chapters_cache: dict[str, CacheEntry] = {}
chapter_audit_failures: dict[str, CacheEntry] = {}
chapter_payload_cache: dict[str, CacheEntry] = {}
manga_meta_cache: dict[str, CacheEntry] = {}
anilist_cache: dict[str, CacheEntry] = {}
author_cache: dict[str, CacheEntry] = {}
author_profile_cache: dict[str, CacheEntry] = {}
kitsu_cache: dict[str, CacheEntry] = {}
mangaupdates_series_cache: dict[str, CacheEntry] = {}
mangaupdates_author_cache: dict[str, CacheEntry] = {}
translation_cache: dict[str, CacheEntry] = {}
image_cache: dict[str, ImageCacheEntry] = {}
image_inflight: dict[str, threading.Event] = {}

mangaupdates_request_lock = threading.Lock()
mangaupdates_last_request_at = 0.0

_chapters_disk_lock = threading.Lock()
_chapters_cache_lock = threading.RLock()
_chapters_refresh_lock = threading.Lock()
_chapters_refreshing: set[str] = set()
_source_resolution_refresh_lock = threading.Lock()
_source_resolution_refreshing: set[str] = set()
_chapter_payload_cache_lock = threading.Lock()
_image_cache_lock = threading.Lock()
_chapter_audit_local = threading.local()
_home_chapter_audit_lock = threading.Lock()
_home_chapter_audit_running = False
# Fila compartilhada: chamadas novas nao podem ser descartadas quando o audit da
# home ja esta rodando. A tela que o usuario abriu entra na frente da fila.
_home_chapter_audit_pending: dict[str, dict] = {}
HOME_CHAPTER_AUDIT_BATCH_SIZE = 24

_image_http = requests.Session()
_image_http_adapter = HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=0)
_image_http.mount("http://", _image_http_adapter)
_image_http.mount("https://", _image_http_adapter)


def _load_chapters_snapshot() -> None:
    """Carrega o cache de capitulos do disco p/ a memoria no startup."""
    try:
        if not CHAPTERS_SNAPSHOT_PATH.exists():
            return
        raw = json.loads(CHAPTERS_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        with _chapters_cache_lock:
            for key, entry in (raw or {}).items():
                if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
                    chapters_cache[key] = CacheEntry(float(entry.get("saved_at") or 0), entry["data"])
    except Exception:
        pass


def _save_chapters_snapshot() -> None:
    """Persiste o cache de capitulos no disco (.cache/chapters.json)."""
    try:
        with _chapters_disk_lock:
            CHAPTERS_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _chapters_cache_lock:
                payload = {
                    key: {"saved_at": entry.saved_at, "data": entry.data}
                    for key, entry in chapters_cache.items()
                }
            tmp = CHAPTERS_SNAPSHOT_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CHAPTERS_SNAPSHOT_PATH)
    except Exception:
        pass


def _refresh_chapters_source_payload(cache_key: str, source: str, lang: str) -> None:
    try:
        payload = _resilient_list_chapters(source, lang)
        with _chapters_cache_lock:
            chapters_cache[cache_key] = CacheEntry(time.time(), dict(payload))
        _save_chapters_snapshot()
    except Exception as exc:
        logger.warning(
            "Falha ao atualizar capitulos source=%s error=%s",
            _scraper_source_name(source),
            _safe_error(exc),
        )
    finally:
        with _chapters_refresh_lock:
            _chapters_refreshing.discard(cache_key)


def _schedule_chapters_refresh(cache_key: str, source: str, lang: str) -> None:
    with _chapters_refresh_lock:
        if cache_key in _chapters_refreshing:
            return
        _chapters_refreshing.add(cache_key)
    threading.Thread(
        target=_refresh_chapters_source_payload,
        args=(cache_key, source, lang),
        daemon=True,
    ).start()


def _load_chapters_source_payload(source: str, lang: str) -> dict:
    cache_key = _chapters_cache_key(source, lang)
    with _chapters_cache_lock:
        cached = chapters_cache.get(cache_key)
    if _cache_is_fresh(cached, CHAPTERS_DISK_TTL_SECONDS):
        payload = dict(cached.data)
        payload["cached"] = True
        return payload

    if cached is not None and isinstance(cached.data, dict) and cached.data:
        _schedule_chapters_refresh(cache_key, source, lang)
        payload = dict(cached.data)
        payload["cached"] = True
        payload["stale"] = True
        payload["refreshing"] = True
        return payload

    try:
        payload = _resilient_list_chapters(source, lang)
        with _chapters_cache_lock:
            chapters_cache[cache_key] = CacheEntry(time.time(), dict(payload))
        _save_chapters_snapshot()
        payload["cached"] = False
        return payload
    except Exception:
        if cached is None or not isinstance(cached.data, dict) or not cached.data:
            raise
        logger.warning(
            "Fonte indisponivel source=%s; servindo cache stale",
            _scraper_source_name(source),
        )
        payload = dict(cached.data)
        payload["cached"] = True
        payload["stale"] = True
        return payload


_load_chapters_snapshot()


def _rotate_headers(attempt: int) -> None:
    """Rotaciona User-Agent + headers reais (mutando o DEFAULT_HEADERS que os
    fetchers do reader_server reaproveitam). O self.lock do reader serializa as
    chamadas, entao a mutacao e segura entre tentativas.
    """
    DEFAULT_HEADERS["User-Agent"] = USER_AGENTS[attempt % len(USER_AGENTS)]
    DEFAULT_HEADERS["Accept-Language"] = "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
    DEFAULT_HEADERS["Accept"] = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    )


def _resilient_list_chapters_unbounded(
    source: str,
    lang: str,
    preferred_chapter: str | None = None,
) -> dict:
    """Busca capitulos com RETRY + backoff exponencial + rotacao de UA.

    Tenta CHAPTERS_FETCH_ATTEMPTS vezes (espera 1s, 2s, 4s...). So levanta
    excecao se TODAS falharem — o chamador decide o fallback (cache stale).
    """
    last_exc: Exception | None = None
    attempts = 1 if reader._is_pieceproject_source(source) else CHAPTERS_FETCH_ATTEMPTS
    for attempt in range(attempts):
        try:
            _rotate_headers(attempt)
            return reader.list_chapters(
                source,
                lang=lang,
                preferred_chapter=preferred_chapter,
            )
        except Exception as exc:  # noqa: BLE001 (rede/HTTP/timeout/parse)
            last_exc = exc
            if attempt < attempts - 1:
                wait = CHAPTERS_BACKOFF_START * (CHAPTERS_BACKOFF_BASE ** attempt)
                logger.warning(
                    "list_chapters tentativa=%d/%d source=%s error=%s retry_s=%.1f",
                    attempt + 1,
                    attempts,
                    _scraper_source_name(source),
                    _safe_error(exc),
                    wait,
                )
                time.sleep(wait)
    raise last_exc if last_exc else RuntimeError("Falha desconhecida ao buscar capitulos.")


def _resilient_list_chapters(
    source: str,
    lang: str,
    preferred_chapter: str | None = None,
) -> dict:
    return scraper_coordinator.run(
        _scraper_source_name(source),
        f"chapters:{source}:{lang}:{preferred_chapter or ''}",
        lambda: _resilient_list_chapters_unbounded(source, lang, preferred_chapter),
    )


app = FastAPI(
    title="MangaTemp API",
    version="0.2.0",
    description="API REST local com fontes reais para alimentar o front-end React do MangaTemp.",
)


_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _request_id(request: Request) -> str:
    candidate = request.headers.get("X-Request-ID", "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def _normalized_route(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    return str(path or "<unmatched>")


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    request_id = _request_id(request)
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.error(
            "request id=%s method=%s route=%s status=500 duration_ms=%.1f error=%s",
            request_id,
            request.method,
            _normalized_route(request),
            elapsed_ms,
            _safe_error(exc),
        )
        raise
    response.headers["X-Request-ID"] = request_id
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "request id=%s method=%s route=%s status=%d duration_ms=%.1f",
        request_id,
        request.method,
        _normalized_route(request),
        response.status_code,
        elapsed_ms,
    )
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    allow_credentials=False,
)

# Capas baixadas viram arquivo estatico: GET /static/covers/<manga_id>.<ext>
# Cache-Control agressivo: o navegador guarda a capa "para sempre" e nem
# revalida (immutable). Como o nome do arquivo e estavel por manga_id, isso e
# seguro; se um dia precisar trocar a capa de um id, versione o nome do arquivo.
class CachedStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")


class MangaHomeSchema(BaseModel):
    """Payload da home — campos que o card (MangaCard.jsx) realmente renderiza.

    Mais enxuto que o item completo do catalogo (sem descriptions_map,
    alternative_titles, cover_original_* nem lista de capitulos), mas COMPLETO o
    bastante p/ o card: capa (com cadeia de fallback), sinopse, generos, autores,
    nota e contagem de capitulos. `cover_path` aponta p/ o arquivo LOCAL em
    /static/covers; `cover_url`/`cover_fallbacks` sao a rede de seguranca quando o
    arquivo local ainda nao esta pronto (evita o card preto).
    """

    id: str
    title: str
    cover_path: str = ""
    cover_url: str = ""
    cover_fallbacks: list[str] = Field(default_factory=list)
    source: str = ""
    description: str = ""
    genres: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    rating: float | None = None
    chapter_count: int | None = None
    chapter_preview: list[str] = Field(default_factory=list)
    chapter_status: str = "pending"
    latest_chapter: str = ""
    updated_at: str = ""
    source_url: str = ""
    chapter_languages: list[str] = Field(default_factory=list)


class ProfileCreateRequest(BaseModel):
    display_name: str = Field(default="Leitor", max_length=48)


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=48)


class ProfileFavoritesRequest(BaseModel):
    favorites: list[dict] = Field(default_factory=list, max_length=500)


class ProfileLibraryEntryRequest(BaseModel):
    item: dict
    status: str = Field(default="COMPLETED", max_length=24)
    score: float | None = Field(default=None, ge=0, le=10)
    review: str = Field(default="", max_length=4000)


class ProfileLibraryDeleteRequest(BaseModel):
    item: dict


class ProfileImageRequest(BaseModel):
    """Define/limpa avatar ou background. Aceita `url` (remota http/https) OU
    `data` (data-URI base64). Ambos vazios/None => limpa a imagem atual."""

    url: str | None = Field(default=None, max_length=2048)
    data: str | None = Field(default=None, description="data:image/...;base64,...")


class ChapterCardRefreshRequest(BaseModel):
    """Cards locais (historico) que precisam receber estado atual do cache."""

    items: list[dict] = Field(default_factory=list, max_length=80)


_PROFILE_IMAGE_KIND = {
    "avatar": (PROFILE_AVATAR_MAX_BYTES, PROFILE_AVATAR_MAX_DIM),
    "background": (PROFILE_BACKGROUND_MAX_BYTES, PROFILE_BACKGROUND_MAX_DIM),
    "home_background": (PROFILE_HOME_BACKGROUND_MAX_BYTES, PROFILE_HOME_BACKGROUND_MAX_DIM),
}
_PROFILE_IMAGE_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
}


def _decode_data_uri(data: str) -> bytes:
    """Extrai os bytes de um data-URI base64. Aceita tambem base64 puro."""
    raw = data.strip()
    if raw.startswith("data:"):
        _, _, tail = raw.partition(",")
        raw = tail
    try:
        return base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Imagem base64 invalida.") from exc


def _data_uri_mime(data: str) -> str:
    raw = data.strip()
    if raw.startswith("data:") and "," in raw:
        header = raw[5:raw.index(",")]
        return header.split(";")[0].strip().lower()
    return ""


def _looks_like_video(mime: str, blob: bytes) -> bool:
    """Detecta mp4/webm por mime OU magic bytes (nao passa pelo Pillow)."""
    if mime.startswith("video/"):
        return True
    if len(blob) >= 12 and blob[4:8] == b"ftyp":  # mp4/mov/m4v
        return True
    if blob[:4] == b"\x1a\x45\xdf\xa3":  # webm/mkv (EBML)
        return True
    return False


def _save_profile_video(profile_id: str, kind: str, blob: bytes, mime: str) -> str:
    """Salva um background animado (mp4/webm) bruto, sem reprocessar."""
    if not blob:
        raise HTTPException(status_code=422, detail="Video vazio.")
    if len(blob) > PROFILE_VIDEO_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"Video excede {PROFILE_VIDEO_MAX_BYTES // (1024 * 1024)}MB.")
    is_webm = "webm" in mime or blob[:4] == b"\x1a\x45\xdf\xa3"
    suffix = ".webm" if is_webm else ".mp4"
    directory = PROFILE_MEDIA_DIR / profile_id
    directory.mkdir(parents=True, exist_ok=True)
    _clear_profile_image_files(profile_id, kind)
    target = directory / f"{kind}{suffix}"
    target.write_bytes(blob)
    rel = target.relative_to(STATIC_DIR).as_posix()
    return f"/static/{rel}?v={int(time.time())}"


def _clear_profile_image_files(profile_id: str, kind: str) -> None:
    directory = PROFILE_MEDIA_DIR / profile_id
    if not directory.exists():
        return
    for existing in directory.glob(f"{kind}.*"):
        try:
            existing.unlink()
        except OSError:
            pass


def _save_profile_image(profile_id: str, kind: str, blob: bytes) -> str:
    """Valida (Pillow), redimensiona se preciso e salva localmente.
    Retorna a URL /static/... (com cache-buster) para o frontend."""
    if Image is None:
        raise HTTPException(status_code=503, detail="Pillow indisponivel no servidor.")
    max_bytes, max_dim = _PROFILE_IMAGE_KIND[kind]
    if not blob:
        raise HTTPException(status_code=422, detail="Imagem vazia.")
    if len(blob) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Imagem excede {max_bytes // (1024 * 1024)}MB.")

    try:
        image = Image.open(io.BytesIO(blob))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail="Arquivo nao e uma imagem valida.") from exc

    image_format = (image.format or "").upper()
    is_animated = getattr(image, "is_animated", False)
    directory = PROFILE_MEDIA_DIR / profile_id
    directory.mkdir(parents=True, exist_ok=True)
    _clear_profile_image_files(profile_id, kind)

    # GIF animado: preserva os bytes originais (nao reamostra os frames).
    if image_format == "GIF" and is_animated:
        target = directory / f"{kind}.gif"
        target.write_bytes(blob)
    else:
        if max(image.size) > max_dim:
            image.thumbnail((max_dim, max_dim), Image.LANCZOS)
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            out_format, suffix = "PNG", ".png"
        else:
            image = image.convert("RGB")
            out_format, suffix = "JPEG", ".jpg"
        target = directory / f"{kind}{suffix}"
        if out_format == "JPEG":
            # Background em tela cheia sofre com recompressao: qualidade alta.
            quality = 95 if kind in ("background", "home_background") else 90
            save_kwargs = {"quality": quality, "subsampling": 0, "optimize": True}
        else:
            save_kwargs = {"optimize": True}
        image.save(target, out_format, **save_kwargs)

    rel = target.relative_to(STATIC_DIR).as_posix()
    return f"/static/{rel}?v={int(time.time())}"


def _apply_profile_image(profile: dict, kind: str, request: "ProfileImageRequest") -> str:
    """Resolve o request (url/data/limpar) e devolve o valor final da URL."""
    profile_id = str(profile.get("id") or "")
    field = "avatar_url" if kind == "avatar" else "background_url"
    data = (request.data or "").strip()
    url = (request.url or "").strip()

    if data:
        mime = _data_uri_mime(data)
        blob = _decode_data_uri(data)
        # Background animado (mp4/webm) so p/ backgrounds, nao p/ avatar.
        if kind in ("background", "home_background") and _looks_like_video(mime, blob):
            return _save_profile_video(profile_id, kind, blob, mime)
        return _save_profile_image(profile_id, kind, blob)
    if url:
        if not url.startswith(("http://", "https://", "/static/")):
            raise HTTPException(status_code=422, detail="URL de imagem invalida.")
        _clear_profile_image_files(profile_id, kind)  # troca upload local por remota
        return url
    # Sem url e sem data => limpar imagem.
    _clear_profile_image_files(profile_id, kind)
    return ""


_profiles_lock = threading.RLock()
_users_lock = threading.RLock()
_tokens_lock = threading.RLock()


@dataclass(frozen=True)
class AuthenticatedUser:
    profile_id: str
    username: str
    session_expires_at: float


def _token_from_header(authorization: str) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _resolve_authenticated_user(authorization: str) -> AuthenticatedUser | None:
    token = _token_from_header(authorization)
    if not token:
        return None
    now = time.time()
    with _tokens_lock:
        entry = session_repository.get(token)
        if not isinstance(entry, dict):
            return None
        try:
            expires_at = float(entry.get("expires", 0))
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at <= now:
            session_repository.revoke(token)
            return None

    profile_id = str(entry.get("profile_id") or "")
    if not profile_id:
        return None
    with _users_lock:
        user = user_repository.get_by_profile_id(profile_id)
    if not isinstance(user, dict):
        return None
    return AuthenticatedUser(
        profile_id=profile_id,
        username=str(user.get("username") or entry.get("username") or ""),
        session_expires_at=expires_at,
    )


def require_current_user(
    authorization: str = Header(default=""),
) -> AuthenticatedUser:
    current_user = _resolve_authenticated_user(authorization)
    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Nao autenticado.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


def require_owned_profile(
    current_user: AuthenticatedUser,
    profile_id: str,
) -> None:
    if not secrets.compare_digest(current_user.profile_id, str(profile_id or "")):
        raise HTTPException(status_code=403, detail="Acesso negado a este perfil.")


def require_profile_owner(
    profile_id: str,
    authorization: str = Header(default=""),
) -> AuthenticatedUser:
    current_user = _resolve_authenticated_user(authorization)
    if current_user is not None:
        require_owned_profile(current_user, profile_id)
        return current_user
    if not settings.is_web and not _token_from_header(authorization):
        return AuthenticatedUser(
            profile_id=profile_id,
            username="desktop-local",
            session_expires_at=0,
        )
    raise HTTPException(
        status_code=401,
        detail="Nao autenticado.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_token(authorization: str) -> str | None:
    """Retorna o profile_id de uma sessao Bearer valida, quando existir."""
    current_user = _resolve_authenticated_user(authorization)
    return current_user.profile_id if current_user else None


def _revoke_token(authorization: str) -> None:
    token = _token_from_header(authorization)
    if not token:
        return
    with _tokens_lock:
        session_repository.revoke(token)


def _profile_favorite(item: dict) -> dict | None:
    source_url = str(item.get("source_url") or "").strip()
    title = str(item.get("title") or "").strip()
    identifier = str(item.get("id") or source_url or title).strip()
    if not identifier or not title:
        return None
    return {
        "id": identifier,
        "title": title,
        "source_url": source_url,
        "source": str(item.get("source") or "").strip(),
        "cover_path": str(item.get("cover_path") or "").strip(),
        "cover_url": str(item.get("cover_url") or "").strip(),
        "cover_fallbacks": [
            str(url).strip()
            for url in (item.get("cover_fallbacks") or [])[:5]
            if str(url or "").strip()
        ],
        "genres": [str(genre).strip() for genre in (item.get("genres") or [])[:8] if str(genre or "").strip()],
        "authors": [str(author).strip() for author in (item.get("authors") or [])[:8] if str(author or "").strip()],
        "chapter_count": item.get("chapter_count"),
        "chapter_preview": [str(chapter).strip() for chapter in (item.get("chapter_preview") or [])[:3] if str(chapter or "").strip()],
        "chapter_status": str(item.get("chapter_status") or "pending"),
        "latest_chapter": str(item.get("latest_chapter") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "chapter_languages": [str(language).strip() for language in (item.get("chapter_languages") or [])[:8] if str(language or "").strip()],
    }


def _profile_library_item(item: dict, status: str = "COMPLETED", score: object = None, review: object = "", external: dict | None = None) -> dict | None:
    saved = _profile_favorite(item)
    if not saved:
        return None
    normalized_status = str(status or "COMPLETED").upper()
    if normalized_status not in {"COMPLETED", "CURRENT", "PLANNING", "PAUSED", "REPEATING", "DROPPED"}:
        normalized_status = "COMPLETED"
    saved.update({
        "status": normalized_status,
        "score": _number_value(score),
        "review": str(review or "").strip()[:4000],
        "progress": (external or {}).get("progress"),
        "external_id": str((external or {}).get("id") or ""),
        "external_provider": str((external or {}).get("provider") or ""),
        "updated_at": float((external or {}).get("updated_at") or time.time()),
    })
    return saved


def _profile_links_payload(profile: dict) -> dict:
    """Contas externas vinculadas, SEM expor tokens de acesso."""
    links: dict[str, dict] = {}
    raw = profile.get("links") or {}
    if isinstance(raw, dict):
        for provider, info in raw.items():
            if not isinstance(info, dict):
                continue
            links[str(provider)] = {
                "id": info.get("id"),
                "name": str(info.get("name") or ""),
                "avatar": str(info.get("avatar") or ""),
                "url": str(info.get("url") or ""),
                "linked_at": float(info.get("linked_at") or 0),
                "synced_at": float(info.get("synced_at") or 0),
                "list_count": int(info.get("list_count") or 0),
                "matched_count": int(info.get("matched_count") or 0),
            }
    return links


def _profile_item_with_cached_chapters(raw_item: object) -> object:
    if not isinstance(raw_item, dict):
        return raw_item
    item = dict(raw_item)
    source_url = str(item.get("source_url") or "").strip()
    if not source_url:
        return item
    language = _item_chapter_language(item)
    payload = _cached_chapters_payload(source_url, language)
    if payload is not None:
        _apply_verified_chapters(item, payload)
        item["chapter_status"] = "ready" if int(item.get("chapter_count") or 0) > 0 else "unavailable"
        return item
    if item.get("chapter_preview") or int(item.get("chapter_count") or 0) > 0:
        item["chapter_status"] = "ready"
    else:
        failure = chapter_audit_failures.get(source_url)
        if _cache_is_fresh(failure, CHAPTER_AUDIT_FAILURE_TTL_SECONDS):
            item["chapter_status"] = "unavailable"
    return item


def _profile_payload(profile: dict) -> dict:
    return {
        "id": str(profile.get("id") or ""),
        "display_name": str(profile.get("display_name") or "Leitor"),
        "avatar_url": str(profile.get("avatar_url") or ""),
        "background_url": str(profile.get("background_url") or ""),
        "home_background_url": str(profile.get("home_background_url") or ""),
        "links": _profile_links_payload(profile),
        "favorites": [_profile_item_with_cached_chapters(item) for item in (profile.get("favorites") or [])],
        "library": [_profile_item_with_cached_chapters(item) for item in (profile.get("library") or [])],
        "created_at": float(profile.get("created_at") or 0),
        "updated_at": float(profile.get("updated_at") or 0),
    }


def _profile_or_404(profile_id: str) -> dict:
    profile = profile_repository.get(profile_id)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=404, detail="Perfil nao encontrado.")
    return profile


def _home_has_real_cover(item: dict) -> bool:
    """Capa disponivel: arquivo local OU URL remota (proxy serve via /api/image).
    Aceita qualquer fonte de imagem valida — nao bloqueia a home enquanto o
    background thread baixa capas pro disco.
    """
    cover_path = str(item.get("cover_path") or "")
    if cover_path and cover_path != PLACEHOLDER_URL and _cover_file_exists(cover_path):
        return True
    # Aceita capa remota (o frontend usa cover_url/fallbacks via proxy)
    cover_url = str(item.get("cover_url") or item.get("cover_original_url") or "")
    return bool(cover_url.strip())


def _home_has_chapters(item: dict) -> bool:
    """Tem capitulos associados (evita 'null caps' poluindo a home)."""
    source_url = str(item.get("source_url") or "").strip()
    language = _item_chapter_language(item)
    cached = _cached_chapters_payload(source_url, language) if source_url else None
    if cached is not None:
        return int(cached.get("count") or len(cached.get("chapters") or [])) > 0

    latest = str(item.get("latest_chapter") or "").strip()
    latest_normalized = normalize_match_text(latest)
    if latest_normalized in {
        "n a",
        "na",
        "none",
        "null",
        "unknown",
        "not available",
        "not avaliable",
        "indisponivel",
        "nao disponivel",
    }:
        return False

    if _guess_provider(item) == "mangadex":
        languages = {
            str(language or "").strip().lower()
            for language in (item.get("chapter_languages") or [])
            if str(language or "").strip()
        }
        if not languages:
            return False

    count = item.get("chapter_count") if item.get("chapter_count_verified") else None
    try:
        if count not in (None, "") and int(count) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(latest)


def _is_home_ready(item: dict) -> bool:
    """Obra pronta p/ a linha de frente: tem capa real E capitulos."""
    return _home_has_real_cover(item) and _home_has_chapters(item)


def _preferred_home_source(item: dict) -> dict:
    if str(item.get("provider") or "").lower() in {"hq_local", "hq_now", "fliptru", "light_novel_local", "novel_mania", "central_novel", "tensura_fan", "pleiades_translations"}:
        return item
    title = str(item.get("title") or "")
    title_identity = normalize_match_text(title)
    curated_raw = next(
        (
            raw
            for raw in _iter_curated()
            if title_identity
            and title_identity in {
                normalize_match_text(str(name or ""))
                for name in [raw.get("title"), *(raw.get("aliases") or [])]
                if str(name or "").strip()
            }
        ),
        None,
    )
    if not curated_raw:
        return item
    curated = _normalize_manga_item(
        {**curated_raw, "title": title},
        section=str(item.get("section") or curated_raw.get("section") or ""),
    )
    if not curated:
        return item
    preferred_url = str(curated.get("source_url") or "")
    current_url = str(item.get("source_url") or "")
    if not preferred_url or preferred_url == current_url:
        return item
    merged = dict(item)
    for key in ("source_url", "provider", "source", "language", "chapter_languages"):
        merged[key] = curated.get(key)
    return merged


def _item_chapter_language(item: dict) -> str:
    """Idioma no qual os capitulos desta obra REALMENTE existem.

    O campo `language` frequentemente e o default "pt-br", mas a obra pode so ter
    capitulos em outro idioma (ex.: MangaDex so em EN). Auditar/consultar no
    idioma errado devolve zero capitulos e trava o card em "Verificando".
    Prioriza: language (se disponivel), depois pt-br/pt, depois o 1o disponivel.
    """
    language = str(item.get("language") or "").strip().lower()
    langs = [
        str(entry).strip().lower()
        for entry in (item.get("chapter_languages") or [])
        if str(entry or "").strip()
    ]
    if language and (not langs or language in langs):
        return language
    for preferred in ("pt-br", "pt"):
        if preferred in langs:
            return preferred
    if langs:
        return langs[0]
    return language or "pt-br"


def _home_item(item: dict) -> dict:
    """Mapeia um item completo do catalogo -> dict da home p/ o card.

    Mantem a capa local (cover_path) + cadeia de fallback (cover_url/fallbacks) e
    os metadados que o card exibe (sinopse, generos, autores, nota, n de caps).
    """
    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    item = _preferred_home_source(item)
    source_url = str(item.get("source_url") or item.get("url") or "")
    language = _item_chapter_language(item)
    cached_chapters = _cached_chapters_payload(source_url, language) if source_url else None
    if cached_chapters:
        chapter_count = int(cached_chapters.get("count") or len(cached_chapters.get("chapters") or []))
        chapter_preview = _chapter_preview_from_payload(cached_chapters)
        # Cache vazio (0 caps neste idioma): audit ja rodou e nao achou nada.
        # Marca como indisponivel em vez de deixar o card eternamente "Verificando".
        chapter_status = "ready" if chapter_count > 0 else "unavailable"
    else:
        # Catalogo externo frequentemente reporta ultimo numero publicado, nao
        # quantidade disponivel nesta fonte/idioma. Nunca exibir isso como caps.
        chapter_count = None
        chapter_preview = []
        failure = chapter_audit_failures.get(source_url)
        chapter_status = (
            "unavailable"
            if _cache_is_fresh(failure, CHAPTER_AUDIT_FAILURE_TTL_SECONDS)
            else "pending"
        )

    return MangaHomeSchema(
        id=str(item.get("id") or item.get("slug") or item.get("source_url") or ""),
        title=str(item.get("title") or ""),
        cover_path=str(item.get("cover_path") or item.get("cover_url") or PLACEHOLDER_URL),
        cover_url=str(item.get("cover_url") or ""),
        cover_fallbacks=[str(u) for u in (item.get("cover_fallbacks") or []) if str(u or "").strip()],
        source=str(item.get("source") or ""),
        description=_clean_synopsis(item.get("description")),
        genres=[str(g) for g in (item.get("genres") or []) if str(g or "").strip()],
        authors=[str(a) for a in (item.get("authors") or []) if str(a or "").strip()],
        rating=_to_float(item.get("rating")),
        chapter_count=chapter_count,
        chapter_preview=chapter_preview,
        chapter_status=chapter_status,
        latest_chapter=str(item.get("latest_chapter") or ""),
        updated_at=str(item.get("updated_at") or ""),
        source_url=source_url,
        chapter_languages=[
            str(language).lower()
            for language in (item.get("chapter_languages") or [])
            if str(language or "").strip()
        ],
    ).model_dump()


def _cache_is_fresh(entry: CacheEntry | None, ttl: int) -> bool:
    return bool(entry and time.time() - entry.saved_at < ttl)


def _chapters_cache_key(source: str, lang: str) -> str:
    source_value = source.strip()
    source_lower = source_value.lower()
    if "novelmania.com.br" in source_lower:
        version = f"{CHAPTERS_CACHE_VERSION}-novelmania2"
    elif "mangasbrasuka.com.br" in source_lower:
        version = f"{CHAPTERS_CACHE_VERSION}-mangasbrasuka2"
    elif "centralnovel.com" in source_lower:
        version = f"{CHAPTERS_CACHE_VERSION}-centralnovel1"
    elif "tensurafan.github.io" in source_lower:
        version = f"{CHAPTERS_CACHE_VERSION}-tensurafan4"
    elif "pleiadestranslations.wordpress.com" in source_lower:
        version = f"{CHAPTERS_CACHE_VERSION}-pleiades2"
    else:
        version = str(CHAPTERS_CACHE_VERSION)
    return f"v{version}|{source_value}|{normalize_match_text(lang)}"


def _cached_chapters_payload(source: str, lang: str) -> dict | None:
    with _chapters_cache_lock:
        entry = chapters_cache.get(_chapters_cache_key(source, lang))
    if entry is None or not isinstance(entry.data, dict) or not entry.data:
        return None
    return entry.data


def _cached_chapter_count(source: str, lang: str = "pt-br") -> int:
    payload = _cached_chapters_payload(source, lang)
    if payload is not None:
        return int(payload.get("count") or len(payload.get("chapters") or []))
    return 0


def _chapter_preview_from_payload(payload: dict, limit: int = 3) -> list[str]:
    preview: list[str] = []
    chapters = payload.get("chapters") or []
    for chapter in chapters:
        number = str(
            chapter.get("number_text")
            or chapter.get("number")
            or chapter.get("label")
            or ""
        ).strip()
        # Oneshot / capitulo sem numeracao (comum no MangaDex): nao tem numero,
        # entao mostra o titulo do capitulo (e "Unico" so se nem titulo houver).
        # Sem isso o preview ficava vazio e o card travava em "Verificando".
        if not number:
            title = str(chapter.get("title") or "").strip()
            number = title or "Unico"
        if number and number not in preview:
            preview.append(number)
        if len(preview) >= limit:
            break
    return preview


def _apply_verified_chapters(item: dict, payload: dict) -> None:
    item["chapter_count"] = int(payload.get("count") or len(payload.get("chapters") or []))
    item["chapter_preview"] = _chapter_preview_from_payload(payload)
    item["chapter_count_verified"] = True


def _slug(value: str) -> str:
    normalized = normalize_match_text(value)
    return "-".join(part for part in normalized.split() if part)


def _is_one_piece_title(value: str) -> bool:
    normalized = normalize_match_text(str(value or ""))
    return normalized == "one piece" or normalized.startswith("one piece ")


def _source_label(provider: str | None) -> str:
    provider = str(provider or "").lower()
    return SOURCE_LABELS.get(provider, provider.title() if provider else "Fonte")


def _is_remote_image_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_app_image_url(url: str) -> bool:
    value = str(url or "")
    return value.startswith("/api/hq/assets/") or value.startswith("/api/light-novels/assets/")


def _can_load_image_directly(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host in DIRECT_IMAGE_HOSTS or _is_mangadex_image_url(url)


def _proxy_image_url(url: str) -> str:
    url = str(url or "").strip()
    if not _is_remote_image_url(url):
        return ""
    return f"/api/image?url={quote(url, safe='')}"


def _unproxy_image_url(url: str) -> str:
    url = str(url or "").strip()
    if not url.startswith("/api/image?"):
        return url
    values = parse_qs(urlparse(url).query).get("url") or []
    return unquote(values[0]).strip() if values else ""


def _is_mangadex_image_url(url: str) -> bool:
    host = urlparse(str(url or "")).netloc.lower()
    return (
        host == "uploads.mangadex.org"
        or host.endswith(".mangadex.org")
        or host.endswith(".mangadex.network")
    )


def _cover_urls(primary: str, fallbacks: list[str]) -> tuple[str, list[str]]:
    originals = []
    for url in [primary, *fallbacks]:
        url = _unproxy_image_url(url)
        url = str(url or "").strip()
        if (_is_remote_image_url(url) or _is_app_image_url(url)) and url not in originals:
            originals.append(url)
    if not originals:
        return "", []

    # Proxy SEMPRE o primary: backend injeta Referer correto -> evita 403 de hotlink
    # (mugiverso/mangasbrasuka/mangalivre bloqueiam carga direta sem referer).
    primary_proxy = _proxy_image_url(originals[0]) or originals[0]
    fallback_urls: list[str] = []
    for url in originals[1:]:
        proxy = _proxy_image_url(url)
        if proxy and proxy not in fallback_urls:
            fallback_urls.append(proxy)
    # ultima cartada: urls cruas (caso o proxy caia)
    for url in originals:
        if url not in fallback_urls:
            fallback_urls.append(url)
    return primary_proxy, fallback_urls


def _refresh_cover_fields(item: dict) -> dict:
    merged = dict(item)
    originals: list[str] = []
    for url in [
        merged.get("cover_original_url"),
        merged.get("cover_url"),
        *(merged.get("cover_original_fallbacks") or []),
        *(merged.get("cover_fallbacks") or []),
    ]:
        clean_url = _unproxy_image_url(str(url or "").strip())
        if (_is_remote_image_url(clean_url) or _is_app_image_url(clean_url)) and clean_url not in originals:
            originals.append(clean_url)
    if not originals:
        return merged
    cover_url, cover_fallbacks = _cover_urls(originals[0], originals[1:])
    merged["cover_url"] = cover_url
    merged["cover_original_url"] = originals[0]
    merged["cover_fallbacks"] = cover_fallbacks
    merged["cover_original_fallbacks"] = originals[1:]
    return merged


def _item_has_cover(item: dict) -> bool:
    return bool(
        str(item.get("cover_path") or "").strip()
        or str(item.get("cover_url") or "").strip()
        or str(item.get("cover_original_url") or "").strip()
    )


def _copy_cover_fields(target: dict, source: dict) -> None:
    for field in (
        "cover_path",
        "cover_url",
        "cover_original_url",
        "cover_fallbacks",
        "cover_original_fallbacks",
    ):
        if target.get(field):
            continue
        value = source.get(field)
        if isinstance(value, list):
            value = list(value)
        if value:
            target[field] = value
    if target.get("cover_original_url") and not target.get("cover_url"):
        target.update(_refresh_cover_fields(target))


def _guess_provider(item: dict) -> str:
    provider = str(item.get("provider") or item.get("source") or "").lower()
    url = str(item.get("url") or "")
    if provider:
        return provider
    if url.startswith("hq-local://"):
        return "hq_local"
    if url.startswith("hq-now://"):
        return "hq_now"
    if "fliptru.com.br" in url:
        return "fliptru"
    if url.startswith("light-novel://"):
        return "light_novel_local"
    if "novelmania.com.br" in url:
        return "novel_mania"
    if "centralnovel.com" in url:
        return "central_novel"
    if "tensurafan.github.io" in url:
        return "tensura_fan"
    if "pleiadestranslations.wordpress.com" in url:
        return "pleiades_translations"
    if "nexusmangas" in url or url.startswith("nexus://"):
        return "nexus"
    if "geekstations.com.br" in url or url.startswith("mangageek://"):
        return "mangageek"
    if "yomumangas" in url or "yumomangas" in url or url.startswith("yumo://"):
        return "yumo"
    if "mangakatana" in url or url.startswith("mangakatana://"):
        return "mangakatana"
    if "mangasbrasuka" in url:
        return "mangasbrasuka"
    if "mangalivre" in url:
        return "mangalivre"
    if "toomics" in url:
        return "toomics"
    if "sakuramangas" in url or url.startswith("sakura://"):
        return "sakura"
    if "mangadex" in url:
        return "mangadex"
    if url.startswith("pieceproject://") or "pieceproject.xyz" in url:
        return "pieceproject"
    return "mangadex"


def _normalize_manga_item(item: dict, *, section: str = "") -> dict | None:
    title = str(item.get("title") or "").strip()
    source_url = str(item.get("url") or item.get("source_url") or "").strip()
    if not title or not source_url:
        return None

    provider = _guess_provider(item)
    if not _provider_enabled(provider):
        return None
    poster_original = str(item.get("poster") or item.get("cover_url") or "").strip()
    fallback_originals = [
        str(url).strip()
        for url in (item.get("poster_fallbacks") or [])
        if str(url or "").strip()
    ]
    cover_url, cover_fallbacks = _cover_urls(poster_original, fallback_originals)
    genres = [
        str(genre).strip()
        for genre in (item.get("genres") or [])
        if str(genre or "").strip()
    ]
    content_rating = str(item.get("content_rating") or item.get("contentRating") or "").lower()
    if content_rating in {"erotica", "pornographic"}:
        return None

    return {
        "id": str(item.get("id") or _slug(title) or source_url),
        "title": title,
        "slug": _slug(title),
        "source_url": source_url,
        "provider": provider,
        "source": _source_label(provider),
        "section": section or str(item.get("section") or ""),
        "cover_url": cover_url,
        "cover_original_url": poster_original,
        "cover_fallbacks": cover_fallbacks,
        "cover_original_fallbacks": fallback_originals,
        "genres": genres[:8],
        "description": item.get("description") or "",
        "descriptions_map": item.get("descriptions") or {},
        "latest_chapter": str(item.get("latest_chapter") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "chapter_languages": [
            str(l).lower()
            for l in (
                item.get("available_translated_languages")
                or item.get("chapter_languages")
                or []
            )
            if l
        ],
        "authors": item.get("authors") or [],
        # Valor de listagem externa pode ser total global ou ultimo numero de
        # capitulo. So usamos contagem verificada pela lista desta mesma fonte.
        "chapter_count": item.get("chapter_count") if provider in {"hq_local", "hq_now", "light_novel_local"} else None,
        "chapter_count_verified": provider in {"hq_local", "hq_now", "light_novel_local"},
        "chapter_preview": item.get("chapter_preview") or [],
        "reported_chapter_count": item.get("chapter_count"),
        "rating": item.get("rating"),
        "status": item.get("status") or "",
        "language": item.get("language") or "pt-br",
        "alternative_titles": item.get("alternative_titles") or [],
    }


def _provider_preference_score(item: dict) -> tuple[float, float, float]:
    try:
        chapter_count = float(item.get("chapter_count") or 0)
    except (TypeError, ValueError):
        chapter_count = 0.0
    reliability = SOURCE_RELIABILITY.get(str(item.get("provider") or "").lower(), 0.5)
    relevance = float(item.get("relevance") or 0)
    return chapter_count, reliability, relevance


def _merge_duplicate(existing: dict, candidate: dict) -> dict:
    preferred = (
        candidate
        if _provider_preference_score(candidate) > _provider_preference_score(existing)
        else existing
    )
    secondary = existing if preferred is candidate else candidate
    merged = dict(preferred)
    for key in ("cover_url", "cover_original_url", "description", "authors", "chapter_count", "rating", "status"):
        if not merged.get(key) and secondary.get(key):
            merged[key] = secondary[key]
        elif key == "chapter_count":
            existing_count = int(merged.get("chapter_count") or 0)
            secondary_count = int(secondary.get("chapter_count") or 0)
            if secondary_count > existing_count:
                merged["chapter_count"] = secondary_count
    merged["cover_fallbacks"] = list(
        dict.fromkeys([
            *(merged.get("cover_fallbacks") or []),
            *(secondary.get("cover_fallbacks") or []),
        ])
    )
    merged["genres"] = list(
        dict.fromkeys([
            *(merged.get("genres") or []),
            *(secondary.get("genres") or []),
        ])
    )[:8]
    return merged


def _canonical_title_identity(title: str) -> str:
    identity = normalize_match_text(str(title or ""))
    if not identity:
        return ""
    for raw in _iter_curated():
        names = [raw.get("title"), *(raw.get("aliases") or [])]
        if identity in {
            normalize_match_text(str(name or ""))
            for name in names
            if str(name or "").strip()
        }:
            return normalize_match_text(str(raw.get("title") or title))
    return identity


def _dedupe(items: list[dict]) -> list[dict]:
    by_identity: dict[str, int] = {}
    result: list[dict] = []
    for item in items:
        title_key = _canonical_title_identity(str(item.get("title") or ""))
        url_key = str(item.get("source_url") or item.get("id") or "")
        identity = title_key or url_key
        if not identity:
            continue
        if identity in by_identity:
            index = by_identity[identity]
            result[index] = _merge_duplicate(result[index], item)
            continue
        by_identity[identity] = len(result)
        if url_key:
            by_identity.setdefault(url_key, len(result))
        result.append(item)
    return result


def _dedupe_search_results(items: list[dict]) -> list[dict]:
    """Busca preserva homonimos: dedupe por fonte/URL, nao so por titulo."""
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        provider = str(item.get("provider") or "").lower()
        source_url = str(item.get("source_url") or item.get("url") or "").strip()
        title = normalize_match_text(str(item.get("title") or ""))
        identity = source_url or str(item.get("id") or "").strip() or title
        key = f"{provider}:{identity}"
        if not identity or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _search_title_candidates(item: dict) -> list[str]:
    alternatives = item.get("alternative_titles") or []
    if not isinstance(alternatives, list):
        alternatives = [str(alternatives)]
    candidates = [str(item.get("title") or ""), *[str(title) for title in alternatives]]
    return [normalize_match_text(title) for title in candidates if normalize_match_text(title)]


def _search_query_tokens(query: str) -> list[str]:
    return [token for token in normalize_match_text(query).split() if len(token) >= 2]


def _fuzzy_search_threshold(query: str) -> float:
    compact = normalize_match_text(query).replace(" ", "")
    if len(compact) <= 3:
        return 0.9
    if len(compact) <= 5:
        return 0.8
    return 0.72


def _best_catalog_fuzzy_match(query: str) -> tuple[dict | None, float]:
    data = catalog_cache.data if catalog_cache else (_read_catalog_snapshot() or {})
    pools = [data.get("items") or []]
    pools.extend(section.get("items") or [] for section in (data.get("sections") or []))
    best_item: dict | None = None
    best_score = 0.0
    seen: set[str] = set()
    for pool in pools:
        for raw in pool:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("source_url") or raw.get("id") or raw.get("title") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            titles = [str(raw.get("title") or ""), *[str(title) for title in (raw.get("alternative_titles") or [])]]
            score = max((fuzzy_match_score(query, title) for title in titles if title), default=0.0)
            if score > best_score:
                best_item = dict(raw)
                best_score = score
    return best_item, best_score


def _fuzzy_search_fallback_term(query: str) -> tuple[str, dict | None]:
    candidate, score = _best_catalog_fuzzy_match(query)
    if candidate and score >= _fuzzy_search_threshold(query):
        return str(candidate.get("title") or "").strip(), candidate
    tokens = [token for token in normalize_match_text(query).split() if len(token) >= 4]
    if not tokens:
        return "", None
    token = max(tokens, key=len)
    return token[: min(5, len(token))], None


def _title_contains_query_tokens(query: str, item: dict) -> bool:
    query_norm = normalize_match_text(query)
    if not query_norm:
        return False
    compact_query = query_norm.replace(" ", "")
    query_tokens = _search_query_tokens(query)
    for candidate in _search_title_candidates(item):
        if candidate == query_norm or query_norm in candidate:
            return True
        if compact_query and compact_query in candidate.replace(" ", ""):
            return True
        candidate_tokens = set(candidate.split())
        if query_tokens and all(token in candidate_tokens for token in query_tokens):
            return True
        if fuzzy_match_score(query, candidate) >= _fuzzy_search_threshold(query):
            return True
    return False


def _search_rank_tier(query: str, item: dict) -> int:
    query_norm = normalize_match_text(query)
    if not query_norm:
        return 9
    candidates = _search_title_candidates(item)
    if any(candidate == query_norm for candidate in candidates):
        return 0
    if any(candidate.startswith(f"{query_norm} ") or candidate.startswith(query_norm) for candidate in candidates):
        return 1
    query_tokens = [token for token in query_norm.split() if len(token) >= 2]
    if query_tokens:
        for candidate in candidates:
            candidate_tokens = set(candidate.split())
            if all(token in candidate_tokens or token in candidate for token in query_tokens):
                return 2
    if any(query_norm in candidate for candidate in candidates):
        return 3
    return 4


def _build_sections_from_items(items: list[dict], per_section: int = 18) -> list[dict]:
    """Fallback: group items by their 'section' field when catalog sections are empty."""
    grouped: dict[str, list[dict]] = {}
    for item in items:
        sec = str(item.get("section") or "Catálogo").strip() or "Catálogo"
        grouped.setdefault(sec, []).append(item)
    sections = []
    for title, sec_items in grouped.items():
        if sec_items:
            sections.append({"title": title, "items": sec_items[:per_section]})
    return sections


def _chapter_count_for_source(source_url: str, lang: str = "pt-br") -> int:
    cache_key = f"{source_url.strip()}|{normalize_match_text(lang)}"
    cached = chapter_count_cache.get(cache_key)
    if _cache_is_fresh(cached, CHAPTER_COUNT_CACHE_TTL_SECONDS):
        return int(cached.data.get("count") or 0)

    disk_count = _cached_chapter_count(source_url, lang)
    if disk_count > 0:
        chapter_count_cache[cache_key] = CacheEntry(time.time(), {"count": disk_count})
        return disk_count
    try:
        payload = _resilient_list_chapters(source_url, lang)
        count = int(payload.get("count") or 0)
        with _chapters_cache_lock:
            chapters_cache[_chapters_cache_key(source_url, lang)] = CacheEntry(time.time(), dict(payload))
    except Exception:
        count = 0
    chapter_count_cache[cache_key] = CacheEntry(time.time(), {"count": count})
    return count


def _curated_match_score(query: str, raw: dict) -> float:
    query_norm = normalize_match_text(query)
    if not query_norm:
        return 0.0
    candidates = [
        str(raw.get("title") or ""),
        *[str(alias) for alias in raw.get("aliases") or []],
    ]
    best = 0.0
    for candidate in candidates:
        candidate_norm = normalize_match_text(candidate)
        if not candidate_norm:
            continue
        if query_norm == candidate_norm:
            return 1.0
        if len(query_norm) >= 10 and (query_norm in candidate_norm or candidate_norm in query_norm):
            best = max(best, 0.96)
        best = max(best, fuzzy_match_score(query, candidate))
    return best


def _curated_override_for_title(title: str) -> dict | None:
    matches = [
        (_curated_match_score(title, raw), raw)
        for raw in _iter_curated()
    ]
    matches.sort(key=lambda item: item[0], reverse=True)
    if not matches or matches[0][0] < 0.78:
        return None
    return matches[0][1]


def _enrich_curated_item(raw: dict) -> dict | None:
    payload = dict(raw)
    if not payload.get("url") and payload.get("query"):
        provider = str(payload.get("provider") or "")
        query = str(payload.get("query") or "")
        search_payload = _search_source(provider, query, limit=4) if provider else []
        if search_payload:
            payload.update(
                {
                    "url": search_payload[0].get("source_url"),
                    "poster": search_payload[0].get("cover_original_url"),
                    "description": search_payload[0].get("description"),
                    "authors": search_payload[0].get("authors"),
                }
            )
    item = _normalize_manga_item(payload, section=str(payload.get("section") or "Catálogo"))
    if not item:
        return None
    source_url = str(item.get("source_url") or "")
    if source_url:
        try:
            metadata = reader.manga_metadata(source_url, include_chapters=False)
            manga = metadata.get("manga") or {}
            item["chapter_count"] = metadata.get("chapter_count") or item.get("chapter_count")
            if manga.get("description") and not item.get("description"):
                item["description"] = manga["description"]
            if manga.get("authors") and not item.get("authors"):
                item["authors"] = manga["authors"]
            if manga.get("genres"):
                item["genres"] = list(dict.fromkeys([*(item.get("genres") or []), *manga["genres"]]))[:8]
            poster = str(manga.get("poster") or "").strip()
            if poster and not item.get("cover_url"):
                item["cover_original_url"] = poster
                item["cover_original_fallbacks"] = [
                    *(item.get("cover_original_fallbacks") or []),
                ]
                item.update(_refresh_cover_fields(item))
            if manga.get("rating", {}).get("score") and not item.get("rating"):
                item["rating"] = float(manga["rating"]["score"])
            if manga.get("status") and not item.get("status"):
                item["status"] = manga["status"]
            alt_titles = manga.get("alternative_titles") or []
            if isinstance(alt_titles, list):
                item["alternative_titles"] = alt_titles
        except Exception:
            count = _chapter_count_for_source(source_url)
            if count:
                item["chapter_count"] = count
    return item


_custom_catalog_cache: tuple[float, list[dict]] | None = None


def _load_custom_catalog() -> list[dict]:
    """Obras adicionadas manualmente (custom_catalog.json). Recarrega sozinho
    quando o arquivo muda (mtime), sem precisar reiniciar o servidor."""
    global _custom_catalog_cache
    try:
        mtime = CUSTOM_CATALOG_PATH.stat().st_mtime
    except OSError:
        _custom_catalog_cache = None
        return []
    if _custom_catalog_cache and _custom_catalog_cache[0] == mtime:
        return _custom_catalog_cache[1]
    try:
        data = json.loads(CUSTOM_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - json invalido nao pode derrubar a home
        return []
    entries = [dict(item) for item in data if isinstance(item, dict) and item.get("url")]
    _custom_catalog_cache = (mtime, entries)
    return entries


def _iter_curated() -> list[dict]:
    """CURATED_CATALOG fixo + obras adicionadas manualmente (sem duplicar URL)."""
    seen = {str(raw.get("url") or "").strip() for raw in CURATED_CATALOG}
    extra = [raw for raw in _load_custom_catalog() if str(raw.get("url") or "").strip() not in seen]
    return CURATED_CATALOG + extra


def _curated_catalog_items() -> list[dict]:
    items: list[dict] = []
    for raw in _iter_curated():
        item = _enrich_curated_item(raw)
        if item:
            items.append(item)
    return _dedupe(items)


def _fast_curated_catalog_items() -> list[dict]:
    items: list[dict] = []
    for raw in _iter_curated():
        payload = dict(raw)
        if not payload.get("url"):
            continue
        item = _normalize_manga_item(payload, section=str(payload.get("section") or "Catálogo"))
        if item:
            items.append(item)
    return _dedupe(items)


def _snapshot_payload(data: dict, limit: int | None = None) -> dict:
    payload = _apply_fast_curated_fields(dict(data))
    items = [
        item
        for item in (payload.get("items") or [])
        if _guess_provider(item) != "yumo" and _provider_enabled(_guess_provider(item))
    ]
    if limit is not None:
        payload["items"] = items[:limit]
        payload["limit"] = limit
    elif str(item.get("provider") or "").lower() in {"hq_local", "hq_now", "light_novel_local"}:
        chapter_count = _to_int(item.get("chapter_count")) or 0
        chapter_preview = [
            str(number) for number in (item.get("chapter_preview") or []) if str(number).strip()
        ][:3]
        chapter_status = "ready" if chapter_count > 0 else "unavailable"
    else:
        payload["items"] = items
    payload["sections"] = [
        {**section, "items": section_items}
        for section in (payload.get("sections") or [])
        if (section_items := [
            item
            for item in (section.get("items") or [])
            if _guess_provider(item) != "yumo" and _provider_enabled(_guess_provider(item))
        ])
    ]
    payload["sources"] = [
        label
        for label in ACTIVE_CATALOG_SOURCES
        if _provider_enabled(label.casefold().replace(" ", "_"))
    ]
    payload["total"] = len(items)
    return payload


def _apply_fast_curated_fields(data: dict) -> dict:
    seeds = {
        normalize_match_text(str(item.get("title") or "")): item
        for item in _fast_curated_catalog_items()
    }
    if not seeds:
        return data

    def merge(item: dict) -> dict:
        key = normalize_match_text(str(item.get("title") or ""))
        seed = seeds.get(key)
        if not seed:
            return _refresh_cover_fields(item)
        merged = dict(item)
        if _is_one_piece_title(str(item.get("title") or "")) and seed.get("provider") == "pieceproject":
            merged["source_url"] = seed["source_url"]
            merged["provider"] = "pieceproject"
            merged["source"] = _source_label("pieceproject")
        for field in ("cover_url", "cover_original_url", "cover_fallbacks", "cover_original_fallbacks"):
            if not merged.get(field) and seed.get(field):
                merged[field] = seed[field]
        return _refresh_cover_fields(merged)

    data["items"] = [merge(dict(item)) for item in data.get("items") or []]
    data["sections"] = [
        {**section, "items": [merge(dict(item)) for item in section.get("items") or []]}
        for section in data.get("sections") or []
    ]
    return data


def _read_catalog_snapshot() -> dict | None:
    try:
        if not CATALOG_SNAPSHOT_PATH.exists():
            return None
        payload = json.loads(CATALOG_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload.get("items"):
            return None
        return payload
    except Exception:
        return None


def _write_catalog_snapshot(data: dict) -> None:
    try:
        CATALOG_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CATALOG_SNAPSHOT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CATALOG_SNAPSHOT_PATH)
    except Exception:
        return


def _catalog_snapshot_age() -> float | None:
    try:
        return time.time() - CATALOG_SNAPSHOT_PATH.stat().st_mtime
    except OSError:
        return None


def _fast_catalog_seed(limit: int) -> dict:
    items = _fast_curated_catalog_items()
    sections = _build_sections_from_items(items, per_section=12) if items else []
    data = {
        "items": items[:limit],
        "sections": sections,
        "total": len(items),
        "limit": limit,
        "offset": 0,
        "sources": list(ACTIVE_CATALOG_SOURCES),
        "cached": True,
        "refreshing": True,
        "version": CATALOG_SNAPSHOT_VERSION,
    }
    return data


def _partner_catalog_source_unbounded(provider: str, limit: int) -> tuple[list[dict], dict | None]:
    if provider == "nexus":
        payload = reader.catalog_nexus(limit=limit)
    elif provider == "mangageek":
        payload = reader.catalog_mangageek(limit=limit)
    elif provider == "mangakatana":
        payload = reader.catalog_mangakatana(limit=limit)
    elif provider == "mangasbrasuka":
        payload = reader.catalog_mangasbrasuka(limit=limit)
    elif provider == "mangalivre":
        payload = reader.catalog_mangalivre(limit=limit)
    elif provider == "fliptru":
        payload = reader.catalog_fliptru(limit=limit)
    else:
        return [], None

    label = _source_label(provider)
    items: list[dict] = []
    for raw in payload.get("results") or []:
        item = _normalize_manga_item(raw, section=f"Catalogo - {label}")
        if item:
            items.append(item)
    items = _dedupe(items)[:limit]
    if not items:
        return [], None
    return items, {"title": f"Catalogo - {label}", "items": items}


def _partner_catalog_source(provider: str, limit: int) -> tuple[list[dict], dict | None]:
    return scraper_coordinator.run(
        provider,
        f"catalog:{provider}:{limit}",
        lambda: _partner_catalog_source_unbounded(provider, limit),
    )


def _dedupe_cross_source_sections(sections: list[dict]) -> list[dict]:
    candidates: dict[str, list[dict]] = {}
    catalog_memberships: dict[str, set[str]] = {}
    for section in sections:
        section_title = str(section.get("title") or "")
        is_catalog_section = normalize_match_text(section_title).startswith("catalogo ")
        for item in section.get("items") or []:
            identity = _canonical_title_identity(str(item.get("title") or ""))
            if identity:
                candidates.setdefault(identity, []).append(item)
                if is_catalog_section:
                    catalog_memberships.setdefault(identity, set()).add(section_title)

    winners: dict[str, dict] = {}
    for identity, items in candidates.items():
        providers = {str(item.get("provider") or "").lower() for item in items}
        if len(providers) <= 1 and len(catalog_memberships.get(identity, set())) <= 1:
            continue
        winner = items[0]
        for candidate in items[1:]:
            winner = _merge_duplicate(winner, candidate)
        winners[identity] = winner

    if not winners:
        return sections

    emitted: set[str] = set()
    cleaned: list[dict] = []
    for section in sections:
        section_items: list[dict] = []
        for item in section.get("items") or []:
            identity = _canonical_title_identity(str(item.get("title") or ""))
            winner = winners.get(identity)
            if winner is None:
                section_items.append(item)
                continue
            same_source = (
                str(item.get("provider") or "").lower() == str(winner.get("provider") or "").lower()
                and str(item.get("source_url") or "") == str(winner.get("source_url") or "")
            )
            if not same_source or identity in emitted:
                continue
            emitted.add(identity)
            section_items.append(winner)
        if section_items:
            cleaned.append({**section, "items": section_items})
    return cleaned


def _partner_catalog_sections(limit: int = PARTNER_CATALOG_LIMIT) -> tuple[list[dict], list[dict]]:
    providers = tuple(
        provider
        for provider in ("fliptru", "nexus", "mangageek", "mangakatana", "mangasbrasuka", "mangalivre")
        if _provider_enabled(provider)
    )
    all_items: list[dict] = []
    sections: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = {
            executor.submit(_partner_catalog_source, provider, limit): provider
            for provider in providers
        }
        for future in as_completed(futures):
            provider = futures[future]
            try:
                items, section = future.result()
            except Exception as exc:
                logger.warning(
                    "Falha ao atualizar catalogo source=%s error=%s",
                    provider,
                    _safe_error(exc),
                )
                continue
            all_items.extend(items)
            if section:
                sections.append(section)
    order = {provider: index for index, provider in enumerate(providers)}
    sections.sort(
        key=lambda section: order.get(
            str((section.get("items") or [{}])[0].get("provider") or ""),
            len(order),
        )
    )
    sections = _dedupe_cross_source_sections(sections)
    balanced_items: list[dict] = []
    max_size = max((len(section.get("items") or []) for section in sections), default=0)
    for index in range(max_size):
        for section in sections:
            source_items = section.get("items") or []
            if index < len(source_items):
                balanced_items.append(source_items[index])
    return _dedupe(balanced_items or all_items), sections


def _refresh_catalog_cache(limit: int = DEFAULT_LIMIT) -> None:
    global catalog_cache, catalog_refreshing
    try:
        previous = dict(catalog_cache.data) if catalog_cache else (_read_catalog_snapshot() or {})
        previous_items = list(previous.get("items") or [])
        previous_sections = list(previous.get("sections") or [])
        previous_partner_sections = [
            section
            for section in previous_sections
            if normalize_match_text(str(section.get("title") or "")).startswith("catalogo ")
        ]
        executor = ThreadPoolExecutor(max_workers=2)
        mangadex_future = executor.submit(
            scraper_coordinator.run,
            "mangadex",
            f"catalog:mangadex:{min(max(limit, 24), 80)}",
            lambda: _catalog_sections_from_mangadex(min(max(limit, 24), 80)),
        )
        partner_future = executor.submit(_partner_catalog_sections, PARTNER_CATALOG_LIMIT)
        try:
            partner_items, partner_sections = partner_future.result(timeout=30)
        except Exception as exc:
            logger.warning(
                "Falha ao atualizar catalogos parceiros; mantendo snapshot error=%s",
                _safe_error(exc),
            )
            partner_items, partner_sections = [], previous_partner_sections
        try:
            items, sections = mangadex_future.result(timeout=30)
        except Exception as exc:
            logger.warning(
                "MangaDex indisponivel; mantendo snapshot error=%s",
                _safe_error(exc),
            )
            items = previous_items
            sections = [
                section
                for section in previous_sections
                if section not in previous_partner_sections
            ]
        finally:
            # Nao deixa uma fonte lenta segurar a publicacao das demais.
            executor.shutdown(wait=False)

        if not partner_sections and previous_partner_sections:
            partner_sections = previous_partner_sections
            partner_items = [
                item
                for section in previous_partner_sections
                for item in (section.get("items") or [])
            ]

        items = _dedupe([*partner_items, *items])
        insert_at = 1 if sections and sections[0].get("layout") == "carousel" else 0
        while insert_at < len(sections) and str(sections[insert_at].get("title") or "").startswith("Rec"):
            insert_at += 1
        sections[insert_at:insert_at] = partner_sections
        sections = _dedupe_cross_source_sections(sections)
        if not sections and items:
            sections = _build_sections_from_items(items)

        data = {
            "items": items,
            "sections": sections,
            "total": len(items),
            "limit": limit,
            "offset": 0,
            "sources": list(ACTIVE_CATALOG_SOURCES),
            "cached": False,
            "refreshing": True,
            "version": CATALOG_SNAPSHOT_VERSION,
        }
        # Publica listagens leves imediatamente. Metadados/capas continuam no mesmo
        # worker e nunca bloqueiam a resposta da homepage.
        catalog_cache = CacheEntry(time.time(), data)
        _write_catalog_snapshot(data)

        # enrich items + section items (dedup por identidade, cap p/ nao estourar rate-limit)
        seen_ids: set[int] = set()
        bucket: list[dict] = []
        for collection in [items, *[sec.get("items") or [] for sec in sections]]:
            for it in collection:
                if id(it) not in seen_ids:
                    seen_ids.add(id(it))
                    bucket.append(it)
        # Confirma capitulos antes do trabalho pesado de capas. Cada card fica
        # visivel assim que sua propria fonte responde, sem esperar o ciclo todo.
        _prewarm_chapters(bucket, limit=200, max_workers=4)
        mangadex_items = [item for item in bucket if _guess_provider(item) == "mangadex"]
        _enrich_items_metadata(mangadex_items[:24], max_workers=4)
        # Baixa as capas p/ static/covers (define item['cover_path']) e re-grava o
        # snapshot ja com os caminhos locais -> home serve estatico, sem proxy.
        _download_covers_to_disk(bucket, limit=96)
        # Capa falhou? tenta fonte alternativa (MangaDex/AniList por titulo);
        # so marca placeholder (incompleta) se nem assim achar.
        for it in bucket[:96]:
            if _cover_file_exists(it.get("cover_path") or ""):
                continue
            if not _recover_and_store_cover(it):
                it["cover_path"] = PLACEHOLDER_URL
        catalog_cache = CacheEntry(time.time(), data)
        _write_catalog_snapshot(data)
        data["refreshing"] = False
        catalog_cache = CacheEntry(time.time(), data)
        _write_catalog_snapshot(data)
    finally:
        with catalog_refresh_lock:
            catalog_refreshing = False


def _schedule_catalog_refresh(limit: int = DEFAULT_LIMIT) -> None:
    global catalog_refreshing
    with catalog_refresh_lock:
        if catalog_refreshing:
            return
        catalog_refreshing = True
    thread = threading.Thread(target=_refresh_catalog_cache, args=(limit,), daemon=True)
    thread.start()


def _apply_curated_source_overrides(items: list[dict], query: str) -> list[dict]:
    """Substitui obras curadas por fontes preferidas quando a busca bate com o titulo."""
    updated = list(items)
    for raw in _iter_curated():
        source_url = str(raw.get("url") or "").strip()
        if not source_url:
            continue
        title = str(raw.get("title") or "").strip()
        if _curated_match_score(query, raw) < 0.78:
            continue
        curated = _normalize_manga_item(
            {
                **raw,
                "section": "Busca",
                "alternative_titles": raw.get("aliases") or [],
            }
        )
        if not curated:
            continue
        aliases = [title, *[str(alias) for alias in raw.get("aliases") or []]]
        updated = [
            item
            for item in updated
            if max(
                fuzzy_match_score(alias, str(item.get("title") or ""))
                for alias in aliases
            ) < 0.92
        ]
        curated["relevance"] = max(
            float(curated.get("relevance") or 0),
            max((float(item.get("relevance") or 0) for item in items), default=0.0),
            1.0 if _curated_match_score(query, raw) >= 0.98 else 0.95,
        )
        updated.insert(0, curated)
    return updated


def _resolve_best_sources(items: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for item in items:
        identity = _canonical_title_identity(str(item.get("title") or "")) or str(item.get("source_url") or "")
        grouped.setdefault(identity, []).append(item)

    resolved: list[dict] = []
    to_race: list[list[dict]] = []
    for group in grouped.values():
        providers = {str(item.get("provider") or "") for item in group}
        if len(group) == 1 or len(providers) == 1:
            resolved.append(group[0])
            continue
        to_race.append(group)

    if not to_race:
        return resolved

    def score_group(group: list[dict]) -> dict:
        scored: list[tuple[tuple[int, float, float], dict]] = []
        for item in group:
            source_url = str(item.get("source_url") or "")
            chapter_count = int(item.get("chapter_count") or 0)
            if source_url and chapter_count <= 0:
                chapter_count = _chapter_count_for_source(source_url)
                if chapter_count:
                    item = dict(item)
                    item["chapter_count"] = chapter_count
            provider = str(item.get("provider") or "").lower()
            if provider == "mangadex" and chapter_count < SPARSE_CHAPTER_THRESHOLD:
                chapter_count = 0
            scored.append(
                (
                    (
                        chapter_count,
                        SOURCE_RELIABILITY.get(provider, 0.5),
                        float(item.get("relevance") or 0),
                    ),
                    item,
                )
            )
        scored.sort(reverse=True)
        return scored[0][1]

    max_workers = min(6, len(to_race))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(score_group, group) for group in to_race]
        for future in as_completed(futures):
            try:
                resolved.append(future.result())
            except Exception:
                continue
    return resolved


def _catalog_sections_from_mangadex(limit: int) -> tuple[list[dict], list[dict]]:
    all_items: list[dict] = []
    sections: list[dict] = []

    trending = reader.trending_mangadex(limit=min(max(limit, 24), 100))
    trending_items: list[dict] = []
    for raw in trending.get("results") or []:
        item = _normalize_manga_item(raw, section="Lancamentos recentes")
        if item:
            trending_items.append(item)
    trending_items = _dedupe(trending_items)[:24]
    if trending_items:
        sections.append({"title": "Lancamentos recentes", "items": trending_items})
        all_items.extend(trending_items)

    # gêneros mais ricos: minimo 12, ate 18 por seção
    per_genre = max(12, min(18, limit // 4))
    # Use no language filter so we get covers even for manga not yet translated to pt-br
    catalog = reader.catalog_mangadex(MANGADEX_GENRES, limit_per_genre=per_genre, lang="")
    for section, section_items in (catalog.get("sections") or {}).items():
        normalized_items: list[dict] = []
        for raw in section_items or []:
            item = _normalize_manga_item(raw, section=section)
            if item:
                normalized_items.append(item)
        normalized_items = _dedupe(normalized_items)[:per_genre]
        if normalized_items:
            sections.append({"title": section, "items": normalized_items})
            all_items.extend(normalized_items)

    # If genre sections came back empty, use the trending items split into a generic section
    if len(sections) <= 1 and all_items:
        sections = _build_sections_from_items(all_items, per_section=per_genre)

    # Carrossel "Em alta": trending real (AniList+Kitsu) cruzado com o catalogo, no topo
    highlights = _trending_highlights(all_items, limit=20)
    if highlights:
        sections.insert(0, {"title": "Em alta", "items": highlights, "layout": "carousel"})

    # Carrossel "Recém-lançados" por fonte (logo abaixo de Em alta)
    latest_pos = 1 if highlights else 0
    for sec in _latest_release_sections():
        sections.insert(latest_pos, sec)
        all_items.extend(sec["items"])
        latest_pos += 1

    return _dedupe(all_items), sections


def _build_catalog(limit: int) -> dict:
    global catalog_cache
    if _cache_is_fresh(catalog_cache, CATALOG_CACHE_TTL_SECONDS):
        return _snapshot_payload(catalog_cache.data, limit)

    snapshot = _read_catalog_snapshot()
    if snapshot:
        catalog_cache = CacheEntry(time.time(), snapshot)
        age = _catalog_snapshot_age()
        if (
            snapshot.get("version") != CATALOG_SNAPSHOT_VERSION
            or snapshot.get("refreshing") is True
            or age is None
            or age > CATALOG_SNAPSHOT_TTL_SECONDS
        ):
            _schedule_catalog_refresh(max(limit, DEFAULT_LIMIT))
            snapshot = {**snapshot, "refreshing": True, "cached": True}
        return _snapshot_payload(snapshot, limit)

    data = _fast_catalog_seed(limit)
    _schedule_catalog_refresh(max(limit, DEFAULT_LIMIT))
    return data


def _search_source_unbounded(name: str, query: str, limit: int) -> list[dict]:
    if name == "mangadex":
        payload = reader.search_mangadex(query, limit=limit)
    elif name == "mangalivre":
        payload = reader.search_mangalivre(query, limit=limit)
    elif name == "toomics":
        payload = reader.search_toomics(query, limit=limit, lang="pt-br")
    elif name == "mangasbrasuka":
        payload = reader.search_mangasbrasuka(query, limit=limit)
    elif name == "sakura":
        payload = reader.search_sakura(query, limit=limit)
    elif name == "nexus":
        payload = reader.search_nexus(query, limit=limit)
    elif name == "mangageek":
        payload = reader.search_mangageek(query, limit=limit)
    elif name == "mangakatana":
        payload = reader.search_mangakatana(query, limit=limit)
    elif name == "fliptru":
        payload = reader.search_fliptru(query, limit=limit)
    else:
        return []

    items = []
    for raw in payload.get("results") or []:
        item = _normalize_manga_item(raw, section="Busca")
        if item:
            items.append(item)
    return items


def _search_source(name: str, query: str, limit: int) -> list[dict]:
    return scraper_coordinator.run(
        name,
        f"search:{query.casefold()}:{limit}",
        lambda: _search_source_unbounded(name, query, limit),
    )


def _search_sources_with_timeout(
    sources: list[str],
    query: str,
    limit: int,
    *,
    timeout: float = SOURCE_SEARCH_TIMEOUT_SECONDS,
) -> tuple[list[dict], list[str]]:
    if not sources:
        return [], []

    items: list[dict] = []
    errors: list[str] = []
    executor = ThreadPoolExecutor(max_workers=len(sources))
    futures = {
        executor.submit(_search_source, source, query, limit): source
        for source in sources
    }
    try:
        done, pending = wait(futures, timeout=timeout)
        for future in done:
            source = futures[future]
            try:
                items.extend(future.result())
            except Exception as exc:
                errors.append(f"{_source_label(source)}: {exc}")
        for future in pending:
            source = futures[future]
            future.cancel()
            errors.append(f"{_source_label(source)}: timeout")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return items, errors


def _copy_with_chapter_count(item: dict) -> dict:
    copied = dict(item)
    count = int(copied.get("chapter_count") or 0)
    source_url = str(copied.get("source_url") or "")
    if source_url and count <= 0:
        count = _chapter_count_for_source(source_url)
        if count:
            copied["chapter_count"] = count
    return copied


def _source_score_tuple(item: dict) -> tuple[int, float, float]:
    provider = str(item.get("provider") or "").lower()
    chapter_count = int(item.get("chapter_count") or 0)
    if provider == "mangadex" and chapter_count < SPARSE_CHAPTER_THRESHOLD:
        chapter_count = 0
    return (
        chapter_count,
        SOURCE_RELIABILITY.get(provider, 0.5),
        float(item.get("relevance") or 0),
    )


def _current_source_item(title: str, source_url: str) -> dict | None:
    if not source_url:
        return None
    return _normalize_manga_item(
        {
            "title": title or source_url,
            "url": source_url,
            "provider": _guess_provider({"url": source_url}),
        },
        section="Fonte atual",
    )


def _mangadex_alt_titles(source_url: str) -> list[str]:
    """Busca titulos alternativos de um manga no MangaDex (altTitles do metadata).

    Retorna lista de strings com PT-BR e EN no topo (sao os mais provaveis de
    encontrar nas fontes brasileiras como MangaLivre).
    """
    manga_id = reader._mangadex_manga_id_from_source(source_url)
    if not manga_id:
        return []
    try:
        payload = reader._mangadex_get(f"/manga/{manga_id}", {"includes[]": ["author"]})
        attrs = (payload.get("data") or {}).get("attributes") or {}
        priority: list[str] = []  # pt-br, en, ja-ro
        rest: list[str] = []
        seen: set[str] = set()

        def add(text: str, is_priority: bool) -> None:
            text = str(text or "").strip()
            if not text or text in seen:
                return
            seen.add(text)
            (priority if is_priority else rest).append(text)

        # titulo principal
        main_title = attrs.get("title") or {}
        for lang_code, text in main_title.items():
            add(text, lang_code in ("pt-br", "en", "ja-ro"))

        # alt titles — separa priority langs do resto
        priority_langs = {"pt-br", "pt", "en", "ja-ro"}
        for alt in attrs.get("altTitles") or []:
            if not isinstance(alt, dict):
                continue
            for lang_code, text in alt.items():
                add(text, lang_code in priority_langs)

        return priority + rest
    except Exception:
        return []


def _source_search_terms(title: str) -> list[str]:
    raw = str(title or "").strip()
    if not raw:
        return []
    plain = re.sub(r"[-‐‑‒–—_/]+", " ", raw)
    plain = re.sub(r"[^\w\s]", " ", plain, flags=re.UNICODE)
    plain = re.sub(r"\s+", " ", plain).strip()
    return list(dict.fromkeys(term for term in (raw, plain) if term))


def _fallback_source_via_alt_titles(
    title: str, original_source: str, failed_source: str, lang: str
) -> str:
    """Quando a fonte resolvida falha, tenta achar
    fonte alternativa via titulo/alt titles. Prioriza PT-completas e, como ultimo
    recurso, o MangaDex (que quase sempre tem a obra). Retorna source_url ou "".
    """
    complete_sources = _pt_complete_sources()
    if "mangadex" not in complete_sources:
        complete_sources = [*complete_sources, "mangadex"]
    search_terms = _source_search_terms(title)

    # Busca alt titles do MangaDex se fonte original era MangaDex
    if _guess_provider({"url": original_source}) == "mangadex":
        alt = _mangadex_alt_titles(original_source)
        alt = [t for t in alt if normalize_match_text(t) != normalize_match_text(title)]
        for alt_title in alt[:4]:
            search_terms.extend(_source_search_terms(alt_title))
    search_terms = list(dict.fromkeys(search_terms))

    for term in search_terms:
        candidates, _ = _search_sources_with_timeout(
            complete_sources,
            term,
            5,
            timeout=SOURCE_RESOLUTION_TIMEOUT_SECONDS,
        )
        for hit in candidates:
            score = _search_match_score(term, hit)
            if score < 0.7:
                continue
            hit_url = str(hit.get("source_url") or hit.get("url") or "").strip()
            if not hit_url or hit_url == failed_source:
                continue
            hit = _copy_with_chapter_count(hit)
            if int(hit.get("chapter_count") or 0) > 0:
                return hit_url
    return ""


def _resolve_best_source_for_title(title: str, current_source_url: str, lang: str = "pt-br") -> dict | None:
    title = title.strip()
    current_source_url = current_source_url.strip()
    if not title and not current_source_url:
        return None

    cache_key = f"{normalize_match_text(title)}|{current_source_url}|{normalize_match_text(lang)}"
    cached = source_resolution_cache.get(cache_key)
    if _cache_is_fresh(cached, SOURCE_RESOLUTION_CACHE_TTL_SECONDS):
        return dict(cached.data.get("item") or {})

    if _is_one_piece_title(title):
        raw = next(
            (item for item in CURATED_CATALOG if _is_one_piece_title(str(item.get("title") or ""))),
            None,
        )
        preferred = _normalize_manga_item(
            {**raw, "section": "Fonte preferida"},
            section="Fonte preferida",
        ) if raw else None
        if preferred:
            source_resolution_cache[cache_key] = CacheEntry(time.time(), {"item": preferred})
            return preferred

    current = _current_source_item(title, current_source_url)
    complete_sources = _pt_complete_sources()
    current_provider = str((current or {}).get("provider") or "").lower()
    current_outage = source_outage_cache.get(current_source_url)
    current_available = not _cache_is_fresh(
        current_outage,
        SOURCE_RESOLUTION_CACHE_TTL_SECONDS,
    )
    if (
        current
        and current_provider in complete_sources
        and current_available
        and _cached_chapter_count(current_source_url, lang) > 0
    ):
        source_resolution_cache[cache_key] = CacheEntry(time.time(), {"item": current})
        return current

    if current and current_provider == "mangadex":
        current_with_count = _copy_with_chapter_count(current)
        if int(current_with_count.get("chapter_count") or 0) >= SPARSE_CHAPTER_THRESHOLD:
            source_resolution_cache[cache_key] = CacheEntry(time.time(), {"item": current_with_count})
            return current_with_count

    curated_raw = _curated_override_for_title(title)
    if curated_raw:
        curated = _enrich_curated_item({**curated_raw, "section": "Fonte completa"})
        if curated:
            source_resolution_cache[cache_key] = CacheEntry(time.time(), {"item": curated})
            return curated

    if not title:
        return current

    # Busca nas fontes PT-completas pelo titulo principal e, se necessario,
    # por titulos alternativos do MangaDex (ex: "Oyasumi Punpun" -> "Goodnight Punpun").
    search_titles = _source_search_terms(title)
    candidates: list[dict] = []
    for search_title in search_titles:
        found, _errors = _search_sources_with_timeout(
            complete_sources,
            search_title,
            5,
            timeout=SOURCE_RESOLUTION_TIMEOUT_SECONDS,
        )
        candidates.extend(found)

    exact_hits: list[tuple[int, float, dict]] = []
    for hit in candidates:
        score = max(_search_match_score(search_title, hit) for search_title in search_titles)
        if score < 0.92:
            continue
        provider = str(hit.get("provider") or "").lower()
        source_order = complete_sources.index(provider) if provider in complete_sources else len(complete_sources)
        exact_hits.append((source_order, score, hit))
    if exact_hits:
        exact_hits.sort(key=lambda pair: (pair[0], -pair[1]))
        for source_order, score, hit in exact_hits:
            candidate = _copy_with_chapter_count(dict(hit))
            if int(candidate.get("chapter_count") or 0) <= 0:
                continue
            candidate["relevance"] = round(score, 4)
            source_resolution_cache[cache_key] = CacheEntry(time.time(), {"item": candidate})
            return candidate

    scored_candidates: list[dict] = []
    for item in candidates:
        relevance = max(_search_match_score(search_title, item) for search_title in search_titles)
        if relevance < MIN_SOURCE_RELEVANCE:
            continue
        item = dict(item)
        item["relevance"] = round(relevance, 4)
        item = _copy_with_chapter_count(item)
        if int(item.get("chapter_count") or 0) <= 0:
            continue
        scored_candidates.append(item)

    # Alt-title fallback SO para mangas quase vazios no MangaDex em TODAS as
    # linguas (ex: Punpun, licenciado/removido). Mangas com muitos caps em
    # qualquer lingua NAO entram aqui — evita 20s de busca desnecessaria.
    if not scored_candidates and _guess_provider({"url": current_source_url}) == "mangadex":
        try:
            total_all_langs = reader.mangadex_chapter_total(current_source_url, "")
        except Exception:
            total_all_langs = 0
        if total_all_langs < SPARSE_CHAPTER_THRESHOLD:
            alt_titles = _mangadex_alt_titles(current_source_url)
            alt_titles = [t for t in alt_titles if normalize_match_text(t) != normalize_match_text(title)]
            for alt in alt_titles[:2]:
                alt_candidates, _ = _search_sources_with_timeout(
                    complete_sources,
                    alt,
                    5,
                    timeout=3.0,
                )
                for hit in alt_candidates:
                    score = _search_match_score(alt, hit)
                    if score < 0.7:
                        continue
                    hit = dict(hit)
                    hit["relevance"] = round(score, 4)
                    hit = _copy_with_chapter_count(hit)
                    if int(hit.get("chapter_count") or 0) <= 0:
                        continue
                    scored_candidates.append(hit)
                if scored_candidates:
                    break

    all_items = [*scored_candidates]
    if current:
        all_items.append(_copy_with_chapter_count(current))
    if not all_items:
        return None

    all_items.sort(key=_source_score_tuple, reverse=True)
    best = all_items[0]
    if current and best.get("source_url") == current.get("source_url"):
        source_resolution_cache[cache_key] = CacheEntry(time.time(), {"item": current})
        return current

    current_count = int((current or {}).get("chapter_count") or 0)
    best_count = int(best.get("chapter_count") or 0)
    current_provider = str((current or {}).get("provider") or "").lower()
    should_swap = (
        not current
        or best_count > current_count
        or (current_provider == "mangadex" and best_count > 0)
    )
    resolved = best if should_swap else current
    source_resolution_cache[cache_key] = CacheEntry(time.time(), {"item": resolved})
    return resolved


def _source_resolution_key(title: str, source_url: str, lang: str) -> str:
    return f"{normalize_match_text(title)}|{source_url.strip()}|{normalize_match_text(lang)}"


def _cached_source_resolution(title: str, source_url: str, lang: str) -> dict | None:
    entry = source_resolution_cache.get(_source_resolution_key(title, source_url, lang))
    if not _cache_is_fresh(entry, SOURCE_RESOLUTION_CACHE_TTL_SECONDS):
        return None
    return dict(entry.data.get("item") or {})


def _refresh_source_resolution(title: str, source_url: str, lang: str, key: str) -> None:
    try:
        _resolve_best_source_for_title(title, source_url, lang)
    except Exception as exc:
        logger.debug("Falha ao resolver fonte em background p/ %s: %s", title, exc)
    finally:
        with _source_resolution_refresh_lock:
            _source_resolution_refreshing.discard(key)


def _schedule_source_resolution(title: str, source_url: str, lang: str) -> None:
    key = _source_resolution_key(title, source_url, lang)
    with _source_resolution_refresh_lock:
        if key in _source_resolution_refreshing:
            return
        _source_resolution_refreshing.add(key)
    threading.Thread(
        target=_refresh_source_resolution,
        args=(title, source_url, lang, key),
        daemon=True,
    ).start()


def _search_match_score(query: str, item: dict) -> float:
    alternative_titles = item.get("alternative_titles") or []
    if not isinstance(alternative_titles, list):
        alternative_titles = [str(alternative_titles)]
    title_candidates = [
        str(item.get("title") or ""),
        *[str(title) for title in alternative_titles],
    ]
    searchable = normalize_match_text(" ".join(title_candidates))
    query_norm = normalize_match_text(query)
    # CJK/Unicode removido pelo normalizador nao pode virar match perfeito.
    if not query_norm:
        return 0.0
    raw_query_tokens = query_norm.split()
    # Single-letter suffixes distinguish series such as GANTZ:G and Gantz:E.
    # Keep them for compact titles while avoiding noisy one-letter words in
    # normal long-form searches.
    query_tokens = [
        token
        for token in raw_query_tokens
        if len(token) >= 2 or (len(raw_query_tokens) <= 2 and len(token) == 1)
    ]
    searchable_tokens = set(searchable.split())
    if query_norm and query_norm in searchable:
        return 1.0
    fuzzy_score = fuzzy_match_score(query, *title_candidates)
    if query_tokens:
        token_hits = sum(
            1 for token in query_tokens
            if token in searchable_tokens or (len(token) > 1 and token in searchable)
        )
        required_hits = len(query_tokens) if len(query_tokens) <= 2 else max(2, round(len(query_tokens) * 0.65))
        if token_hits < required_hits:
            return fuzzy_score if fuzzy_score >= _fuzzy_search_threshold(query) else 0.0
    return fuzzy_score


def _anilist_metadata(title: str) -> dict:
    key = normalize_match_text(title)
    cached = anilist_cache.get(key)
    if _cache_is_fresh(cached, ANILIST_CACHE_TTL_SECONDS):
        return dict(cached.data)
    metadata = reader.anilist_metadata(title)
    anilist_cache[key] = CacheEntry(time.time(), metadata)
    return dict(metadata)


def _anilist_graphql(query: str, variables: dict) -> dict:
    response = requests.post(
        ANILIST_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        timeout=getattr(reader.args, "timeout", 20),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_HEADERS["User-Agent"],
        },
    )
    try:
        payload = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError("AniList retornou uma resposta invalida.")
    if payload.get("errors"):
        message = payload["errors"][0].get("message") or "AniList retornou erro."
        raise RuntimeError(message)
    response.raise_for_status()
    return payload.get("data") or {}


def _mangaupdates_request(method: str, path: str, json_body: dict | None = None) -> dict:
    global mangaupdates_last_request_at

    url = f"{MANGAUPDATES_API_URL}/{path.lstrip('/')}"
    last_error: Exception | None = None
    for attempt in range(MANGAUPDATES_REQUEST_ATTEMPTS):
        retry_after = 0.0
        with mangaupdates_request_lock:
            wait_for = MANGAUPDATES_REQUEST_GAP_SECONDS - (
                time.monotonic() - mangaupdates_last_request_at
            )
            if wait_for > 0:
                time.sleep(wait_for)
            try:
                response = requests.request(
                    method.upper(),
                    url,
                    json=json_body,
                    timeout=(8, 20),
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "Kari/0.1 (local personal manga reader; MangaUpdates fallback)",
                    },
                )
                mangaupdates_last_request_at = time.monotonic()
            except requests.RequestException as exc:
                mangaupdates_last_request_at = time.monotonic()
                last_error = exc
                response = None

        if response is None:
            if attempt < MANGAUPDATES_REQUEST_ATTEMPTS - 1:
                time.sleep(1.0 * (2 ** attempt))
                continue
            raise last_error if last_error else RuntimeError("MangaUpdates indisponivel.")

        if response.status_code in {429, 500, 502, 503, 504}:
            try:
                retry_after = float(response.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                retry_after = 0.0
            if attempt < MANGAUPDATES_REQUEST_ATTEMPTS - 1:
                time.sleep(max(retry_after, 1.0 * (2 ** attempt)))
                continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("MangaUpdates retornou resposta invalida.")
        return payload

    raise last_error if last_error else RuntimeError("MangaUpdates indisponivel.")


def _clean_anilist_staff_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"</?[^>]+>", " ", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _anilist_date_text(value: dict | None) -> str:
    if not value:
        return ""
    year = value.get("year")
    month = value.get("month")
    day = value.get("day")
    if year and month and day:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if year and month:
        return f"{year:04d}-{month:02d}"
    if year:
        return str(year)
    if month and day:
        return f"{month:02d}-{day:02d}"
    return ""


def _same_author_name(left: str, right: str) -> bool:
    left_raw = re.sub(r"\s+", " ", str(left or "").strip()).casefold()
    right_raw = re.sub(r"\s+", " ", str(right or "").strip()).casefold()
    if left_raw and left_raw == right_raw:
        return True
    left_norm = normalize_match_text(left)
    right_norm = normalize_match_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    left_parts = left_norm.split()
    right_parts = right_norm.split()
    return len(left_parts) > 1 and sorted(left_parts) == sorted(right_parts)


def _author_cache_key_text(value: str) -> str:
    normalized = normalize_match_text(value)
    if normalized:
        return normalized
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _staff_payload(staff: dict, role: str = "", title: str = "") -> dict:
    name = staff.get("name") or {}
    image = staff.get("image") or {}
    large = image.get("large")
    medium = image.get("medium")
    site_url = staff.get("siteUrl") or ""
    return {
        "id": staff.get("id"),
        "name": name.get("full") or "",
        "native_name": name.get("native") or "",
        "alternative_names": name.get("alternative") or [],
        "role": role or "",
        "matched_title": title or "",
        "image_url": large or medium or "",
        "image_fallbacks": [url for url in [medium] if url and url != large],
        "description": _clean_anilist_staff_text(staff.get("description")),
        "gender": staff.get("gender") or "",
        "birth_date": _anilist_date_text(staff.get("dateOfBirth")),
        "death_date": _anilist_date_text(staff.get("dateOfDeath")),
        "age": staff.get("age"),
        "years_active": staff.get("yearsActive") or [],
        "home_town": staff.get("homeTown") or "",
        "occupations": staff.get("primaryOccupations") or [],
        "language": staff.get("languageV2") or "",
        "favourites": staff.get("favourites"),
        "social_links": [],
        "site_url": site_url,
        "source_links": [{"label": "AniList", "url": site_url}] if site_url else [],
        "source": "AniList",
    }


def _anilist_staff_fields() -> str:
    return """
      id
      siteUrl
      name { full native alternative }
      image { large medium }
      description
      gender
      dateOfBirth { year month day }
      dateOfDeath { year month day }
      age
      yearsActive
      homeTown
      primaryOccupations
      languageV2
      favourites
    """


def _lookup_author_from_anilist(name: str, title: str = "") -> dict:
    author_name = str(name or "").strip()
    media_title = str(title or "").strip()
    if not author_name:
        raise ValueError("Informe o nome do autor.")

    cache_key = f"author:{_author_cache_key_text(author_name)}|{normalize_match_text(media_title)}"
    cached = author_cache.get(cache_key)
    if _cache_is_fresh(cached, ANILIST_CACHE_TTL_SECONDS):
        return dict(cached.data)

    fields = _anilist_staff_fields()
    if media_title:
        query = f"""
        query AuthorFromManga($search: String) {{
          Media(search: $search, type: MANGA) {{
            title {{ romaji english native }}
            staff(sort: [RELEVANCE, FAVOURITES_DESC], perPage: 20) {{
              edges {{
                role
                node {{ {fields} }}
              }}
            }}
          }}
        }}
        """
        media = (_anilist_graphql(query, {"search": media_title}).get("Media") or {})
        titles = media.get("title") or {}
        matched_title = titles.get("english") or titles.get("romaji") or titles.get("native") or media_title
        for edge in ((media.get("staff") or {}).get("edges") or []):
            staff = edge.get("node") or {}
            staff_name = staff.get("name") or {}
            candidates = [
                staff_name.get("full"),
                staff_name.get("native"),
                *(staff_name.get("alternative") or []),
            ]
            if any(_same_author_name(author_name, str(candidate or "")) for candidate in candidates):
                result = _staff_payload(staff, edge.get("role") or "", matched_title)
                author_cache[cache_key] = CacheEntry(time.time(), result)
                return dict(result)

    query = f"""
    query AuthorByName($search: String) {{
      Staff(search: $search) {{
        {fields}
      }}
    }}
    """
    staff = (_anilist_graphql(query, {"search": author_name}).get("Staff") or {})
    if not staff:
        raise RuntimeError(f"AniList nao encontrou autor: {author_name}")
    result = _staff_payload(staff)
    author_cache[cache_key] = CacheEntry(time.time(), result)
    return dict(result)


def _mangaupdates_image_urls(image: dict | None) -> tuple[str, list[str]]:
    urls = (image or {}).get("url") or {}
    original = str(urls.get("original") or "").strip()
    thumb = str(urls.get("thumb") or "").strip()
    primary = original or thumb
    fallbacks = [url for url in [thumb, original] if url and url != primary]
    return primary, fallbacks


def _mangaupdates_date_text(value: dict | None) -> str:
    if not value:
        return ""
    as_string = str(value.get("as_string") or "").strip()
    if as_string:
        return as_string
    year = value.get("year")
    month = value.get("month")
    day = value.get("day")
    if year and month and day:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    if year and month:
        return f"{int(year):04d}-{int(month):02d}"
    return str(year or "")


def _mangaupdates_author_score(query: str, result: dict) -> float:
    record = result.get("record") or {}
    names = [record.get("name"), result.get("hit_name")]
    if any(_same_author_name(query, str(name or "")) for name in names):
        return 1.0
    return max(
        (fuzzy_match_score(query, str(name or "")) for name in names if str(name or "").strip()),
        default=0.0,
    )


def _author_social_links(social: object) -> list[dict]:
    if not isinstance(social, dict):
        return []
    labels = {
        "officialsite": "Site oficial", "website": "Site oficial", "twitter": "Twitter",
        "facebook": "Facebook", "instagram": "Instagram", "pixiv": "Pixiv",
        "youtube": "YouTube", "tiktok": "TikTok", "tumblr": "Tumblr",
    }
    links: list[dict] = []
    seen: set[str] = set()
    for key, value in social.items():
        url = str(value or "").strip()
        if not url:
            continue
        if key == "twitter" and url.startswith("@"):
            url = f"https://x.com/{url[1:]}"
        if not re.match(r"^https?://", url, flags=re.I) or url in seen:
            continue
        seen.add(url)
        links.append({"label": labels.get(str(key).lower(), str(key).replace("_", " ").title()), "url": url})
    return links


def _lookup_author_from_mangaupdates(name: str, title: str = "") -> dict:
    author_name = str(name or "").strip()
    media_title = str(title or "").strip()
    if not author_name:
        raise ValueError("Informe o nome do autor.")

    cache_key = (
        f"mu-author:v2:{_author_cache_key_text(author_name)}|"
        f"{normalize_match_text(media_title)}"
    )
    cached = mangaupdates_author_cache.get(cache_key)
    if _cache_is_fresh(cached, MANGAUPDATES_CACHE_TTL_SECONDS):
        if not cached.data:
            raise RuntimeError(f"MangaUpdates nao encontrou autor: {author_name}")
        return dict(cached.data)

    search = _mangaupdates_request(
        "POST",
        "/authors/search",
        {"search": author_name, "perpage": 5},
    )
    ranked = sorted(
        (
            (_mangaupdates_author_score(author_name, result), result)
            for result in search.get("results") or []
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.82:
        series = _mangaupdates_series_metadata(media_title) if media_title else {}
        is_series_author = any(
            _same_author_name(author_name, str(candidate or ""))
            for candidate in series.get("authors") or []
        )
        if is_series_author:
            site_url = str(series.get("url") or "").strip()
            result = {
                "id": None,
                "name": author_name,
                "native_name": author_name,
                "alternative_names": [],
                "role": "Autor",
                "matched_title": str(series.get("title") or media_title).strip(),
                "image_url": "",
                "image_fallbacks": [],
                "description": "",
                "gender": "",
                "birth_date": "",
                "death_date": "",
                "age": None,
                "years_active": [],
                "home_town": "",
                "occupations": [],
                "language": "",
                "favourites": None,
                "status": "",
                "genres": [],
                "total_series": None,
                "blood_type": "",
                "official_site": "",
                "twitter": "",
                "facebook": "",
                "social_links": [],
                "site_url": "",
                "mangaupdates_url": site_url,
                "source_links": (
                    [{"label": "MangaUpdates (obra)", "url": site_url}]
                    if site_url else []
                ),
                "source": "MangaUpdates",
                "profile_limited": True,
            }
            mangaupdates_author_cache[cache_key] = CacheEntry(time.time(), result)
            return dict(result)
        mangaupdates_author_cache[cache_key] = CacheEntry(time.time(), {})
        raise RuntimeError(f"MangaUpdates nao encontrou autor: {author_name}")

    search_record = ranked[0][1].get("record") or {}
    author_id = search_record.get("id")
    if not author_id:
        raise RuntimeError(f"MangaUpdates retornou autor sem ID: {author_name}")
    detail = _mangaupdates_request("GET", f"/authors/{author_id}")

    image_url, image_fallbacks = _mangaupdates_image_urls(detail.get("image"))
    associated = [
        str(item.get("name") or "").strip()
        for item in detail.get("associated") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    status = str(detail.get("status") or "").strip()
    if status == "N/A":
        status = ""
    gender = str(detail.get("gender") or "").strip()
    if gender == "N/A":
        gender = ""
    social = detail.get("social") or {}
    site_url = str(detail.get("url") or search_record.get("url") or "").strip()
    result = {
        "id": author_id,
        "name": str(detail.get("name") or search_record.get("name") or author_name).strip(),
        "native_name": str(detail.get("actualname") or "").strip(),
        "alternative_names": list(dict.fromkeys(associated)),
        "role": "",
        "matched_title": media_title,
        "image_url": image_url,
        "image_fallbacks": image_fallbacks,
        "description": _clean_anilist_staff_text(detail.get("comments")),
        "gender": gender,
        "birth_date": _mangaupdates_date_text(detail.get("birthday")),
        "death_date": (
            _mangaupdates_date_text(detail.get("status_date"))
            if status == "Deceased"
            else ""
        ),
        "age": None,
        "years_active": [],
        "home_town": str(detail.get("birthplace") or "").strip(),
        "occupations": [],
        "language": "",
        "favourites": None,
        "status": status,
        "genres": detail.get("genres") or [],
        "total_series": (detail.get("stats") or {}).get("total_series"),
        "blood_type": "" if detail.get("bloodtype") == "N/A" else (detail.get("bloodtype") or ""),
        "official_site": str(social.get("officialsite") or "").strip(),
        "twitter": str(social.get("twitter") or "").strip(),
        "facebook": str(social.get("facebook") or "").strip(),
        "social_links": _author_social_links(social),
        "site_url": site_url,
        "mangaupdates_url": site_url,
        "source_links": [{"label": "MangaUpdates", "url": site_url}] if site_url else [],
        "source": "MangaUpdates",
    }
    mangaupdates_author_cache[cache_key] = CacheEntry(time.time(), result)
    return dict(result)


def _lookup_author_from_kitsu(name: str, title: str = "") -> dict:
    author_name = str(name or "").strip()
    media_title = str(title or "").strip()
    if not author_name:
        raise ValueError("Informe o nome do autor.")
    if not media_title:
        raise RuntimeError("Kitsu precisa do titulo da obra para localizar o autor.")

    metadata = _kitsu_metadata(media_title)
    ranked = sorted(
        (
            (
                1.0
                if _same_author_name(author_name, str(person.get("name") or ""))
                else fuzzy_match_score(author_name, str(person.get("name") or "")),
                person,
            )
            for person in metadata.get("staff") or []
            if str(person.get("name") or "").strip()
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.84:
        raise RuntimeError(f"Kitsu nao encontrou autor: {author_name}")

    person = ranked[0][1]
    site_url = str(person.get("site_url") or "").strip()
    return {
        "id": person.get("id"),
        "name": str(person.get("name") or author_name).strip(),
        "native_name": str(person.get("native_name") or "").strip(),
        "alternative_names": person.get("alternative_names") or [],
        "role": str(person.get("role") or "").strip(),
        "matched_title": media_title,
        "image_url": str(person.get("image_url") or "").strip(),
        "image_fallbacks": person.get("image_fallbacks") or [],
        "description": str(person.get("description") or "").strip(),
        "gender": "",
        "birth_date": "",
        "death_date": "",
        "age": None,
        "years_active": [],
        "home_town": "",
        "occupations": [],
        "language": "",
        "favourites": None,
        "status": "",
        "genres": [],
        "total_series": None,
        "blood_type": "",
        "official_site": "",
        "twitter": "",
        "facebook": "",
        "social_links": [],
        "site_url": site_url,
        "kitsu_url": site_url,
        "source_links": [{"label": "Kitsu", "url": site_url}] if site_url else [],
        "source": "Kitsu",
    }


def _merge_author_profiles(primary: dict, fallback: dict) -> dict:
    if not primary:
        return dict(fallback)
    if not fallback:
        return dict(primary)

    merged = dict(primary)
    used_fallback = False
    for key in (
        "native_name", "image_url", "description", "gender", "birth_date",
        "death_date", "age", "home_town", "language", "status", "total_series",
        "blood_type", "official_site", "twitter", "facebook",
    ):
        if merged.get(key) in (None, "", []):
            value = fallback.get(key)
            if value not in (None, "", []):
                merged[key] = value
                used_fallback = True

    for key in ("alternative_names", "image_fallbacks", "years_active", "occupations", "genres"):
        values = [
            value
            for value in [*(merged.get(key) or []), *(fallback.get(key) or [])]
            if value not in (None, "")
        ]
        deduped = list(dict.fromkeys(values))
        if deduped != (merged.get(key) or []):
            used_fallback = True
        merged[key] = deduped

    social_links: list[dict] = []
    seen_social_urls: set[str] = set()
    for link in [*(primary.get("social_links") or []), *(fallback.get("social_links") or [])]:
        url = str((link or {}).get("url") or "").strip()
        if not url or url in seen_social_urls:
            continue
        seen_social_urls.add(url)
        social_links.append({"label": str((link or {}).get("label") or "Rede"), "url": url})
    merged["social_links"] = social_links

    links: list[dict] = []
    seen_urls: set[str] = set()
    for link in [*(primary.get("source_links") or []), *(fallback.get("source_links") or [])]:
        url = str((link or {}).get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        links.append({"label": str((link or {}).get("label") or "Fonte"), "url": url})
    merged["source_links"] = links
    merged["mangaupdates_url"] = fallback.get("mangaupdates_url") or ""
    if used_fallback:
        merged["source"] = "AniList + MangaUpdates"
    return merged


def _lookup_author_profile(name: str, title: str = "", source_url: str = "") -> dict:
    author_name = str(name or "").strip()
    media_title = str(title or "").strip()
    media_source = str(source_url or "").strip()
    if not author_name:
        raise ValueError("Informe o nome do autor.")

    cache_key = (
        f"profile:v4:{_author_cache_key_text(author_name)}|"
        f"{normalize_match_text(media_title)}|{media_source}"
    )
    cached = author_profile_cache.get(cache_key)
    if _cache_is_fresh(cached, ANILIST_CACHE_TTL_SECONDS):
        return dict(cached.data)

    primary: dict = {}
    fallback: dict = {}
    anilist_error: Exception | None = None
    mangaupdates_error: Exception | None = None
    kitsu_error: Exception | None = None
    native_error: Exception | None = None
    try:
        primary = _lookup_author_from_anilist(author_name, media_title)
    except Exception as exc:
        anilist_error = exc

    needs_fallback = not primary or not primary.get("image_url") or not primary.get("description")
    if needs_fallback:
        try:
            fallback = _lookup_author_from_mangaupdates(author_name, media_title)
        except Exception as exc:
            mangaupdates_error = exc

    result = _merge_author_profiles(primary, fallback)
    if not result:
        try:
            result = _lookup_author_from_kitsu(author_name, media_title)
        except Exception as exc:
            kitsu_error = exc
    if not result and reader.fliptru_plugin.is_source(media_source):
        try:
            result = reader.fliptru_plugin.author_profile(author_name)
            result["matched_title"] = media_title
        except Exception as exc:
            native_error = exc
    if result:
        author_profile_cache[cache_key] = CacheEntry(time.time(), result)
        return dict(result)

    for error in (native_error, kitsu_error, mangaupdates_error, anilist_error):
        if isinstance(error, requests.RequestException):
            raise error
    raise RuntimeError(
        f"AniList, MangaUpdates, Kitsu e fonte original nao encontraram autor: {author_name}"
    )


def _apply_anilist_metadata(item: dict, metadata: dict) -> None:
    cover = str(metadata.get("poster") or "").strip()
    if cover and not item.get("cover_url"):
        item["cover_original_url"] = cover
        item["cover_original_fallbacks"] = [
            str(url).strip()
            for url in metadata.get("poster_fallbacks") or []
            if str(url or "").strip()
        ]
        item.update(_refresh_cover_fields(item))
    if metadata.get("average_score") and not _has_rating(item):
        item["rating"] = round(float(metadata["average_score"]) / 10, 1)
    if metadata.get("description") and not item.get("description"):
        item["description"] = metadata["description"]
    if metadata.get("description"):
        _add_desc_lang(item, "en", metadata["description"])
    if metadata.get("authors") and not item.get("authors"):
        item["authors"] = metadata["authors"]
    if metadata.get("status") and not item.get("status"):
        item["status"] = metadata["status"]
    if metadata.get("genres") and not item.get("genres"):
        item["genres"] = metadata["genres"]
    item["anilist_url"] = metadata.get("url") or item.get("anilist_url") or ""


def _has_rating(item: dict) -> bool:
    try:
        return float(item.get("rating")) > 0
    except (TypeError, ValueError):
        return False


def _kitsu_title_score(title: str, entry: dict) -> float:
    attrs = entry.get("attributes") or {}
    candidates = [
        attrs.get("canonicalTitle"),
        *(attrs.get("titles") or {}).values(),
        *(attrs.get("abbreviatedTitles") or []),
    ]
    query_norm = normalize_match_text(title)
    if any(query_norm and query_norm == normalize_match_text(str(value or "")) for value in candidates):
        return 1.0
    return max(
        (
            fuzzy_match_score(title, str(value or ""))
            for value in candidates
            if str(value or "").strip()
        ),
        default=0.0,
    )


def _kitsu_staff_from_payload(entry: dict, included: list[dict]) -> list[dict]:
    resources = {
        (str(resource.get("type") or ""), str(resource.get("id") or "")): resource
        for resource in included
        if isinstance(resource, dict)
    }
    staff: list[dict] = []
    for reference in ((entry.get("relationships") or {}).get("staff") or {}).get("data") or []:
        staff_record = resources.get(
            (str(reference.get("type") or "mediaStaff"), str(reference.get("id") or ""))
        ) or {}
        person_reference = (
            ((staff_record.get("relationships") or {}).get("person") or {}).get("data") or {}
        )
        person = resources.get(
            (str(person_reference.get("type") or "people"), str(person_reference.get("id") or ""))
        ) or {}
        attrs = person.get("attributes") or {}
        name = str(attrs.get("name") or "").strip()
        if not name:
            continue
        image = attrs.get("image") or {}
        image_url = str(
            image.get("large") or image.get("medium") or image.get("original") or ""
        ).strip()
        image_fallbacks = list(dict.fromkeys(
            str(url).strip()
            for url in [image.get("medium"), image.get("small"), image.get("original")]
            if str(url or "").strip() and str(url).strip() != image_url
        ))
        person_id = str(person.get("id") or "").strip()
        site_url = f"https://kitsu.app/people/{person_id}" if person_id else ""
        staff.append(
            {
                "id": person_id or None,
                "name": name,
                "native_name": "",
                "alternative_names": [],
                "role": str((staff_record.get("attributes") or {}).get("role") or "").strip(),
                "image_url": image_url,
                "image_fallbacks": image_fallbacks,
                "description": _clean_anilist_staff_text(attrs.get("description")),
                "site_url": site_url,
            }
        )
    return staff


def _kitsu_metadata(title: str) -> dict:
    key = normalize_match_text(title)
    cached = kitsu_cache.get(key)
    if _cache_is_fresh(cached, KITSU_CACHE_TTL_SECONDS):
        return dict(cached.data)
    data: dict = {}
    try:
        response = requests.get(
            "https://kitsu.app/api/edge/manga",
            params={"filter[text]": title, "page[limit]": 5, "include": "staff.person"},
            headers={"Accept": "application/vnd.api+json", "User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        entries = payload.get("data") or []
        ranked = sorted(
            ((_kitsu_title_score(title, entry), entry) for entry in entries),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if ranked and ranked[0][0] >= 0.72:
            entry = ranked[0][1]
            attrs = entry.get("attributes") or {}
            poster = attrs.get("posterImage") or {}
            avg = attrs.get("averageRating")
            try:
                rating = round(float(avg) / 10, 1) if avg else None  # kitsu 0-100 -> 0-10
            except (TypeError, ValueError):
                rating = None
            staff = _kitsu_staff_from_payload(entry, payload.get("included") or [])
            data = {
                "rating": rating,
                "description": str(attrs.get("synopsis") or attrs.get("description") or "").strip(),
                "poster": str(
                    poster.get("large") or poster.get("medium") or poster.get("original") or ""
                ).strip(),
                "poster_fallbacks": [
                    str(url).strip()
                    for url in [poster.get("medium"), poster.get("small"), poster.get("original")]
                    if str(url or "").strip()
                ],
                "authors": list(dict.fromkeys(
                    str(person.get("name") or "").strip()
                    for person in staff
                    if str(person.get("name") or "").strip()
                )),
                "staff": staff,
                "url": f"https://kitsu.app/manga/{entry.get('id')}" if entry.get("id") else "",
            }
    except Exception:
        data = {}
    kitsu_cache[key] = CacheEntry(time.time(), data)
    return dict(data)


def _apply_kitsu_metadata(item: dict, metadata: dict) -> None:
    cover = str(metadata.get("poster") or "").strip()
    if cover and not item.get("cover_url"):
        item["cover_original_url"] = cover
        item["cover_original_fallbacks"] = [
            str(url).strip()
            for url in metadata.get("poster_fallbacks") or []
            if str(url or "").strip()
        ]
        item.update(_refresh_cover_fields(item))
    rating = metadata.get("rating")
    if rating and not _has_rating(item):
        item["rating"] = rating
    if metadata.get("description") and not item.get("description"):
        item["description"] = metadata["description"]
    if metadata.get("description"):
        _add_desc_lang(item, "en", metadata["description"])
    if metadata.get("authors") and not item.get("authors"):
        item["authors"] = metadata["authors"]
    item["kitsu_url"] = metadata.get("url") or item.get("kitsu_url") or ""


def _mangaupdates_series_score(title: str, result: dict) -> float:
    record = result.get("record") or {}
    candidates = [record.get("title"), result.get("hit_title")]
    query_norm = normalize_match_text(title)
    for candidate in candidates:
        if query_norm and query_norm == normalize_match_text(str(candidate or "")):
            return 1.0
    return max(
        (fuzzy_match_score(title, str(candidate or "")) for candidate in candidates if str(candidate or "").strip()),
        default=0.0,
    )


def _mangaupdates_series_metadata(title: str) -> dict:
    clean_title = str(title or "").strip()
    key = normalize_match_text(clean_title)
    cached = mangaupdates_series_cache.get(key)
    if _cache_is_fresh(cached, MANGAUPDATES_CACHE_TTL_SECONDS):
        return dict(cached.data)
    if not clean_title:
        return {}

    search = _mangaupdates_request(
        "POST",
        "/series/search",
        {"search": clean_title, "stype": "title", "perpage": 5},
    )
    ranked = sorted(
        (
            (_mangaupdates_series_score(clean_title, result), result)
            for result in search.get("results") or []
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.84:
        mangaupdates_series_cache[key] = CacheEntry(time.time(), {})
        return {}

    record = ranked[0][1].get("record") or {}
    series_id = record.get("series_id")
    detail: dict = {}
    detail_complete = False
    if series_id:
        try:
            detail = _mangaupdates_request("GET", f"/series/{series_id}")
            detail_complete = True
        except Exception:
            detail = {}
    merged = {**record, **detail}

    image_url, image_fallbacks = _mangaupdates_image_urls(merged.get("image"))
    genres = [
        str(item.get("genre") if isinstance(item, dict) else item).strip()
        for item in merged.get("genres") or []
        if str(item.get("genre") if isinstance(item, dict) else item).strip()
    ]
    authors = [
        str(item.get("name") or "").strip()
        for item in merged.get("authors") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    alternative_titles = [
        str(item.get("title") or "").strip()
        for item in merged.get("associated") or []
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ]
    data = {
        "id": series_id,
        "title": str(merged.get("title") or clean_title).strip(),
        "url": str(merged.get("url") or record.get("url") or "").strip(),
        "description": _clean_anilist_staff_text(merged.get("description")),
        "poster": image_url,
        "poster_fallbacks": image_fallbacks,
        "rating": merged.get("bayesian_rating"),
        "genres": list(dict.fromkeys(genres)),
        "authors": list(dict.fromkeys(authors)),
        "status": str(merged.get("status") or "").strip(),
        "alternative_titles": list(dict.fromkeys(alternative_titles)),
        "type": str(merged.get("type") or "").strip(),
        "year": str(merged.get("year") or "").strip(),
        "source": "MangaUpdates",
    }
    saved_at = time.time()
    if not detail_complete:
        saved_at -= MANGAUPDATES_CACHE_TTL_SECONDS - (15 * 60)
    mangaupdates_series_cache[key] = CacheEntry(saved_at, data)
    return dict(data)


def _apply_mangaupdates_metadata(item: dict, metadata: dict) -> None:
    cover = str(metadata.get("poster") or "").strip()
    if cover and not item.get("cover_url"):
        item["cover_original_url"] = cover
        item["cover_original_fallbacks"] = [
            str(url).strip()
            for url in metadata.get("poster_fallbacks") or []
            if str(url or "").strip()
        ]
        item.update(_refresh_cover_fields(item))
    rating = metadata.get("rating")
    if rating and not _has_rating(item):
        item["rating"] = round(float(rating), 1)
    if metadata.get("description") and not item.get("description"):
        item["description"] = metadata["description"]
    if metadata.get("description"):
        _add_desc_lang(item, "en", metadata["description"])
    if metadata.get("authors") and not item.get("authors"):
        item["authors"] = metadata["authors"]
    if metadata.get("status") and not item.get("status"):
        item["status"] = metadata["status"]
    if metadata.get("genres") and not item.get("genres"):
        item["genres"] = metadata["genres"]
    if metadata.get("alternative_titles"):
        item["alternative_titles"] = list(dict.fromkeys([
            *(item.get("alternative_titles") or []),
            *metadata["alternative_titles"],
        ]))
    item["mangaupdates_url"] = metadata.get("url") or item.get("mangaupdates_url") or ""


def _complete_with_mangaupdates(item: dict, title: str) -> None:
    if not str(title or "").strip():
        return
    if not (
        not _has_rating(item)
        or not item.get("authors")
        or not item.get("description")
        or not item.get("cover_url")
        or not item.get("genres")
        or not item.get("status")
    ):
        return
    try:
        _apply_mangaupdates_metadata(item, _mangaupdates_series_metadata(title))
    except Exception:
        pass


def _complete_missing_authors(item: dict, title: str) -> None:
    clean_title = str(title or "").strip()
    if not clean_title or item.get("authors"):
        return
    try:
        _apply_anilist_metadata(item, _anilist_metadata(clean_title))
    except Exception:
        pass
    if item.get("authors"):
        return
    try:
        _apply_mangaupdates_metadata(item, _mangaupdates_series_metadata(clean_title))
    except Exception:
        pass
    if item.get("authors"):
        return
    try:
        _apply_kitsu_metadata(item, _kitsu_metadata(clean_title))
    except Exception:
        pass


def _kitsu_trending_raw(limit: int = 20) -> list[dict]:
    """Trending manga 'da semana' direto do Kitsu, com poster/rating/sinopse."""
    try:
        response = requests.get(
            "https://kitsu.app/api/edge/trending/manga",
            params={"page[limit]": min(max(limit, 1), 20)},
            headers={"Accept": "application/vnd.api+json", "User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        response.raise_for_status()
        entries = response.json().get("data") or []
    except Exception:
        return []
    out: list[dict] = []
    for entry in entries:
        attrs = entry.get("attributes") or {}
        title = str(attrs.get("canonicalTitle") or "").strip()
        if not title:
            continue
        poster = attrs.get("posterImage") or {}
        avg = attrs.get("averageRating")
        try:
            rating = round(float(avg) / 10, 1) if avg else None
        except (TypeError, ValueError):
            rating = None
        aliases = [str(v).strip() for v in (attrs.get("titles") or {}).values() if str(v or "").strip()]
        out.append(
            {
                "title": title,
                "aliases": aliases,
                "meta": {
                    "poster": str(
                        poster.get("large") or poster.get("medium") or poster.get("original") or ""
                    ).strip(),
                    "poster_fallbacks": [
                        str(u).strip()
                        for u in [poster.get("medium"), poster.get("small"), poster.get("original")]
                        if str(u or "").strip()
                    ],
                    "rating": rating,
                    "description": str(attrs.get("synopsis") or attrs.get("description") or "").strip(),
                    "url": f"https://kitsu.app/manga/{entry.get('id')}" if entry.get("id") else "",
                },
            }
        )
    return out


def _kitsu_trending_items(limit: int = 20) -> list[dict]:
    """Resolve cada trending do Kitsu a uma fonte legivel (MangaDex), mantendo a ordem do Kitsu."""
    raw = _kitsu_trending_raw(limit)
    if not raw:
        return []

    def resolve(entry: dict) -> dict | None:
        names = [entry["title"], *entry["aliases"][:3]]
        best: dict | None = None
        best_score = 0.0
        for name in names:
            try:
                payload = reader.search_mangadex(name, limit=5)
            except Exception:
                continue
            for raw_item in payload.get("results") or []:
                cand = _normalize_manga_item(raw_item, section="Em alta")
                if not cand:
                    continue
                score = max(
                    fuzzy_match_score(n, str(cand.get("title") or ""))
                    for n in names
                )
                if score > best_score:
                    best_score, best = score, cand
            if best_score >= 0.92:
                break
        if not best or best_score < 0.6:
            return None
        _apply_kitsu_metadata(best, entry["meta"])
        if entry["meta"].get("rating") and not best.get("rating"):
            best["rating"] = entry["meta"]["rating"]
        return best

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for it in executor.map(resolve, raw):
            if it:
                out.append(it)
    return _dedupe(out)[:limit]


def _kitsu_trending_titles(limit: int = 40) -> list[str]:
    titles: list[str] = []
    for entry in _kitsu_trending_raw(limit):
        titles.append(entry["title"])
        titles.extend(entry["aliases"])
    return titles


def _latest_release_sections() -> list[dict]:
    """Carrossel 'Recém-lançados' por fonte. Hoje: MangaDex (feed real de ultimo cap)."""
    sections: list[dict] = []
    try:
        payload = reader.latest_mangadex(limit=24, lang="")
    except Exception:
        payload = {}
    items: list[dict] = []
    for raw in payload.get("results") or []:
        item = _normalize_manga_item(raw, section="Recem-lancados")
        if item:
            items.append(item)
    items = _dedupe(items)[:20]
    if items:
        sections.append(
            {"title": "Recém-lançados · MangaDex", "items": items, "layout": "carousel"}
        )
    # mangasbrasuka / mangalivre: sem scraper de feed de lancamentos ainda (so busca).
    return sections


def _trending_highlights(catalog_items: list[dict], limit: int = 20) -> list[dict]:
    """Carrossel 'Em alta da semana': trending real do Kitsu resolvido a fontes legiveis.
    Kitsu primeiro (ordem do Kitsu), completa com AniList x catalogo. Nunca usa curated."""
    picked: list[dict] = []
    seen: set[str] = set()

    def add(item: dict) -> None:
        key = str(item.get("id") or item.get("source_url") or item.get("title"))
        if key and key not in seen:
            seen.add(key)
            picked.append(item)

    # 1) Kitsu trending da semana (fonte de verdade do carrossel)
    for item in _kitsu_trending_items(limit):
        add(item)

    # 2) completa com AniList trending cruzado com o catalogo ja carregado
    if len(picked) < limit:
        titles: list[str] = []
        try:
            titles += reader.anilist_trending_titles(40)
        except Exception:
            pass
        index: dict[str, dict] = {}
        for item in catalog_items:
            for name in [item.get("title"), *(item.get("alternative_titles") or [])]:
                norm = normalize_match_text(str(name or ""))
                if norm:
                    index.setdefault(norm, item)
        for title in titles:
            item = index.get(normalize_match_text(title))
            if item:
                add(item)
            if len(picked) >= limit:
                break

    # 3) fallback final: itens do catalogo COM capa, EXCETO curated ("Destaques")
    if len(picked) < 8:
        for item in catalog_items:
            if str(item.get("section") or "") == "Destaques":
                continue
            if not item.get("cover_url"):
                continue
            add(item)
            if len(picked) >= 12:
                break

    return picked[:limit]


def _fill_chapter_counts(items: list[dict], max_workers: int = 6, cap: int = 60) -> None:
    """Valida contagem pela mesma lista de capitulos aberta pelo leitor."""
    targets = [item for item in items if str(item.get("source_url") or "")][:cap]
    if not targets:
        return

    def fill(item: dict) -> None:
        try:
            source_url = str(item.get("source_url") or "")
            lang = _item_chapter_language(item)
            payload = _cached_chapters_payload(source_url, lang)
            if payload is None:
                payload = _resilient_list_chapters(source_url, lang)
                with _chapters_cache_lock:
                    chapters_cache[_chapters_cache_key(source_url, lang)] = CacheEntry(time.time(), dict(payload))
            _apply_verified_chapters(item, payload)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(fill, targets))


def _enrich_items_metadata(items: list[dict], max_workers: int = 6) -> None:
    """Preenche metadados: AniList, MangaUpdates e Kitsu como ultimo fallback."""
    candidates = [
        item for item in items
        if item.get("title")
        and (
            not _has_rating(item)
            or not item.get("authors")
            or not item.get("description")
            or not item.get("cover_url")
        )
    ]
    if not candidates:
        return

    def enrich(item: dict) -> None:
        title = str(item.get("title") or "")
        try:
            _apply_anilist_metadata(item, _anilist_metadata(title))
        except Exception:
            pass
        if (
            not _has_rating(item)
            or not item.get("authors")
            or not item.get("description")
            or not item.get("cover_url")
            or not item.get("genres")
            or not item.get("status")
        ):
            try:
                _apply_mangaupdates_metadata(item, _mangaupdates_series_metadata(title))
            except Exception:
                pass
        if (
            not _has_rating(item)
            or not item.get("authors")
            or not item.get("description")
            or not item.get("cover_url")
        ):
            try:
                _apply_kitsu_metadata(item, _kitsu_metadata(title))
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(enrich, candidates))


def _enrich_items_from_anilist(items: list[dict], max_workers: int = 4) -> None:
    candidates = [
        item for item in items
        if item.get("title")
        and (
            not item.get("rating")
            or not item.get("authors")
            or not item.get("description")
            or not item.get("cover_url")
        )
    ]
    if not candidates:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_anilist_metadata, str(item.get("title") or "")): item
            for item in candidates
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                _apply_anilist_metadata(item, future.result())
            except Exception:
                continue


def _fill_missing_cover_from_anilist(items: list[dict], limit: int = 4) -> None:
    _enrich_items_from_anilist(items[:limit], max_workers=4)


def _share_search_covers_by_title(items: list[dict]) -> None:
    covers_by_title: dict[str, dict] = {}
    for item in items:
        title_key = normalize_match_text(str(item.get("title") or ""))
        if title_key and _item_has_cover(item):
            covers_by_title.setdefault(title_key, item)
    for item in items:
        if _item_has_cover(item):
            continue
        title_key = normalize_match_text(str(item.get("title") or ""))
        source = covers_by_title.get(title_key)
        if source:
            _copy_cover_fields(item, source)


def _recover_missing_search_covers(items: list[dict], limit: int = SEARCH_COVER_RECOVERY_LIMIT) -> None:
    targets = [item for item in items if not _item_has_cover(item) and item.get("title")][:limit]
    if not targets:
        return

    def recover(item: dict) -> tuple[dict, str]:
        return item, _recover_cover_url(str(item.get("title") or ""))

    with ThreadPoolExecutor(max_workers=min(4, len(targets))) as executor:
        futures = [executor.submit(recover, item) for item in targets]
        for future in as_completed(futures):
            try:
                item, url = future.result()
            except Exception:
                continue
            if _is_remote_image_url(url):
                item["cover_original_url"] = url
                item.update(_refresh_cover_fields(item))


_PT_HINTS = (
    "ção", "ã", "õ", "á", "ç", "í", "ú", "ê", "ô",
    " não ", " que ", " uma ", " com ", " para ", " é ", " dos ", " das ",
    " ele ", " ela ", " você ", " mais ", " seu ", " sua ", " mas ", " são ",
)
_EN_HINTS = (
    " the ", " and ", " of ", " is ", " to ", " his ", " her ", " with ",
    " was ", " that ", " they ", " when ", " who ", " from ", " after ",
)


def _looks_english(text: str) -> bool:
    """Heuristica barata: provavelmente ingles (sem tracos PT, com stopwords EN)."""
    t = f" {str(text or '').lower()} "
    if len(t) < 12:
        return False
    if any(hint in t for hint in _PT_HINTS):
        return False
    return any(hint in t for hint in _EN_HINTS)


def _looks_portuguese(text: str) -> bool:
    t = f" {str(text or '').lower()} "
    return any(hint in t for hint in _PT_HINTS)


def _translate_to_pt(text: str) -> str:
    """Traduz QUALQUER idioma -> PT (sl=auto). Ja-PT ou falha -> texto original."""
    original = str(text or "").strip()
    if not original or _looks_portuguese(original):
        return original
    cached = translation_cache.get(original)
    if _cache_is_fresh(cached, TRANSLATION_CACHE_TTL_SECONDS):
        return str(cached.data.get("text") or original)
    translated = original
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "pt", "dt": "t", "q": original},
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        segments = response.json()[0] or []
        joined = "".join(seg[0] for seg in segments if seg and seg[0]).strip()
        translated = joined or original
    except Exception:
        translated = original
    translation_cache[original] = CacheEntry(time.time(), {"text": translated})
    return translated


def _add_desc_lang(item: dict, lang: str, text: str) -> None:
    text = _clean_synopsis(text)
    if not text:
        return
    item.setdefault("descriptions_map", {}).setdefault(lang, text)


def _clean_synopsis(value: object) -> str:
    """Remove markup e creditos soltos de fontes sem perder texto da sinopse."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)

    def markdown_link(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        # Links de rede social costumam ser apenas credito no fim da sinopse.
        if label.casefold() in {"twitter", "x", "instagram", "facebook", "discord"}:
            return ""
        return label

    text = re.sub(r"\[([^\]]+)\]\((?:https?://|www\.)[^)]*\)", markdown_link, text)
    text = re.sub(
        r"(?i)(?:\[?(?:twitter|instagram|facebook|discord|source)\]?\s*:?\s*)?https?://\S+",
        "",
        text,
    )
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _finalize_descriptions(item: dict) -> None:
    """Lista ordenada de sinopses (PT topo -> EN -> resto) + define description default."""
    raw = item.get("descriptions_map") or {}
    norm: dict[str, str] = {}
    for lang, text in raw.items():
        text = _clean_synopsis(text)
        if text:
            norm[str(lang or "").lower()] = text
    # sem map, mas tem description solta -> classifica por idioma
    if not norm and str(item.get("description") or "").strip():
        d = _clean_synopsis(item["description"])
        norm["pt-br" if _looks_portuguese(d) else "en"] = d

    pt = norm.get("pt-br") or norm.get("pt")
    en = norm.get("en")
    rest = sorted(k for k in norm if k not in ("pt-br", "pt", "en"))

    ordered: list[dict] = []
    if pt:
        ordered.append({"lang": "pt-br", "text": pt})
    elif en:
        ordered.append({"lang": "pt-br", "text": _translate_to_pt(en), "auto": True})
    elif rest:
        ordered.append({"lang": "pt-br", "text": _translate_to_pt(norm[rest[0]]), "auto": True})
    if en:
        ordered.append({"lang": "en", "text": en})
    for k in rest:
        ordered.append({"lang": k, "text": norm[k]})

    item.pop("descriptions_map", None)
    if ordered:
        item["descriptions"] = ordered
        item["description"] = ordered[0]["text"]


def _strip_descriptions_map(item: dict) -> None:
    raw = item.pop("descriptions_map", None)
    if item.get("descriptions"):
        for description in item["descriptions"]:
            if isinstance(description, dict):
                description["text"] = _clean_synopsis(description.get("text"))
        item["descriptions"] = [
            description
            for description in item["descriptions"]
            if isinstance(description, dict) and description.get("text")
        ]
        if item["descriptions"]:
            item["description"] = item["descriptions"][0]["text"]
        else:
            item["description"] = ""
        return
    if raw:
        ordered = []
        for lang, text in raw.items():
            text = _clean_synopsis(text)
            if text:
                ordered.append({"lang": str(lang or "").lower(), "text": text})
        if ordered:
            item["descriptions"] = ordered
    elif item.get("description"):
        d = _clean_synopsis(item["description"])
        item["description"] = d
        item["descriptions"] = [
            {"lang": "pt-br" if _looks_portuguese(d) else "en", "text": d}
        ]


def _finalize_payload_descriptions(data: dict, max_workers: int = 6, cap: int = 200) -> None:
    seen: set[int] = set()
    bucket: list[dict] = []

    def collect(item: dict) -> None:
        if not isinstance(item, dict) or id(item) in seen:
            return
        seen.add(id(item))
        bucket.append(item)

    for item in data.get("items") or []:
        collect(item)
    for section in data.get("sections") or []:
        for item in section.get("items") or []:
            collect(item)
    if not bucket:
        return
    head = bucket[:cap]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_finalize_descriptions, head))
    for item in bucket[cap:]:  # tail: sem traduzir, so normaliza/limpa
        _strip_descriptions_map(item)


def _image_referer(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "mangadex.org" in host:
        return "https://mangadex.org/"
    if "mugiverso.com" in host or "mangasbrasuka" in host:
        return "https://mangasbrasuka.com.br/"
    if "anilist.co" in host or "anilistcdn" in host:
        return "https://anilist.co/"
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def _sniff_image_media_type(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content[4:8] == b"ftyp" and content[8:12] in {b"avif", b"avis"}:
        return "image/avif"
    return ""


def _prune_image_cache() -> None:
    if len(image_cache) <= IMAGE_CACHE_MAX_ITEMS:
        return
    expired_before = time.time() - IMAGE_CACHE_TTL_SECONDS
    for key, entry in list(image_cache.items()):
        if entry.saved_at < expired_before:
            image_cache.pop(key, None)
    if len(image_cache) <= IMAGE_CACHE_MAX_ITEMS:
        return
    for key, _entry in sorted(image_cache.items(), key=lambda pair: pair[1].saved_at)[
        : len(image_cache) - IMAGE_CACHE_MAX_ITEMS
    ]:
        image_cache.pop(key, None)


def _fetch_image(url: str) -> ImageCacheEntry:
    allowed_ports = {80, 443} if settings.is_web else None
    requested_url = validate_public_http_url(url, allowed_ports=allowed_ports)
    while True:
        with _image_cache_lock:
            cached = image_cache.get(requested_url)
            if cached and time.time() - cached.saved_at < IMAGE_CACHE_TTL_SECONDS:
                return cached
            pending = image_inflight.get(requested_url)
            if pending is None:
                pending = threading.Event()
                image_inflight[requested_url] = pending
                break
        pending.wait(timeout=30)

    try:
        current_url = requested_url
        response = None
        content = b""
        for redirect_count in range(REMOTE_IMAGE_MAX_REDIRECTS + 1):
            headers = {
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
                "Referer": _image_referer(current_url),
            }
            if _is_mangadex_image_url(current_url):
                headers["Accept"] = "*/*"
                headers["User-Agent"] = "python-requests/2.32.5"

            response = _image_http.get(
                current_url,
                timeout=(5, 20),
                headers=headers,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "").strip()
                response.close()
                if not location or redirect_count >= REMOTE_IMAGE_MAX_REDIRECTS:
                    raise RuntimeError("Redirecionamento de imagem invalido.")
                current_url = validate_public_http_url(
                    urljoin(current_url, location),
                    allowed_ports=allowed_ports,
                )
                continue

            try:
                response.raise_for_status()
                content_length = response.headers.get("content-length", "").strip()
                if content_length.isdigit() and int(content_length) > REMOTE_IMAGE_MAX_BYTES:
                    raise RuntimeError("Imagem remota excede o limite de 25 MB.")
                chunks = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    chunks.extend(chunk)
                    if len(chunks) > REMOTE_IMAGE_MAX_BYTES:
                        raise RuntimeError("Imagem remota excede o limite de 25 MB.")
            finally:
                response.close()
            content = bytes(chunks)
            break

        if response is None or not content:
            raise RuntimeError("URL retornou imagem vazia.")
        media_type = _sniff_image_media_type(content)
        if not media_type:
            raise RuntimeError("URL nao retornou imagem raster valida.")

        entry = ImageCacheEntry(
            saved_at=time.time(),
            content=content,
            media_type=media_type,
        )
        with _image_cache_lock:
            image_cache[requested_url] = entry
            _prune_image_cache()
        return entry
    finally:
        with _image_cache_lock:
            finished = image_inflight.pop(requested_url, None)
            if finished is not None:
                finished.set()


def _cover_extension(media_type: str, url: str) -> str:
    mt = (media_type or "").split(";", 1)[0].strip().lower()
    by_mime = {
        "image/webp": ".webp", "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/png": ".png", "image/gif": ".gif", "image/avif": ".avif",
    }
    if mt in by_mime:
        return by_mime[mt]
    path = urlparse(url).path.lower()
    for ext in (".webp", ".jpg", ".jpeg", ".png", ".gif", ".avif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def _cover_file_exists(cover_path: str) -> bool:
    """True se o cover_path /static/... aponta para um arquivo que existe no disco."""
    cover_path = str(cover_path or "")
    if not cover_path.startswith("/static/"):
        return False
    try:
        return (STATIC_DIR / cover_path[len("/static/"):]).is_file()
    except Exception:
        return False


def _cover_key(item: dict) -> str:
    """Chave estavel p/ o nome do arquivo da capa (manga_id, fallback slug)."""
    raw = str(item.get("id") or "").strip() or str(item.get("slug") or "") or str(item.get("title") or "")
    return _slug(raw) or "cover"


def _store_cover_local(item: dict) -> None:
    """Baixa a capa 1x para static/covers/<manga_id>.<ext> e grava item['cover_path'].

    Reusa _fetch_image (Referer correto + cache em memoria). Idempotente: se o
    arquivo ja existe, so reusa o caminho.
    """
    src = str(item.get("cover_original_url") or "").strip() or _unproxy_image_url(item.get("cover_url") or "")
    if not _is_remote_image_url(src):
        return
    key = _cover_key(item)
    try:
        existing = next(COVERS_DIR.glob(f"{key}.*"), None)
        if existing and existing.stat().st_size > 0:
            item["cover_path"] = f"/static/covers/{existing.name}"
            return
        entry = _fetch_image(src)
        filename = f"{key}{_cover_extension(entry.media_type, src)}"
        (COVERS_DIR / filename).write_bytes(entry.content)
        item["cover_path"] = f"/static/covers/{filename}"
    except Exception:
        return  # falha de capa nao pode derrubar a raspagem


def _download_covers_to_disk(items: list[dict], limit: int = 80, max_workers: int = 8) -> None:
    targets = [
        it for it in items
        if str(it.get("cover_original_url") or it.get("cover_url") or "").strip()
    ][:limit]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_store_cover_local, targets))


def _mangadex_cover_url(title: str) -> str:
    """Capa via API MangaDex buscando por titulo (1o resultado)."""
    title = str(title or "").strip()
    if not title:
        return ""
    try:
        resp = requests.get(
            "https://api.mangadex.org/manga",
            params={
                "title": title,
                "limit": 1,
                "includes[]": "cover_art",
                "contentRating[]": ["safe", "suggestive", "erotica"],
                "order[relevance]": "desc",
            },
            timeout=15,
            headers={"User-Agent": "python-requests/2.32.5"},
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            return ""
        entry = data[0]
        manga_id = entry.get("id")
        file_name = next(
            (
                rel.get("attributes", {}).get("fileName")
                for rel in entry.get("relationships") or []
                if rel.get("type") == "cover_art" and rel.get("attributes")
            ),
            None,
        )
        if manga_id and file_name:
            return f"https://uploads.mangadex.org/covers/{manga_id}/{file_name}"
    except Exception:
        return ""
    return ""


def _recover_cover_url(title: str) -> str:
    """Tenta achar uma capa por TITULO: MangaDex -> AniList. '' se nada."""
    url = _mangadex_cover_url(title)
    if _is_remote_image_url(url):
        return url
    try:
        poster = str(_anilist_metadata(title).get("poster") or "").strip()
        return poster if _is_remote_image_url(poster) else ""
    except Exception:
        return ""


def _recover_and_store_cover(item: dict) -> bool:
    """Recupera a capa por titulo numa fonte alternativa e salva local.

    Retorna True se conseguiu (cover_path setado p/ arquivo existente).
    """
    url = _recover_cover_url(str(item.get("title") or ""))
    if not _is_remote_image_url(url):
        return False
    key = _cover_key(item)
    try:
        entry = _fetch_image(url)
        filename = f"{key}{_cover_extension(entry.media_type, url)}"
        (COVERS_DIR / filename).write_bytes(entry.content)
        item["cover_path"] = f"/static/covers/{filename}"
        item.setdefault("cover_original_url", url)
        return _cover_file_exists(item["cover_path"])
    except Exception:
        return False


def _chapter_audit_reader() -> MangaReader:
    reader_instance = getattr(_chapter_audit_local, "reader", None)
    if reader_instance is None:
        reader_instance = MangaReader(reader.args)
        _chapter_audit_local.reader = reader_instance
    return reader_instance


def _prewarm_chapters(items: list[dict], limit: int = 40, max_workers: int = 4) -> None:
    """Pre-busca a lista de capitulos das obras do catalogo (1x) e persiste em
    disco, para o PRIMEIRO clique do usuario ja vir do cache local (sem fetch).

    Cada fonte tem timeout proprio: uma fonte que trava vira falha registrada
    (card mostra "indisponivel" e reentra na fila depois) em vez de segurar o
    worker e travar o audit inteiro.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        source_url = str(item.get("source_url") or "").strip()
        if not source_url:
            continue
        lang = _item_chapter_language(item)
        grouped.setdefault((source_url, lang), []).append(item)
        if len(grouped) >= limit:
            break
    if not grouped:
        return

    def _record_failure(source_url: str, error: str) -> None:
        chapter_audit_failures[source_url] = CacheEntry(time.time(), {"error": error})

    def warm(target: tuple[str, str]) -> None:
        source_url, lang = target
        key = _chapters_cache_key(source_url, lang)
        with _chapters_cache_lock:
            cached = chapters_cache.get(key)
        if _cache_is_fresh(cached, CHAPTERS_DISK_TTL_SECONDS):
            payload = dict(cached.data)
        else:
            # Reader do thread externo (reusado pelo pool via thread-local); o
            # future aninhado so o executa com timeout. list_chapters segura
            # self.lock, mas so esta chamada o usa por vez.
            audit_reader = _chapter_audit_reader()
            fetch_executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = fetch_executor.submit(
                    scraper_coordinator.run,
                    _scraper_source_name(source_url),
                    f"audit-chapters:{source_url}:{lang}",
                    lambda: audit_reader.list_chapters(source_url, lang=lang),
                )
                payload = future.result(timeout=CHAPTER_AUDIT_TARGET_TIMEOUT_SECONDS)
            except FuturesTimeoutError:
                # Fetch travado segura o self.lock do reader; abandona esse reader
                # (a thread vazada morre com ele) e forca um novo no proximo target.
                _chapter_audit_local.reader = None
                _record_failure(source_url, "timeout")
                return
            except Exception as exc:  # noqa: BLE001 - rede/parse/fonte nao suportada
                _record_failure(source_url, str(exc))
                return
            finally:
                # wait=False: nao bloqueia esperando a thread travada terminar.
                fetch_executor.shutdown(wait=False)

        with _chapters_cache_lock:
            chapters_cache[key] = CacheEntry(time.time(), dict(payload))
        chapter_audit_failures.pop(source_url, None)
        for item in grouped[target]:
            _apply_verified_chapters(item, payload)

    executor = ThreadPoolExecutor(max_workers=max_workers)
    future_map = {executor.submit(warm, target): target for target in grouped}
    try:
        for _ in as_completed(future_map, timeout=CHAPTER_AUDIT_BATCH_TIMEOUT_SECONDS):
            pass
    except FuturesTimeoutError:
        # Teto do lote estourado: marca os pendentes como falha p/ o card sair de
        # "Verificando" e o proximo ciclo tentar de novo.
        for future, target in future_map.items():
            if not future.done():
                _record_failure(target[0], "batch-timeout")
    finally:
        executor.shutdown(wait=False)
    _save_chapters_snapshot()


def _schedule_home_chapter_audit(items: list[dict], *, priority: bool = False) -> bool:
    global _home_chapter_audit_running, _home_chapter_audit_pending

    targets: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        source_url = str(item.get("source_url") or "").strip()
        lang = _item_chapter_language(item)
        target_key = (source_url, lang)
        if not source_url or target_key in seen:
            continue
        seen.add(target_key)
        if _cached_chapters_payload(source_url, lang) is not None:
            continue
        failure = chapter_audit_failures.get(source_url)
        if _cache_is_fresh(failure, CHAPTER_AUDIT_FAILURE_TTL_SECONDS):
            continue
        targets.append(item)

    if not targets:
        with _home_chapter_audit_lock:
            return _home_chapter_audit_running

    with _home_chapter_audit_lock:
        queued = {
            f"{str(item.get('source_url') or '').strip()}|{_item_chapter_language(item)}": item
            for item in targets
        }
        if priority:
            # Mantem ordem de insercao: obras da sidebar/historico passam na frente
            # do preload amplo da home, sem cancelar trabalho ja em andamento.
            _home_chapter_audit_pending = {**queued, **_home_chapter_audit_pending}
        else:
            _home_chapter_audit_pending.update(queued)
        if _home_chapter_audit_running:
            return True
        _home_chapter_audit_running = True

    def run() -> None:
        global _home_chapter_audit_running
        while True:
            with _home_chapter_audit_lock:
                batch = list(_home_chapter_audit_pending.values())[:HOME_CHAPTER_AUDIT_BATCH_SIZE]
                for item in batch:
                    key = f"{str(item.get('source_url') or '').strip()}|{_item_chapter_language(item)}"
                    _home_chapter_audit_pending.pop(key, None)
                if not batch:
                    _home_chapter_audit_running = False
                    return
            # Mais paralelismo reduz tempo visivel; o timeout por fonte continua
            # isolando hosts lentos para nao prender a fila inteira.
            _prewarm_chapters(batch, limit=len(batch), max_workers=8)

    threading.Thread(target=run, daemon=True).start()
    return True


def _prefetch_cover_images(items: list[dict], limit: int = 48) -> None:
    urls: list[str] = []
    for item in items:
        for url in [
            item.get("cover_original_url"),
            *(item.get("cover_original_fallbacks") or []),
        ]:
            url = str(url or "").strip()
            if url and url not in urls and _is_remote_image_url(url):
                urls.append(url)
                break
        if len(urls) >= limit:
            break
    if not urls:
        return
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_fetch_image, url) for url in urls]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                continue


def _fill_visible_home_chapter_counts(data: dict, matches_genre, cap: int = 48) -> None:
    seen: set[str] = set()
    targets: list[dict] = []
    pools = [data.get("items") or []]
    pools.extend(section.get("items") or [] for section in data.get("sections") or [])
    for pool in pools:
        for item in pool:
            source_url = str(item.get("source_url") or "").strip()
            if not source_url or source_url in seen:
                continue
            seen.add(source_url)
            if not matches_genre(item) or not _is_home_ready(item):
                continue
            try:
                if int(item.get("chapter_count") or 0) > 0:
                    continue
            except (TypeError, ValueError):
                pass
            targets.append(item)
            if len(targets) >= cap:
                break
        if len(targets) >= cap:
            break
    _fill_chapter_counts(targets, max_workers=8, cap=cap)


def _search_mangas(query: str, limit: int) -> dict:
    normalized_query = normalize_match_text(query)
    if len(normalized_query) < 2:
        return {
            "items": [],
            "sections": [{"title": "Resultados", "items": []}],
            "total": 0,
            "limit": limit,
            "offset": 0,
            "sources": [],
            "errors": [],
            "cached": False,
        }

    cache_key = f"v{SEARCH_CACHE_VERSION}:{normalized_query}:{limit}"
    cached = search_cache.get(cache_key)
    if _cache_is_fresh(cached, SEARCH_CACHE_TTL_SECONDS):
        return {**cached.data, "cached": True}

    sources = _search_sources()
    source_limit = _search_source_limit(limit)
    # Sakura sai do batch rapido (browser nao cabe em 4s) e vira passe dedicado.
    sakura_live = "sakura" in sources
    fast_sources = [source for source in sources if source != "sakura"]
    items, errors = _search_sources_with_timeout(
        fast_sources,
        query,
        source_limit,
        timeout=SOURCE_SEARCH_TIMEOUT_SECONDS,
    )

    # Fontes externas geralmente exigem nome exato. Se primeira passada nao
    # achou match forte, tenta uma vez titulo proximo/prefixo sem abrir flood.
    first_best = max((_search_match_score(query, item) for item in items), default=0.0)
    if first_best < 0.9:
        fallback_term, catalog_match = _fuzzy_search_fallback_term(query)
        if fallback_term and normalize_match_text(fallback_term) != normalized_query:
            fallback_items, fallback_errors = _search_sources_with_timeout(
                fast_sources,
                fallback_term,
                source_limit,
                timeout=min(SOURCE_SEARCH_TIMEOUT_SECONDS, 3.0),
            )
            items.extend(fallback_items)
            errors.extend(fallback_errors)
        if catalog_match:
            items.append(catalog_match)

    # Requisicao Sakura EM TEMPO REAL: so quando as fontes rapidas nao acharam a
    # obra (nenhum match forte) -> caso "obra muito especifica que so tem no
    # Sakura". Evita pagar a latencia do browser em toda busca.
    if sakura_live:
        best_match = max((_search_match_score(query, it) for it in items), default=0.0)
        if best_match < 0.9:
            sakura_items, sakura_errors = _search_sources_with_timeout(
                ["sakura"],
                query,
                source_limit,
                timeout=SAKURA_LIVE_SEARCH_TIMEOUT_SECONDS,
            )
            items.extend(sakura_items)
            errors.extend(sakura_errors)

    items = _dedupe_search_results(items)
    items = _apply_curated_source_overrides(items, query)
    items = _dedupe_search_results(items)
    relevant_items = []
    for item in items:
        if not _title_contains_query_tokens(query, item):
            continue
        score = _search_match_score(query, item)
        tier = _search_rank_tier(query, item)
        if score > 0:
            item["relevance"] = round(score, 4)
            item["_search_tier"] = tier
            relevant_items.append(item)
    items = relevant_items
    items.sort(
        key=lambda item: (
            int(item.get("_search_tier", 9)),
            -float(item.get("relevance") or 0),
            -SOURCE_RELIABILITY.get(str(item.get("provider") or "").lower(), 0.5),
            -int(item.get("chapter_count") or 0),
            item["title"].lower(),
        )
    )
    for item in items:
        item.pop("_search_tier", None)
    _share_search_covers_by_title(items)
    _recover_missing_search_covers(items[:limit])
    data = {
        "items": items[:limit],
        "sections": [{"title": "Resultados", "items": items[:limit]}],
        "total": len(items),
        "limit": limit,
        "offset": 0,
        "sources": [_source_label(source) for source in sources],
        "errors": errors,
        "cached": False,
    }
    search_cache[cache_key] = CacheEntry(time.time(), data)
    return data


@app.get("/api/capabilities")
def capabilities() -> dict:
    return settings.public_capabilities()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def readiness(response: Response) -> dict[str, str]:
    if repositories.ready():
        return {"status": "ready"}
    response.status_code = 503
    return {"status": "not_ready"}


@app.post("/api/profiles")
def create_profile(request: ProfileCreateRequest) -> dict:
    _require_desktop_capability()
    display_name = request.display_name.strip() or "Leitor"
    now = time.time()
    profile = {
        "id": uuid4().hex,
        "display_name": display_name,
        "favorites": [],
        "library": [],
        "created_at": now,
        "updated_at": now,
    }
    with _profiles_lock:
        profile_repository.save(profile)
    return _profile_payload(profile)


@app.get("/api/profiles/{profile_id}")
def get_profile(
    profile_id: str,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        return _profile_payload(profile)


@app.put("/api/profiles/{profile_id}")
def update_profile(
    profile_id: str,
    request: ProfileUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    display_name = request.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="Nome do perfil vazio.")
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        profile["display_name"] = display_name
        profile["updated_at"] = time.time()
        profile_repository.save(profile)
    return _profile_payload(profile)


@app.put("/api/profiles/{profile_id}/favorites")
def update_profile_favorites(
    profile_id: str,
    request: ProfileFavoritesRequest,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    favorites: list[dict] = []
    seen: set[str] = set()
    for raw_item in request.favorites:
        item = _profile_favorite(raw_item)
        if not item:
            continue
        key = str(item.get("source_url") or item.get("id") or "")
        if key in seen:
            continue
        seen.add(key)
        favorites.append(item)

    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        profile["favorites"] = favorites
        profile["updated_at"] = time.time()
        profile_repository.save(profile)
    return _profile_payload(profile)


@app.put("/api/profiles/{profile_id}/library")
def upsert_profile_library_entry(
    profile_id: str,
    request: ProfileLibraryEntryRequest,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    entry = _profile_library_item(request.item, request.status, request.score, request.review)
    if not entry:
        raise HTTPException(status_code=422, detail="Obra invalida para biblioteca.")
    key = str(entry.get("source_url") or entry.get("id") or "")
    with _profiles_lock:
        remote_profile = copy.deepcopy(_profile_or_404(profile_id))
    anilist_sync = _save_library_entry_to_anilist(remote_profile, entry)
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        library = [item for item in (profile.get("library") or []) if isinstance(item, dict)]
        entry["updated_at"] = time.time()
        replaced = False
        for index, current in enumerate(library):
            current_key = str(current.get("source_url") or current.get("id") or "")
            if current_key == key:
                library[index] = entry
                replaced = True
                break
        if not replaced:
            library.insert(0, entry)
        profile["library"] = library[:500]
        profile["updated_at"] = time.time()
        profile_repository.save(profile)
    return {"profile": _profile_payload(profile), "anilist": anilist_sync}


@app.delete("/api/profiles/{profile_id}/library")
def delete_profile_library_entry(
    profile_id: str,
    request: ProfileLibraryDeleteRequest,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    requested_key = str(
        request.item.get("source_url") or request.item.get("id") or ""
    ).strip()
    if not requested_key:
        raise HTTPException(status_code=422, detail="Obra invalida para remover da lista.")
    with _profiles_lock:
        remote_profile = copy.deepcopy(_profile_or_404(profile_id))
        current = next(
            (
                item for item in (remote_profile.get("library") or [])
                if isinstance(item, dict)
                and str(item.get("source_url") or item.get("id") or "") == requested_key
            ),
            None,
        )
    if not current:
        raise HTTPException(status_code=404, detail="Obra nao esta na sua lista.")
    anilist_sync = _delete_library_entry_from_anilist(remote_profile, current)
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        profile["library"] = [
            item for item in (profile.get("library") or [])
            if not isinstance(item, dict)
            or str(item.get("source_url") or item.get("id") or "") != requested_key
        ]
        profile["updated_at"] = time.time()
        profile_repository.save(profile)
    return {"profile": _profile_payload(profile), "anilist": anilist_sync}


# ---------------------------------------------------------------------------
# Aparencia do perfil: avatar (foto) e background (capa do painel).
# ---------------------------------------------------------------------------
@app.put("/api/profiles/{profile_id}/avatar")
def set_profile_avatar(
    profile_id: str,
    request: ProfileImageRequest,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        profile["avatar_url"] = _apply_profile_image(profile, "avatar", request)
        profile["updated_at"] = time.time()
        profile_repository.save(profile)
    return _profile_payload(profile)


@app.put("/api/profiles/{profile_id}/background")
def set_profile_background(
    profile_id: str,
    request: ProfileImageRequest,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        profile["background_url"] = _apply_profile_image(profile, "background", request)
        profile["updated_at"] = time.time()
        profile_repository.save(profile)
    return _profile_payload(profile)


@app.put("/api/profiles/{profile_id}/home-background")
def set_profile_home_background(
    profile_id: str,
    request: ProfileImageRequest,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    """Imagem de fundo da HOME escolhida pelo leitor (customizacao de perfil)."""
    del current_user
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        profile["home_background_url"] = _apply_profile_image(profile, "home_background", request)
        profile["updated_at"] = time.time()
        profile_repository.save(profile)
    return _profile_payload(profile)


# ---------------------------------------------------------------------------
# Vinculo de contas externas via OAuth2 (AniList / MyAnimeList).
# ---------------------------------------------------------------------------
_oauth_states: dict[str, dict] = {}
_oauth_states_lock = threading.Lock()


def _purge_oauth_states() -> None:
    now = time.time()
    expired = [key for key, value in _oauth_states.items() if value.get("expires", 0) < now]
    for key in expired:
        _oauth_states.pop(key, None)


def _oauth_html(message: dict) -> Response:
    """Pagina retornada no callback: avisa a janela que abriu (postMessage) e fecha.
    Fallback: link de volta pro app se o popup nao tiver opener."""
    payload = json.dumps({"source": "kari-oauth", **message})
    frontend = json.dumps(FRONTEND_BASE_URL)
    body = f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>Kari - Vinculo de conta</title>
<style>body{{background:#0b0b0e;color:#e5e5e5;font-family:system-ui,sans-serif;
display:grid;place-items:center;height:100vh;margin:0}}a{{color:#6ee7b7}}</style></head>
<body><div><p>{html.escape(str(message.get('detail') or 'Pode fechar esta janela.'))}</p>
<p><a href={frontend}>Voltar ao Kari</a></p></div>
<script>
(function(){{
  var msg = {payload};
  try {{ if (window.opener) {{ window.opener.postMessage(msg, {frontend}); }} }} catch (e) {{}}
  setTimeout(function(){{ try {{ window.close(); }} catch (e) {{}} }}, 400);
}})();
</script></body></html>"""
    return Response(content=body, media_type="text/html")


def _fetch_anilist_viewer(access_token: str) -> dict:
    query = "query{Viewer{id name avatar{large medium} siteUrl}}"
    response = requests.post(
        ANILIST_GRAPHQL_URL,
        json={"query": query},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=15,
    )
    response.raise_for_status()
    viewer = (response.json().get("data") or {}).get("Viewer") or {}
    avatar = viewer.get("avatar") or {}
    return {
        "id": viewer.get("id"),
        "name": viewer.get("name") or "",
        "avatar": avatar.get("large") or avatar.get("medium") or "",
        "url": viewer.get("siteUrl") or "",
    }


def _fetch_mal_viewer(access_token: str) -> dict:
    response = requests.get(
        MAL_USER_URL,
        params={"fields": "id,name,picture"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    name = data.get("name") or ""
    return {
        "id": data.get("id"),
        "name": name,
        "avatar": data.get("picture") or "",
        "url": f"https://myanimelist.net/profile/{name}" if name else "",
    }


def _refresh_mal_access_token(tokens: dict) -> tuple[str, dict]:
    access_token = str(tokens.get("access_token") or "")
    obtained_at = float(tokens.get("obtained_at") or 0)
    expires_in = float(tokens.get("expires_in") or 0)
    if access_token and (not expires_in or obtained_at + expires_in - 60 > time.time()):
        return access_token, tokens

    refresh_token = str(tokens.get("refresh_token") or "")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Sessao MyAnimeList expirada. Vincule a conta novamente.")
    form = {
        "client_id": MAL_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if MAL_CLIENT_SECRET:
        form["client_secret"] = MAL_CLIENT_SECRET
    try:
        response = requests.post(MAL_TOKEN_URL, data=form, timeout=15)
        response.raise_for_status()
        refreshed = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Refresh MyAnimeList falhou error=%s", _safe_error(exc))
        raise HTTPException(status_code=502, detail="Nao consegui renovar acesso ao MyAnimeList.") from exc
    updated = {
        "access_token": str(refreshed.get("access_token") or ""),
        "refresh_token": str(refreshed.get("refresh_token") or refresh_token),
        "expires_in": refreshed.get("expires_in"),
        "obtained_at": time.time(),
    }
    if not updated["access_token"]:
        raise HTTPException(status_code=502, detail="MyAnimeList nao retornou novo token de acesso.")
    return updated["access_token"], updated


def _anilist_authenticated_graphql(access_token: str, query: str, variables: dict) -> dict:
    try:
        response = requests.post(
            ANILIST_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Nao consegui atualizar AniList.") from exc
    if payload.get("errors"):
        detail = str((payload.get("errors") or [{}])[0].get("message") or "AniList recusou atualizacao.")
        raise HTTPException(status_code=502, detail=detail)
    return payload.get("data") or {}


def _anilist_media_id_for_library_item(access_token: str, item: dict) -> int:
    external_id = str(item.get("external_id") or "")
    if str(item.get("external_provider") or "") == "anilist" and external_id.isdigit():
        return int(external_id)
    anilist_url = str(item.get("anilist_url") or "")
    matched = re.search(r"anilist\.co/manga/(\d+)", anilist_url)
    if matched:
        return int(matched.group(1))
    title = str(item.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Obra sem titulo para localizar no AniList.")
    data = _anilist_authenticated_graphql(
        access_token,
        "query($search:String!){Media(search:$search,type:MANGA){id}}",
        {"search": title},
    )
    media_id = ((data.get("Media") or {}).get("id"))
    if not media_id:
        raise HTTPException(status_code=404, detail="Obra nao encontrada no AniList.")
    return int(media_id)


def _anilist_linked_access_token(profile: dict) -> str:
    linked = (profile.get("links") or {}).get("anilist")
    tokens = (profile.get("_tokens") or {}).get("anilist")
    if not isinstance(linked, dict) or not isinstance(tokens, dict):
        return ""
    access_token = str(tokens.get("access_token") or "")
    expires_in = float(tokens.get("expires_in") or 0)
    obtained_at = float(tokens.get("obtained_at") or 0)
    if not access_token or (expires_in and obtained_at + expires_in <= time.time()):
        raise HTTPException(status_code=401, detail="Sessao AniList expirada. Vincule conta novamente.")
    return access_token


def _save_library_entry_to_anilist(profile: dict, entry: dict) -> dict:
    access_token = _anilist_linked_access_token(profile)
    if not access_token:
        return {"state": "not_linked"}
    media_id = _anilist_media_id_for_library_item(access_token, entry)
    variables: dict[str, object] = {
        "mediaId": media_id,
        "status": str(entry.get("status") or "COMPLETED").upper(),
        "notes": str(entry.get("review") or "")[:4000],
    }
    score = entry.get("score")
    if score is not None:
        # AniList aplica formato de nota configurado pelo usuario. Kari usa
        # 0-10, mesmo formato desta conta; enviar 70 fazia AniList limitar a 10.
        variables["score"] = float(score)
        mutation = """
        mutation($mediaId:Int!,$status:MediaListStatus,$score:Float,$notes:String){
          SaveMediaListEntry(mediaId:$mediaId,status:$status,score:$score,notes:$notes){id mediaId score}
        }
        """
    else:
        # Nota vazia significa "nao alterar nota externa", nunca zerar AniList.
        mutation = """
        mutation($mediaId:Int!,$status:MediaListStatus,$notes:String){
          SaveMediaListEntry(mediaId:$mediaId,status:$status,notes:$notes){id mediaId score}
        }
        """
    data = _anilist_authenticated_graphql(access_token, mutation, variables)
    saved_entry = data.get("SaveMediaListEntry") or {}
    entry["external_id"] = str(media_id)
    entry["external_provider"] = "anilist"
    if score is not None and saved_entry.get("score") is not None:
        entry["score"] = float(saved_entry["score"])
    return {"state": "updated", "media_id": media_id, "score": saved_entry.get("score")}


def _delete_library_entry_from_anilist(profile: dict, entry: dict) -> dict:
    access_token = _anilist_linked_access_token(profile)
    if not access_token:
        return {"state": "not_linked"}
    media_id = _anilist_media_id_for_library_item(access_token, entry)
    viewer_id = int(((profile.get("links") or {}).get("anilist") or {}).get("id") or 0)
    data = _anilist_authenticated_graphql(
        access_token,
        "query($mediaId:Int!,$userId:Int!){MediaList(mediaId:$mediaId,userId:$userId){id}}",
        {"mediaId": media_id, "userId": viewer_id},
    )
    list_id = ((data.get("MediaList") or {}).get("id"))
    if not list_id:
        return {"state": "not_found", "media_id": media_id}
    _anilist_authenticated_graphql(
        access_token,
        "mutation($id:Int!){DeleteMediaListEntry(id:$id){deleted}}",
        {"id": int(list_id)},
    )
    return {"state": "deleted", "media_id": media_id}


def _fetch_anilist_manga_list(access_token: str, user_id: object) -> list[dict]:
    query = """
    query($userId:Int!){
      MediaListCollection(userId:$userId,type:MANGA){
        lists{entries{status progress score notes media{id idMal siteUrl coverImage{large medium} title{romaji english native userPreferred}}}}
      }
    }
    """
    try:
        response = requests.post(
            ANILIST_GRAPHQL_URL,
            json={"query": query, "variables": {"userId": int(user_id)}},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("Lista AniList falhou error=%s", _safe_error(exc))
        raise HTTPException(status_code=502, detail="Nao consegui carregar lista do AniList.") from exc
    if payload.get("errors"):
        raise HTTPException(status_code=502, detail="AniList recusou consulta da lista.")
    lists = (((payload.get("data") or {}).get("MediaListCollection") or {}).get("lists") or [])
    entries: list[dict] = []
    seen_media_ids: set[str] = set()
    for media_list in lists:
        if not isinstance(media_list, dict):
            continue
        for entry in media_list.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            media = entry.get("media") or {}
            media_id = str(media.get("id") or "")
            if media_id and media_id in seen_media_ids:
                continue
            if media_id:
                seen_media_ids.add(media_id)
            titles = media.get("title") or {}
            entries.append({
                "id": media.get("id"),
                "status": str(entry.get("status") or ""),
                "progress": entry.get("progress"),
                "score": entry.get("score"),
                "review": entry.get("notes") or "",
                "provider": "anilist",
                "site_url": media.get("siteUrl") or "",
                "cover_url": ((media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""),
                "titles": [titles.get(key) for key in ("userPreferred", "english", "romaji", "native")],
            })
    return entries


def _fetch_mal_manga_list(access_token: str) -> list[dict]:
    try:
        response = requests.get(
            MAL_MANGA_LIST_URL,
            params={
                "fields": "alternative_titles,list_status",
                "limit": 1000,
                "nsfw": "true",
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Lista MyAnimeList falhou error=%s", _safe_error(exc))
        raise HTTPException(status_code=502, detail="Nao consegui carregar lista do MyAnimeList.") from exc
    entries: list[dict] = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        status = str((row.get("list_status") or {}).get("status") or "")
        if status == "dropped":
            continue
        node = row.get("node") or {}
        alternatives = node.get("alternative_titles") or {}
        synonyms = alternatives.get("synonyms") or []
        entries.append({
            "id": node.get("id"),
            "status": status,
            "progress": (row.get("list_status") or {}).get("num_chapters_read"),
            "score": (row.get("list_status") or {}).get("score"),
            "review": (row.get("list_status") or {}).get("comments") or "",
            "provider": "myanimelist",
            "titles": [node.get("title"), alternatives.get("en"), alternatives.get("ja"), *synonyms],
        })
    return entries


def _sync_catalog_title_index() -> dict[str, dict]:
    data = catalog_cache.data if catalog_cache else (_read_catalog_snapshot() or {})
    pools = [data.get("items") or [], _hq_catalog_items(), _light_novel_catalog_items()]
    pools.extend(section.get("items") or [] for section in data.get("sections") or [])
    index: dict[str, dict] = {}
    for pool in pools:
        for item in pool:
            if not isinstance(item, dict) or not _is_home_ready(item):
                continue
            candidates: list[object] = [item.get("title"), item.get("original_title")]
            for key in ("titles", "alternative_titles", "alt_titles", "synonyms"):
                value = item.get(key)
                if isinstance(value, dict):
                    candidates.extend(value.values())
                elif isinstance(value, list):
                    candidates.extend(value)
            for title in candidates:
                identity = _canonical_title_identity(str(title or ""))
                if identity and identity not in index:
                    index[identity] = item
    return index


def _match_external_list(entries: list[dict]) -> list[dict]:
    index = _sync_catalog_title_index()
    matched: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        item = None
        for title in entry.get("titles") or []:
            identity = _canonical_title_identity(str(title or ""))
            if identity and identity in index:
                item = index[identity]
                break
        if not item:
            continue
        key = str(item.get("source_url") or _canonical_title_identity(str(item.get("title") or "")))
        if not key or key in seen:
            continue
        seen.add(key)
        matched.append(item)
    return matched


def _external_searchable_titles(entry: dict) -> list[str]:
    """Keep AniList aliases that our Latin-only matcher can compare safely."""
    titles: list[str] = []
    for raw_title in entry.get("titles") or []:
        title = str(raw_title or "").strip()
        # ``normalize_match_text`` intentionally removes CJK. Passing a title
        # containing them can leave only a generic English fragment, such as
        # "the lost canvas", which incorrectly wins against a full title.
        if (
            title
            and not re.search(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]", title)
            and normalize_match_text(title)
        ):
            titles.append(title)
    return list(dict.fromkeys(titles))


def _resolve_external_list_items(entries: list[dict]) -> list[tuple[dict, dict]]:
    """Casa lista externa com cache local e busca fontes apenas durante Sync.

    Catalogo da home e propositalmente limitado; usar somente ele fazia uma lista
    AniList inteira virar 1/17. Aqui, itens ausentes recebem busca curta nas
    fontes Kari. Esse trabalho ocorre so apos clique explicito em Sincronizar.
    """
    index = _sync_catalog_title_index()
    resolved: list[tuple[dict, dict]] = []
    missing: list[dict] = []
    seen_external: set[str] = set()

    for entry in entries:
        item = None
        searchable_titles = _external_searchable_titles(entry)
        for title in searchable_titles:
            identity = _canonical_title_identity(str(title or ""))
            if identity and identity in index:
                item = index[identity]
                break
        external_key = str(entry.get("id") or next((title for title in entry.get("titles") or [] if title), ""))
        if not external_key or external_key in seen_external:
            continue
        seen_external.add(external_key)
        if item:
            override = next(
                (
                    SYNC_TITLE_SOURCE_OVERRIDES[identity]
                    for title in searchable_titles
                    if (identity := _canonical_title_identity(str(title or ""))) in SYNC_TITLE_SOURCE_OVERRIDES
                ),
                None,
            )
            if override:
                preferred_title = next(iter(searchable_titles), str(item.get("title") or ""))
                item = {**item, "title": preferred_title, **override}
            resolved.append((item, entry))
        else:
            missing.append(entry)

    def find(entry: dict) -> tuple[dict, dict] | None:
        original_titles = [str(title).strip() for title in (entry.get("titles") or []) if str(title or "").strip()]
        titles = _external_searchable_titles(entry)
        override = next(
            (
                SYNC_TITLE_SOURCE_OVERRIDES[identity]
                for title in titles
                if (identity := _canonical_title_identity(title)) in SYNC_TITLE_SOURCE_OVERRIDES
            ),
            None,
        )
        if override:
            source_url = str(override.get("source_url") or "")
            return {
                "id": source_url.rsplit("/", 1)[-1],
                "title": titles[0] if titles else (original_titles[0] if original_titles else ""),
                "source": str(override.get("source") or ""),
                "source_url": source_url,
                "cover_url": str(entry.get("cover_url") or ""),
                "chapter_status": "pending",
                **override,
            }, entry
        queries: list[str] = []
        for title in titles[:3]:
            queries.append(title)
            # AniList costuma guardar subtitulo japonês longo; busca base encontra
            # a obra da mesma série nas fontes sem relaxar match final.
            if " - " in title:
                queries.append(title.split(" - ", 1)[0].strip())
            normalized_title = normalize_match_text(title)
            first_token = normalized_title.split(maxsplit=1)[0] if normalized_title else ""
            if len(first_token) >= 4:
                queries.append(first_token)
        best_item: dict | None = None
        best_score = 0.0
        for query in dict.fromkeys(queries):
            try:
                candidates = _search_mangas(query, limit=8).get("items") or []
            except Exception as exc:  # noqa: BLE001 - fonte instavel nao aborta sync inteiro
                logger.info("Sync search falhou error=%s", _safe_error(exc))
                continue
            best = max(
                candidates,
                key=lambda candidate: max((_search_match_score(title, candidate) for title in titles), default=0),
                default=None,
            )
            score = max((_search_match_score(title, best) for title in titles), default=0) if best else 0
            if best and score > best_score and str(best.get("source_url") or "").strip():
                best_item = best
                best_score = score

        # A broad alias may find a related manga first (Gantz:E for GANTZ:G).
        # Score all title variants before committing to a source.
        if best_item and best_score >= 0.82:
            return best_item, entry

        # Sem fonte ativa nao pode desaparecer da lista importada. Fica visivel
        # como AniList; quando fonte entrar no Kari, proximo Sync troca o item.
        if original_titles:
            return {
                "id": f"anilist:{entry.get('id')}",
                "title": original_titles[0],
                "source": "AniList (sem fonte no Kari)",
                "source_url": "",
                "cover_url": str(entry.get("cover_url") or ""),
                "chapter_status": "unavailable",
                "anilist_url": str(entry.get("site_url") or ""),
            }, entry
        return None

    # Nao dispara dezenas de scrapers simultaneos; cada busca ja consulta fontes
    # em paralelo. Tres workers mantem Sync rapido sem degradar servidor.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(find, entry) for entry in missing]
        for future in as_completed(futures):
            try:
                match = future.result()
            except Exception:  # noqa: BLE001
                match = None
            if match:
                resolved.append(match)

    deduped: list[tuple[dict, dict]] = []
    seen_items: set[str] = set()
    for item, entry in resolved:
        key = (
            f"external:{entry.get('provider')}:{entry.get('id')}"
            if entry.get("provider") and entry.get("id")
            else str(item.get("source_url") or _canonical_title_identity(str(item.get("title") or "")))
        )
        if key and key not in seen_items:
            seen_items.add(key)
            deduped.append((item, entry))
    return deduped


@app.post("/api/profiles/{profile_id}/link/{provider}")
def start_account_link(
    profile_id: str,
    provider: str,
    request: Request,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    _enforce_rate_limit(
        request,
        "oauth-link",
        OAUTH_RATE_LIMIT,
        user_id=profile_id,
        resource=provider,
    )
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail="Provedor desconhecido.")
    if not _oauth_provider_configured(provider):
        raise HTTPException(
            status_code=503,
            detail=f"OAuth do {PROVIDER_LABELS.get(provider, provider)} nao configurado no servidor (.env).",
        )
    with _profiles_lock:
        _profile_or_404(profile_id)

    state = secrets.token_urlsafe(24)
    redirect_uri = _oauth_redirect_uri(provider)
    entry = {
        "profile_id": profile_id,
        "provider": provider,
        "expires": time.time() + OAUTH_STATE_TTL_SECONDS,
    }

    if provider == "anilist":
        params = {
            "client_id": ANILIST_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        authorize_url = f"{ANILIST_AUTHORIZE_URL}?{urlencode(params)}"
    else:  # myanimelist (PKCE plain: code_challenge == code_verifier)
        verifier = secrets.token_urlsafe(64)[:128]
        entry["code_verifier"] = verifier
        params = {
            "response_type": "code",
            "client_id": MAL_CLIENT_ID,
            "state": state,
            "code_challenge": verifier,
            "code_challenge_method": "plain",
            "redirect_uri": redirect_uri,
        }
        authorize_url = f"{MAL_AUTHORIZE_URL}?{urlencode(params)}"

    with _oauth_states_lock:
        _purge_oauth_states()
        _oauth_states[state] = entry
    return {"authorize_url": authorize_url}


@app.get("/api/oauth/{provider}/callback")
def account_link_callback(
    provider: str,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> Response:
    if provider not in OAUTH_PROVIDERS:
        return _oauth_html({"ok": False, "detail": "Provedor desconhecido."})
    if error:
        return _oauth_html({"ok": False, "provider": provider, "detail": f"Autorizacao negada: {error}"})

    with _oauth_states_lock:
        _purge_oauth_states()
        entry = _oauth_states.pop(state, None)
    if not entry or entry.get("provider") != provider:
        return _oauth_html({"ok": False, "provider": provider, "detail": "Sessao OAuth invalida ou expirada."})
    if not code:
        return _oauth_html({"ok": False, "provider": provider, "detail": "Codigo de autorizacao ausente."})

    profile_id = entry["profile_id"]
    redirect_uri = _oauth_redirect_uri(provider)
    try:
        if provider == "anilist":
            token_response = requests.post(
                ANILIST_TOKEN_URL,
                json={
                    "grant_type": "authorization_code",
                    "client_id": ANILIST_CLIENT_ID,
                    "client_secret": ANILIST_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=15,
            )
            token_response.raise_for_status()
            tokens = token_response.json()
            viewer = _fetch_anilist_viewer(tokens.get("access_token", ""))
        else:
            form = {
                "client_id": MAL_CLIENT_ID,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": entry.get("code_verifier", ""),
            }
            if MAL_CLIENT_SECRET:
                form["client_secret"] = MAL_CLIENT_SECRET
            token_response = requests.post(MAL_TOKEN_URL, data=form, timeout=15)
            token_response.raise_for_status()
            tokens = token_response.json()
            viewer = _fetch_mal_viewer(tokens.get("access_token", ""))
    except requests.RequestException as exc:
        logger.warning("OAuth provider=%s falhou error=%s", provider, _safe_error(exc))
        return _oauth_html({"ok": False, "provider": provider, "detail": "Falha ao trocar o codigo por token."})

    link_info = {**viewer, "linked_at": time.time()}
    with _profiles_lock:
        profile = profile_repository.get(profile_id)
        if not isinstance(profile, dict):
            return _oauth_html({"ok": False, "provider": provider, "detail": "Perfil nao encontrado."})
        links = profile.get("links")
        if not isinstance(links, dict):
            links = {}
        links[provider] = link_info
        profile["links"] = links
        stored_tokens = profile.get("_tokens")
        if not isinstance(stored_tokens, dict):
            stored_tokens = {}
        stored_tokens[provider] = {
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_in": tokens.get("expires_in"),
            "obtained_at": time.time(),
        }
        profile["_tokens"] = stored_tokens
        profile["updated_at"] = time.time()
        profile_repository.save(profile)

    return _oauth_html({
        "ok": True,
        "provider": provider,
        "name": link_info.get("name") or "",
        "detail": f"Conta {PROVIDER_LABELS.get(provider, provider)} vinculada!",
    })


@app.delete("/api/profiles/{profile_id}/link/{provider}")
def unlink_account(
    profile_id: str,
    provider: str,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail="Provedor desconhecido.")
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        links = profile.get("links")
        if isinstance(links, dict):
            links.pop(provider, None)
        tokens = profile.get("_tokens")
        if isinstance(tokens, dict):
            tokens.pop(provider, None)
        profile["updated_at"] = time.time()
        profile_repository.save(profile)
    return _profile_payload(profile)


@app.get("/api/profiles/{profile_id}/link/status")
def account_link_status(
    profile_id: str,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    """Diz quais provedores estao CONFIGURADOS no servidor (habilita botoes no UI)."""
    del current_user
    return {
        "providers": {
            provider: {
                "configured": _oauth_provider_configured(provider),
                "label": PROVIDER_LABELS.get(provider, provider),
            }
            for provider in OAUTH_PROVIDERS
        }
    }


@app.post("/api/profiles/{profile_id}/sync/{provider}")
def sync_linked_manga_list(
    profile_id: str,
    provider: str,
    request: Request,
    current_user: AuthenticatedUser = Depends(require_profile_owner),
) -> dict:
    del current_user
    _enforce_rate_limit(
        request,
        "oauth-sync",
        OAUTH_RATE_LIMIT,
        user_id=profile_id,
        resource=provider,
    )
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail="Provedor desconhecido.")
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        link = copy.deepcopy((profile.get("links") or {}).get(provider))
        tokens = copy.deepcopy((profile.get("_tokens") or {}).get(provider))
    if not isinstance(link, dict) or not isinstance(tokens, dict):
        raise HTTPException(status_code=409, detail=f"Vincule conta {PROVIDER_LABELS[provider]} primeiro.")

    refreshed_tokens = tokens
    if provider == "myanimelist":
        access_token, refreshed_tokens = _refresh_mal_access_token(tokens)
        external_entries = _fetch_mal_manga_list(access_token)
    else:
        access_token = str(tokens.get("access_token") or "")
        expires_in = float(tokens.get("expires_in") or 0)
        obtained_at = float(tokens.get("obtained_at") or 0)
        if not access_token or (expires_in and obtained_at + expires_in <= time.time()):
            raise HTTPException(status_code=401, detail="Sessao AniList expirada. Vincule a conta novamente.")
        external_entries = _fetch_anilist_manga_list(access_token, link.get("id"))

    matched_pairs = _resolve_external_list_items(external_entries)
    now = time.time()
    with _profiles_lock:
        profile = _profile_or_404(profile_id)
        library = [item for item in (profile.get("library") or []) if isinstance(item, dict)]
        seen = {
            str(item.get("source_url") or _canonical_title_identity(str(item.get("title") or "")))
            for item in library
        }
        added = 0
        for item, external in matched_pairs:
            saved = _profile_library_item(
                item,
                str(external.get("status") or "PLANNING"),
                external.get("score"),
                external.get("review"),
                external,
            )
            if not saved:
                continue
            key = str(saved.get("source_url") or _canonical_title_identity(saved["title"]))
            if not key:
                continue
            saved_title = _canonical_title_identity(str(saved.get("title") or ""))
            incoming_external_id = str(saved.get("external_id") or "")

            def is_same_library_entry(current: dict) -> bool:
                current_provider = str(current.get("external_provider") or "")
                current_external_id = str(current.get("external_id") or "")
                if incoming_external_id and current_provider == provider and current_external_id:
                    return current_external_id == incoming_external_id
                current_key = str(
                    current.get("source_url")
                    or _canonical_title_identity(str(current.get("title") or ""))
                )
                return (
                    current_key == key
                    or _canonical_title_identity(str(current.get("title") or "")) == saved_title
                )

            existing_index = next(
                (
                    i for i, current in enumerate(library)
                    if is_same_library_entry(current)
                ),
                None,
            )
            if existing_index is None:
                seen.add(key)
                library.append(saved)
                added += 1
            else:
                previous = library[existing_index]
                # Resenha escrita no Kari vence campo de notes importado do AniList.
                if str(previous.get("review") or "").strip() and previous.get("external_provider") != provider:
                    saved["review"] = previous["review"]
                library[existing_index] = saved
        # Syncs antigos podiam casar uma obra errada por título aproximado e,
        # depois, adicionar a correta como segunda entrada. ID externo identifica
        # obra de forma estável; mantém somente item mais novo daquele ID.
        deduped_library: list[dict] = []
        seen_library: set[str] = set()
        for current in library:
            external_provider = str(current.get("external_provider") or "")
            external_id = str(current.get("external_id") or "")
            identity = (
                f"external:{external_provider}:{external_id}"
                if external_provider and external_id
                else str(current.get("source_url") or _canonical_title_identity(str(current.get("title") or "")))
            )
            if not identity or identity in seen_library:
                continue
            seen_library.add(identity)
            deduped_library.append(current)
        profile["library"] = deduped_library[:500]
        links = profile.setdefault("links", {})
        current_link = links.setdefault(provider, link)
        current_link.update({
            "synced_at": now,
            "list_count": len(external_entries),
            "matched_count": len(matched_pairs),
        })
        profile.setdefault("_tokens", {})[provider] = refreshed_tokens
        profile["updated_at"] = now
        profile_repository.save(profile)

    return {
        "profile": _profile_payload(profile),
        "sync": {
            "provider": provider,
            "list_count": len(external_entries),
            "matched_count": len(matched_pairs),
            "added_count": added,
            "synced_at": now,
        },
    }


@app.get("/api/backgrounds")
def list_preset_backgrounds() -> dict:
    """Backgrounds pre-definidos disponiveis (static/backgrounds/*)."""
    items: list[dict] = []
    for path in sorted(BACKGROUNDS_DIR.glob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in BACKGROUND_VIDEO_EXTS:
            kind = "video"
        elif ext in BACKGROUND_IMAGE_EXTS:
            kind = "image"
        else:
            continue
        items.append({
            "name": path.stem,
            "url": f"/static/backgrounds/{path.name}",
            "kind": kind,
        })
    return {"backgrounds": items}


# ---------------------------------------------------------------------------
# Cadastro / Login (contas locais). Senha com PBKDF2; sessao via token Bearer.
# Auth local: persistencia JSON mantida para compatibilidade desktop/dev.
# ---------------------------------------------------------------------------
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,32}$")


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()


def _argon2_hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def _verify_password_and_rehash(login_key: str, user: dict, password: str) -> bool:
    password_hash = str(user.get("password_hash") or "")
    algorithm = str(user.get("password_algorithm") or "").lower()
    if password_hash.startswith("$argon2id$") or algorithm == "argon2id":
        try:
            valid = _password_hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError):
            return False
        if valid and _password_hasher.check_needs_rehash(password_hash):
            updated = dict(user)
            updated["password_hash"] = _argon2_hash_password(password)
            updated["password_algorithm"] = "argon2id"
            updated.pop("salt", None)
            user_repository.save(login_key, updated)
        return bool(valid)

    salt = str(user.get("salt") or "")
    try:
        candidate = _hash_password(password, salt)
    except (ValueError, TypeError):
        return False
    if not secrets.compare_digest(candidate, password_hash):
        return False
    updated = dict(user)
    updated["password_hash"] = _argon2_hash_password(password)
    updated["password_algorithm"] = "argon2id"
    updated.pop("salt", None)
    user_repository.save(login_key, updated)
    return True


def _issue_token(profile_id: str, username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _tokens_lock:
        session_repository.purge_expired(now)
        session_repository.save(
            token,
            {
                "profile_id": profile_id,
                "username": username,
                "expires": now + settings.session_ttl_seconds,
                "created_at": now,
            },
        )
    return token




class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)
    email: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@app.post("/api/auth/register")
def auth_register(request: RegisterRequest, http_request: Request) -> dict:
    username = request.username.strip()
    _enforce_rate_limit(
        http_request,
        "auth-register",
        REGISTER_RATE_LIMIT,
        resource=username.lower(),
    )
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=422, detail="Usuario invalido (3-32: letras, numeros, _ ou .).")
    key = username.lower()
    now = time.time()
    with _users_lock:
        if user_repository.get(key) is not None:
            raise HTTPException(status_code=409, detail="Esse usuario ja existe.")
        profile = {
            "id": uuid4().hex,
            "display_name": username,
            "favorites": [],
            "created_at": now,
            "updated_at": now,
        }
        with _profiles_lock:
            profile_repository.save(profile)
        user_repository.save(key, {
            "username": username,
            "email": request.email.strip(),
            "password_hash": _argon2_hash_password(request.password),
            "password_algorithm": "argon2id",
            "profile_id": profile["id"],
            "created_at": now,
        })
    token = _issue_token(profile["id"], username)
    return {"token": token, "profile": _profile_payload(profile)}


@app.post("/api/auth/login")
def auth_login(request: LoginRequest, http_request: Request) -> dict:
    key = request.username.strip().lower()
    _enforce_rate_limit(
        http_request,
        "auth-login",
        LOGIN_RATE_LIMIT,
        resource=key,
    )
    with _users_lock:
        user = user_repository.get(key)
        password_valid = bool(
            user and _verify_password_and_rehash(key, user, request.password)
        )
    if not password_valid:
        raise HTTPException(status_code=401, detail="Usuario ou senha invalidos.")
    with _profiles_lock:
        profile = profile_repository.get(user["profile_id"])
    if not isinstance(profile, dict):
        raise HTTPException(status_code=500, detail="Perfil da conta nao encontrado.")
    token = _issue_token(user["profile_id"], user["username"])
    return {"token": token, "profile": _profile_payload(profile)}


@app.get("/api/auth/me")
def auth_me(
    current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict:
    with _profiles_lock:
        profile = profile_repository.get(current_user.profile_id)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=401, detail="Sessao invalida.")
    return {"profile": _profile_payload(profile)}


@app.post("/api/auth/logout")
def auth_logout(
    authorization: str = Header(default=""),
    current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict:
    del current_user
    _revoke_token(authorization)
    return {"ok": True}


@app.get("/api/auth/providers")
def auth_providers() -> dict:
    """Metodos de login externos configurados (habilita botoes no UI)."""
    return {
        "discord": {"configured": _discord_configured()},
        "google": {"configured": _google_configured()},
    }


@app.get("/api/auth/discord/start")
def auth_discord_start(request: Request) -> dict:
    _enforce_rate_limit(request, "oauth-login", OAUTH_RATE_LIMIT, resource="discord")
    if not _discord_configured():
        raise HTTPException(status_code=503, detail="Login com Discord nao configurado no servidor (.env).")
    state = secrets.token_urlsafe(24)
    with _oauth_states_lock:
        _purge_oauth_states()
        _oauth_states[state] = {"purpose": "discord-login", "expires": time.time() + OAUTH_STATE_TTL_SECONDS}
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": _discord_redirect_uri(),
        "response_type": "code",
        "scope": "identify email",
        "state": state,
        "prompt": "consent",
    }
    return {"authorize_url": f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"}


@app.get("/api/auth/google/start")
def auth_google_start(request: Request) -> dict:
    _enforce_rate_limit(request, "oauth-login", OAUTH_RATE_LIMIT, resource="google")
    if not _google_configured():
        raise HTTPException(status_code=503, detail="Login com Google nao configurado no servidor (.env).")
    state = secrets.token_urlsafe(24)
    with _oauth_states_lock:
        _purge_oauth_states()
        _oauth_states[state] = {"purpose": "google-login", "expires": time.time() + OAUTH_STATE_TTL_SECONDS}
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return {"authorize_url": f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"}


def _auth_html(message: dict) -> Response:
    """Pagina do callback: envia token/erro pra janela que abriu e fecha."""
    payload = json.dumps({"source": "kari-auth", **message})
    frontend = json.dumps(FRONTEND_BASE_URL)
    body = f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>Kari - Login</title>
<style>body{{background:#0b0b0e;color:#e5e5e5;font-family:system-ui,sans-serif;
display:grid;place-items:center;height:100vh;margin:0}}a{{color:#6ee7b7}}</style></head>
<body><div><p>{html.escape(str(message.get('detail') or 'Pode fechar esta janela.'))}</p>
<p><a href={frontend}>Voltar ao Kari</a></p></div>
<script>
(function(){{
  var msg = {payload};
  try {{ if (window.opener) {{ window.opener.postMessage(msg, {frontend}); }} }} catch (e) {{}}
  setTimeout(function(){{ try {{ window.close(); }} catch (e) {{}} }}, 400);
}})();
</script></body></html>"""
    return Response(content=body, media_type="text/html")


def _finish_external_login(
    provider: str,
    external_id: str,
    display_name: str,
    email: str = "",
    avatar_url: str = "",
) -> dict:
    """Cria/reusa conta externa e emite somente token local do Kari."""
    display = display_name.strip() or "Leitor"
    key = f"{provider}:{external_id}"
    now = time.time()
    with _users_lock:
        user = user_repository.get(key)
        profile = None
        if user and isinstance(user.get("profile_id"), str):
            with _profiles_lock:
                profile = profile_repository.get(user["profile_id"])
            if not isinstance(profile, dict):
                profile = None
        if profile is None:
            profile = {
                "id": uuid4().hex,
                "display_name": display,
                "avatar_url": avatar_url,
                "favorites": [],
                "created_at": now,
                "updated_at": now,
            }
            with _profiles_lock:
                profile_repository.save(profile)
            user_repository.save(key, {
                "username": display,
                "provider": provider,
                f"{provider}_id": external_id,
                "email": email,
                "profile_id": profile["id"],
                "created_at": now,
            })

    return {
        "token": _issue_token(profile["id"], display),
        "display_name": display,
    }


@app.get("/api/auth/discord/callback")
def auth_discord_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> Response:
    if error:
        return _auth_html({"ok": False, "detail": f"Autorizacao negada: {error}"})
    with _oauth_states_lock:
        _purge_oauth_states()
        entry = _oauth_states.pop(state, None)
    if not entry or entry.get("purpose") != "discord-login":
        return _auth_html({"ok": False, "detail": "Sessao de login invalida ou expirada."})
    if not code:
        return _auth_html({"ok": False, "detail": "Codigo de autorizacao ausente."})

    try:
        token_resp = requests.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _discord_redirect_uri(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token", "")
        user_resp = requests.get(
            DISCORD_USER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        user_resp.raise_for_status()
        duser = user_resp.json()
    except requests.RequestException as exc:
        logger.warning("Discord login falhou error=%s", _safe_error(exc))
        return _auth_html({"ok": False, "detail": "Falha ao autenticar com o Discord."})

    discord_id = str(duser.get("id") or "")
    if not discord_id:
        return _auth_html({"ok": False, "detail": "Discord nao retornou o usuario."})
    display = str(duser.get("global_name") or duser.get("username") or "Leitor").strip() or "Leitor"
    avatar_hash = duser.get("avatar")
    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png?size=256"
        if avatar_hash else ""
    )

    login = _finish_external_login(
        "discord",
        discord_id,
        display,
        str(duser.get("email") or ""),
        avatar_url,
    )
    return _auth_html({
        "ok": True,
        "token": login["token"],
        "detail": f"Conectado como {login['display_name']}!",
    })


@app.get("/api/auth/google/callback")
def auth_google_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> Response:
    if error:
        return _auth_html({"ok": False, "detail": f"Autorizacao negada: {error}"})
    with _oauth_states_lock:
        _purge_oauth_states()
        entry = _oauth_states.pop(state, None)
    if not entry or entry.get("purpose") != "google-login":
        return _auth_html({"ok": False, "detail": "Sessao de login invalida ou expirada."})
    if not code:
        return _auth_html({"ok": False, "detail": "Codigo de autorizacao ausente."})

    try:
        token_resp = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _google_redirect_uri(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            raise requests.RequestException("Google nao retornou access_token")
        user_resp = requests.get(
            GOOGLE_USER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        user_resp.raise_for_status()
        google_user = user_resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Google login falhou error=%s", _safe_error(exc))
        return _auth_html({"ok": False, "detail": "Falha ao autenticar com o Google."})

    google_id = str(google_user.get("sub") or "")
    if not google_id:
        return _auth_html({"ok": False, "detail": "Google nao retornou o usuario."})
    display = str(google_user.get("name") or google_user.get("email") or "Leitor").strip() or "Leitor"
    login = _finish_external_login(
        "google",
        google_id,
        display,
        str(google_user.get("email") or ""),
        str(google_user.get("picture") or ""),
    )
    return _auth_html({
        "ok": True,
        "token": login["token"],
        "detail": f"Conectado como {login['display_name']}!",
    })


@app.get("/api/mangas")
def list_mangas(
    request: Request,
    background_tasks: BackgroundTasks,
    q: str = Query(default="", description="Busca por titulo em fontes reais."),
    genre: str = Query(default="", description="Filtro local por genero."),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Compat: rota legada. Delega p/ a busca (com q) ou a home (sem q).

    Mantida para nao quebrar clientes antigos; as rotas novas e TIPADAS sao
    /api/search e /api/home.
    """
    _enforce_rate_limit(request, "catalog", SEARCH_RATE_LIMIT, resource=q.strip().lower())
    if q.strip():
        return _build_search_payload(q, genre, limit, offset)
    return _build_home_payload(genre, limit, offset)


@app.get("/api/search", response_model=SearchResponse)
def search_mangas(
    request: Request,
    q: str = Query(..., description="Termo de busca por titulo em fontes reais."),
    genre: str = Query(default="", description="Filtro local por genero."),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SearchResponse:
    """Busca tipada: retorna MangaSearchItem (sinopse, generos, autores, etc.)."""
    _enforce_rate_limit(request, "search", SEARCH_RATE_LIMIT, resource=q.strip().lower())
    return SearchResponse(**_build_search_payload(q, genre, limit, offset))


@app.get("/api/home", response_model=HomeResponse)
def home_catalog(
    request: Request,
    genre: str = Query(default="", description="Filtro local por genero."),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> HomeResponse:
    """Home tipada: obras PRONTAS (capa real + capitulos) como MangaHomeItem."""
    _enforce_rate_limit(request, "home", SEARCH_RATE_LIMIT, resource=genre.strip().lower())
    return HomeResponse(**_build_home_payload(genre, limit, offset))


@app.get("/api/hq/library")
def hq_library(q: str = Query(default="", description="Filtra biblioteca local por titulo.")) -> dict:
    _require_desktop_capability()
    normalized = _hq_catalog_items(q.strip())
    items = [_home_item(item) for item in normalized]
    return {
        "items": items,
        "total": len(items),
        "source": "HQ Local",
        "formats": [extension.lstrip(".").upper() for extension in sorted(HQ_SUPPORTED_EXTENSIONS)],
    }


@app.get("/api/plugins/hq-now")
def hq_now_library(
    request: Request,
    q: str = Query(default="", description="Busca HQs exclusivamente no plugin HQ Now."),
    limit: int = Query(default=32, ge=1, le=60),
) -> dict:
    """Catalogo isolado do plugin. Nunca participa da home ou busca geral."""
    _enforce_rate_limit(request, "plugin", EXPENSIVE_RATE_LIMIT, resource="hq-now")
    try:
        raw_items = scraper_coordinator.run(
            "hq-now",
            f"plugin:{q.strip().casefold()}:{limit}",
            lambda: reader.hq_now_plugin.catalog_items(query=q.strip(), limit=limit),
        )
        items: list[dict] = []
        for raw in raw_items:
            normalized = _normalize_manga_item(raw, section="HQ Now")
            if not normalized:
                continue
            card = _home_item(normalized)
            chapter_count = int(normalized.get("chapter_count") or 0)
            card["chapter_count"] = chapter_count
            card["chapter_preview"] = [
                str(number) for number in (normalized.get("chapter_preview") or []) if str(number).strip()
            ][:3]
            card["chapter_status"] = "ready" if chapter_count > 0 else "unavailable"
            items.append(card)
        return {
            "items": items,
            "total": len(items),
            "source": "HQ Now",
            "isolated": True,
        }
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"HQ Now indisponivel: {exc}") from exc


@app.get("/api/plugins/novel-mania")
def novel_mania_library(
    request: Request,
    q: str = Query(default="", description="Busca novels exclusivamente no plugin Novel Mania."),
    limit: int = Query(default=24, ge=1, le=24),
) -> dict:
    """Catalogo isolado do plugin de novels; nao participa da home geral."""
    _enforce_rate_limit(request, "plugin", EXPENSIVE_RATE_LIMIT, resource="novel-mania")
    try:
        raw_items = scraper_coordinator.run(
            "novel-mania",
            f"plugin:{q.strip().casefold()}:{limit}",
            lambda: reader.novel_mania_plugin.catalog_items(query=q.strip(), limit=limit),
        )
        items: list[dict] = []
        for raw in raw_items:
            normalized = _normalize_manga_item(raw, section="Novel Mania")
            if not normalized:
                continue
            card = _home_item(normalized)
            direct_cover = str(raw.get("poster") or "").strip()
            if urlparse(direct_cover).hostname == "assets.novelmania.com.br":
                card["cover_path"] = direct_cover
                card["cover_url"] = direct_cover
                card["cover_fallbacks"] = []
            card["chapter_preview"] = ["Abrir lista de capitulos"]
            card["chapter_status"] = "ready"
            items.append(card)
        return {
            "items": items,
            "total": len(items),
            "source": "Novel Mania",
            "isolated": True,
        }
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Novel Mania indisponivel: {exc}") from exc


@app.get("/api/plugins/central-novel")
def central_novel_library(
    request: Request,
    q: str = Query(default="", description="Busca novels exclusivamente no plugin Central Novel."),
    limit: int = Query(default=24, ge=1, le=24),
) -> dict:
    """Catalogo isolado do Central Novel; nao participa da home geral."""
    _enforce_rate_limit(request, "plugin", EXPENSIVE_RATE_LIMIT, resource="central-novel")
    try:
        raw_items = scraper_coordinator.run(
            "central-novel",
            f"plugin:{q.strip().casefold()}:{limit}",
            lambda: reader.central_novel_plugin.catalog_items(query=q.strip(), limit=limit),
        )
        items: list[dict] = []
        for raw in raw_items:
            normalized = _normalize_manga_item(raw, section="Central Novel")
            if not normalized:
                continue
            card = _home_item(normalized)
            direct_cover = str(raw.get("poster") or "").strip()
            if _is_remote_image_url(direct_cover):
                card["cover_path"] = direct_cover
                card["cover_url"] = direct_cover
                card["cover_fallbacks"] = []
            previews = [
                str(value).strip()
                for value in (raw.get("chapter_preview") or [])
                if str(value or "").strip()
            ]
            card["chapter_preview"] = previews[:3] or ["Abrir lista de capitulos"]
            card["chapter_status"] = "ready"
            items.append(card)
        return {
            "items": items,
            "total": len(items),
            "source": "Central Novel",
            "isolated": True,
        }
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Central Novel indisponivel: {exc}") from exc


@app.get("/api/plugins/tensura-fan")
def tensura_fan_library(
    request: Request,
    q: str = Query(default="", description="Busca exclusivamente no plugin Tensura Fan."),
    limit: int = Query(default=24, ge=1, le=24),
) -> dict:
    """Catalogo isolado do Tensura Fan; nao participa da home geral."""
    _enforce_rate_limit(request, "plugin", EXPENSIVE_RATE_LIMIT, resource="tensura-fan")
    try:
        raw_items = scraper_coordinator.run(
            "tensura-fan",
            f"plugin:{q.strip().casefold()}:{limit}",
            lambda: reader.tensura_fan_plugin.catalog_items(query=q.strip(), limit=limit),
        )
        items: list[dict] = []
        for raw in raw_items:
            normalized = _normalize_manga_item(raw, section="Tensura Fan")
            if not normalized:
                continue
            card = _home_item(normalized)
            direct_cover = str(raw.get("poster") or "").strip()
            if _is_remote_image_url(direct_cover):
                card["cover_path"] = direct_cover
                card["cover_url"] = direct_cover
                card["cover_fallbacks"] = []
            previews = [
                str(value).strip()
                for value in (raw.get("chapter_preview") or [])
                if str(value or "").strip()
            ]
            card["chapter_preview"] = previews[:3] or ["Abrir lista de volumes"]
            card["chapter_status"] = "ready"
            items.append(card)
        return {
            "items": items,
            "total": len(items),
            "source": "Tensura Fan",
            "isolated": True,
        }
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Tensura Fan indisponivel: {exc}") from exc


@app.get("/api/plugins/pleiades-translations")
def pleiades_translations_library(
    request: Request,
    q: str = Query(default="", description="Busca exclusivamente no plugin Pleiades Translations."),
    limit: int = Query(default=24, ge=1, le=24),
) -> dict:
    """Catalogo isolado do Pleiades Translations; nao participa da home geral."""
    _enforce_rate_limit(request, "plugin", EXPENSIVE_RATE_LIMIT, resource="pleiades")
    try:
        raw_items = scraper_coordinator.run(
            "pleiades",
            f"plugin:{q.strip().casefold()}:{limit}",
            lambda: reader.pleiades_translations_plugin.catalog_items(query=q.strip(), limit=limit),
        )
        items: list[dict] = []
        for raw in raw_items:
            normalized = _normalize_manga_item(raw, section="Pleiades Translations")
            if not normalized:
                continue
            card = _home_item(normalized)
            direct_cover = str(raw.get("poster") or "").strip()
            if _is_remote_image_url(direct_cover):
                card["cover_path"] = direct_cover
                card["cover_url"] = direct_cover
                card["cover_fallbacks"] = []
            previews = [
                str(value).strip()
                for value in (raw.get("chapter_preview") or [])
                if str(value or "").strip()
            ]
            card["chapter_preview"] = previews[:3] or ["Abrir lista de capitulos"]
            card["chapter_status"] = "ready"
            items.append(card)
        return {
            "items": items,
            "total": len(items),
            "source": "Pleiades Translations",
            "isolated": True,
        }
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Pleiades Translations indisponivel: {exc}",
        ) from exc


@app.post("/api/hq/import")
async def import_hq(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    issue_number: str = Form(default=""),
    description: str = Form(default=""),
) -> dict:
    _require_desktop_capability()
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if extension not in HQ_SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato nao suportado. Use CBZ, ZIP, CBR ou PDF.")

    temporary_path: Path | None = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=extension,
            prefix="upload-",
            dir=reader.hq_plugin.import_root,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > HQ_MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="HQ excede limite de 350 MB.")
                temporary.write(chunk)
        raw_item = reader.hq_plugin.import_file(
            temporary_path,
            filename,
            title=title,
            issue_number=issue_number,
            description=description,
        )
        normalized = _normalize_manga_item(raw_item, section="Minha biblioteca de HQs")
        if not normalized:
            raise RuntimeError("HQ importada, mas metadados ficaram invalidos.")
        source_url = str(normalized.get("source_url") or "")
        chapters_payload = reader.list_chapters(source_url, lang="pt-br")
        with _chapters_cache_lock:
            chapters_cache[_chapters_cache_key(source_url, "pt-br")] = CacheEntry(
                time.time(), dict(chapters_payload)
            )
        _save_chapters_snapshot()
        return {"ok": True, "item": _home_item(normalized)}
    except HTTPException:
        raise
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


@app.delete("/api/hq/{comic_id}")
def delete_hq(comic_id: str) -> dict:
    _require_desktop_capability()
    try:
        reader.hq_plugin.delete_comic(comic_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/hq/assets/{comic_id}/{issue_id}/{filename}")
def hq_asset(comic_id: str, issue_id: str, filename: str) -> FileResponse:
    _require_desktop_capability()
    try:
        path = reader.hq_plugin.resolve_asset(comic_id, issue_id, filename)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/api/light-novels/library")
def light_novel_library(q: str = Query(default="", description="Filtra light novels locais.")) -> dict:
    _require_desktop_capability()
    normalized = _light_novel_catalog_items(q.strip())
    items = [_home_item(item) for item in normalized]
    return {
        "items": items,
        "total": len(items),
        "source": "Light Novel Local",
        "formats": [extension.lstrip(".").upper() for extension in sorted(NOVEL_SUPPORTED_EXTENSIONS)],
    }


@app.post("/api/light-novels/import")
async def import_light_novel(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    author: str = Form(default=""),
    description: str = Form(default=""),
    language: str = Form(default="pt-br"),
) -> dict:
    _require_desktop_capability()
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if extension not in NOVEL_SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato nao suportado. Use EPUB, TXT ou MD.")

    temporary_path: Path | None = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=extension,
            prefix="upload-",
            dir=reader.light_novel_plugin.import_root,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > NOVEL_MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Light novel excede limite de 60 MB.")
                temporary.write(chunk)
        raw_item = reader.light_novel_plugin.import_file(
            temporary_path,
            filename,
            title=title,
            author=author,
            description=description,
            language=language,
        )
        normalized = _normalize_manga_item(raw_item, section="Minha biblioteca de Light Novels")
        if not normalized:
            raise RuntimeError("Light novel importada, mas metadados ficaram invalidos.")
        source_url = str(normalized.get("source_url") or "")
        item_language = _item_chapter_language(normalized)
        chapters_payload = reader.list_chapters(source_url, lang=item_language)
        with _chapters_cache_lock:
            chapters_cache[_chapters_cache_key(source_url, item_language)] = CacheEntry(
                time.time(), dict(chapters_payload)
            )
        _save_chapters_snapshot()
        return {"ok": True, "item": _home_item(normalized)}
    except HTTPException:
        raise
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


@app.delete("/api/light-novels/{novel_id}")
def delete_light_novel(novel_id: str) -> dict:
    _require_desktop_capability()
    try:
        reader.light_novel_plugin.delete_novel(novel_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/light-novels/assets/{novel_id}/{filename}")
def light_novel_asset(novel_id: str, filename: str) -> FileResponse:
    _require_desktop_capability()
    if filename != "cover.webp":
        raise HTTPException(status_code=404, detail="Arquivo da light novel nao encontrado.")
    try:
        path = reader.light_novel_plugin.resolve_cover(novel_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/api/authors/lookup")
def author_lookup(
    request: Request,
    name: str = Query(..., min_length=1, description="Nome do autor no catalogo."),
    title: str = Query(default="", description="Titulo da obra para casar staff no AniList."),
    source_url: str = Query(default="", description="Fonte da obra para fallback nativo."),
) -> dict:
    try:
        return _lookup_author_profile(name, title, source_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Fontes de autor indisponiveis: {exc}") from exc


def _matches_genre_factory(genre: str):
    genre_filter = normalize_match_text(genre)

    def _matches(item: dict) -> bool:
        if not genre_filter:
            return True
        return any(
            normalize_match_text(g) == genre_filter for g in (item.get("genres") or [])
        )

    return _matches


def _hq_catalog_items(query: str = "") -> list[dict]:
    items: list[dict] = []
    for raw in reader.hq_plugin.catalog_items(query=query):
        normalized = _normalize_manga_item(raw, section="Minha biblioteca de HQs")
        if normalized:
            items.append(normalized)
    return items


def _light_novel_catalog_items(query: str = "") -> list[dict]:
    items: list[dict] = []
    for raw in reader.light_novel_plugin.catalog_items(query=query):
        normalized = _normalize_manga_item(raw, section="Minha biblioteca de Light Novels")
        if normalized:
            items.append(normalized)
    return items


def _build_search_payload(q: str, genre: str, limit: int, offset: int) -> dict:
    """Logica de BUSCA: payload completo (poucos itens), com traducao."""
    query = q.strip()
    _matches_genre = _matches_genre_factory(genre)

    data = _search_mangas(query, limit=max(limit + offset, limit))
    local_sections = [
        ("Minha biblioteca de HQs", _hq_catalog_items(query)),
        ("Minha biblioteca de Light Novels", _light_novel_catalog_items(query)),
    ]
    local_sections = [(title, section_items) for title, section_items in local_sections if section_items]
    local_items = [item for _, section_items in local_sections for item in section_items]
    if local_items:
        data = dict(data)
        local_urls = {str(item.get("source_url") or "") for item in local_items}
        data["items"] = [*local_items, *[
            item for item in (data.get("items") or [])
            if str(item.get("source_url") or "") not in local_urls
        ]]
        data["sections"] = [
            *[{"title": title, "items": section_items} for title, section_items in local_sections],
            *(data.get("sections") or []),
        ]
        data["sources"] = [
            *["HQ Local" if "HQs" in title else "Light Novel Local" for title, _ in local_sections],
            *(data.get("sources") or []),
        ]
    items = [it for it in (data.get("items") or []) if _matches_genre(it)]
    sections = [
        {"title": sec.get("title"), "layout": sec.get("layout", ""),
         "items": [it for it in (sec.get("items") or []) if _matches_genre(it)]}
        for sec in (data.get("sections") or [{"title": "Resultados", "items": items}])
    ]
    sections = [sec for sec in sections if sec["items"]]
    paged = items[offset : offset + limit]
    result = {**data, "items": paged, "sections": sections,
              "total": len(items), "limit": limit, "offset": offset}
    _finalize_payload_descriptions(result, cap=0)  # search rapido: sem traducao remota
    return result


def _build_home_payload(genre: str, limit: int, offset: int) -> dict:
    """Logica da HOME: payload p/ o card, so obras PRONTAS (capa real + caps)."""
    _matches_genre = _matches_genre_factory(genre)

    data = _build_catalog(limit=max(limit + offset, limit))
    hq_items = _hq_catalog_items()
    novel_items = _light_novel_catalog_items()
    local_items = [*hq_items, *novel_items]
    data = dict(data)
    data["items"] = [*local_items, *(data.get("items") or [])]
    base_sections = data.get("sections") or [{"title": "Catálogo", "items": data.get("items") or []}]
    base_sections = [
        section for section in base_sections
        if str(section.get("title") or "").strip().casefold() != "destaques"
    ]
    local_sections = []
    if hq_items:
        local_sections.append({"title": "Minha biblioteca de HQs", "items": hq_items})
    if novel_items:
        local_sections.append({"title": "Minha biblioteca de Light Novels", "items": novel_items})
    if local_sections:
        base_sections = [
            *local_sections,
            *base_sections,
        ]
        data["sources"] = [
            *( ["HQ Local"] if hq_items else [] ),
            *( ["Light Novel Local"] if novel_items else [] ),
            *(data.get("sources") or []),
        ]
    sections_src = _dedupe_cross_source_sections(
        base_sections
    )
    items: list[dict] = []
    seen: set[str] = set()
    pools = [data.get("items") or []]
    pools.extend(section.get("items") or [] for section in sections_src)
    for pool in pools:
        for item in pool:
            if not _matches_genre(item) or not _is_home_ready(item):
                continue
            identity = _canonical_title_identity(str(item.get("title") or "")) or str(item.get("source_url") or "")
            if not identity or identity in seen:
                continue
            seen.add(identity)
            items.append(item)

    preferred_items = [_preferred_home_source(item) for item in items]
    chapter_audit_running = _schedule_home_chapter_audit(preferred_items)
    paged = preferred_items[offset : offset + limit]
    slim_items = [_home_item(it) for it in paged]
    slim_sections = []
    for sec in sections_src:
        sec_items = [
            _home_item(it) for it in (sec.get("items") or [])
            if _matches_genre(it) and _is_home_ready(it)
        ]
        if sec_items:
            slim_sections.append({
                "title": sec.get("title"),
                "layout": sec.get("layout", ""),  # preserva 'carousel' do hero "Em alta"
                "items": sec_items,
            })

    return {
        "items": slim_items,
        "sections": slim_sections,
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "sources": data.get("sources") or [],
        "cached": data.get("cached", False),
        "refreshing": bool(data.get("refreshing", False) or chapter_audit_running),
    }


def _find_catalog_item(source_url: str) -> dict | None:
    """Acha o item completo do catalogo (com descriptions_map/genres/autores) pelo source_url."""
    global catalog_cache
    source_url = str(source_url or "").strip()
    if not source_url:
        return None
    if reader.hq_plugin.is_source(source_url):
        return next(
            (
                item for item in _hq_catalog_items()
                if str(item.get("source_url") or "") == source_url
            ),
            None,
        )
    if reader.light_novel_plugin.is_source(source_url):
        return next(
            (
                item for item in _light_novel_catalog_items()
                if str(item.get("source_url") or "") == source_url
            ),
            None,
        )
    if catalog_cache is None:
        snapshot = _read_catalog_snapshot()
        if snapshot:
            catalog_cache = CacheEntry(time.time(), snapshot)
    if catalog_cache is None:
        return None
    data = catalog_cache.data or {}
    pools = [data.get("items") or []]
    for section in data.get("sections") or []:
        pools.append(section.get("items") or [])
    for pool in pools:
        for item in pool:
            if str(item.get("source_url") or "") == source_url:
                return item
    return None


def _manga_meta_payload(enriched: dict) -> dict:
    return {
        "description": enriched.get("description") or "",
        "descriptions": enriched.get("descriptions") or [],
        "cover_path": enriched.get("cover_path") or "",
        "cover_url": enriched.get("cover_url") or "",
        "cover_fallbacks": enriched.get("cover_fallbacks") or [],
        "cover_original_url": enriched.get("cover_original_url") or "",
        "cover_original_fallbacks": enriched.get("cover_original_fallbacks") or [],
        "genres": enriched.get("genres") or [],
        "authors": enriched.get("authors") or [],
        "status": enriched.get("status") or "",
        "rating": enriched.get("rating"),
        "chapter_languages": enriched.get("chapter_languages") or [],
        "alternative_titles": enriched.get("alternative_titles") or [],
        "mangaupdates_url": enriched.get("mangaupdates_url") or "",
    }


def _build_manga_meta_fast(item: dict | None) -> dict:
    enriched = dict(item or {})
    _strip_descriptions_map(enriched)
    return _manga_meta_payload(enriched)


def _build_manga_meta(item: dict | None, source_url: str, title: str = "") -> dict:
    """Metadados ricos p/ o painel de detalhe: sinopse multi-idioma, generos,
    autores, status, rating e idiomas de capitulo. Vem do catalogo (preferido)
    ou, em ultimo caso, de uma consulta de metadata externa best-effort.
    """
    enriched: dict | None = None
    if item:
        enriched = dict(item)
        provider = str(enriched.get("provider") or "").lower()
        needs_native_metadata = provider == "fliptru" and (
            not enriched.get("description") or not enriched.get("authors")
        )
        if source_url and (not _item_has_cover(enriched) or needs_native_metadata):
            try:
                md = reader.manga_metadata(source_url, include_chapters=False) or {}
                mg = md.get("manga") or {}
                poster = str(mg.get("poster") or "").strip()
                if _is_remote_image_url(poster):
                    enriched["cover_original_url"] = poster
                    enriched.update(_refresh_cover_fields(enriched))
                if mg.get("description") and not enriched.get("description"):
                    enriched["description"] = mg["description"]
                if mg.get("genres") and not enriched.get("genres"):
                    enriched["genres"] = mg["genres"]
                if mg.get("authors") and not enriched.get("authors"):
                    enriched["authors"] = mg["authors"]
                if md.get("available_translated_languages") and not enriched.get("chapter_languages"):
                    enriched["chapter_languages"] = [
                        str(l).lower() for l in (md.get("available_translated_languages") or [])
                    ]
            except Exception:
                pass
        if not _item_has_cover(enriched):
            recovered = _recover_cover_url(str(enriched.get("title") or title or ""))
            if _is_remote_image_url(recovered):
                enriched["cover_original_url"] = recovered
                enriched.update(_refresh_cover_fields(enriched))
        if str(enriched.get("provider") or "").lower() not in {"hq_local", "hq_now", "fliptru", "light_novel_local", "novel_mania", "central_novel", "tensura_fan", "pleiades_translations"}:
            metadata_title = str(enriched.get("title") or title or "")
            _complete_missing_authors(enriched, metadata_title)
            _complete_with_mangaupdates(enriched, metadata_title)
        _finalize_descriptions(enriched)  # descriptions_map -> descriptions[] (PT/EN/...) + traducao
    else:
        try:
            md = reader.manga_metadata(source_url, include_chapters=False) or {}
            mg = md.get("manga") or {}
            rating = mg.get("rating")
            if isinstance(rating, dict):
                rating = rating.get("score")
            enriched = {
                "description": mg.get("description") or "",
                "descriptions_map": mg.get("descriptions") or {},
                "genres": mg.get("genres") or [],
                "authors": mg.get("authors") or [],
                "status": mg.get("status") or "",
                "rating": rating,
                "chapter_languages": [str(l).lower() for l in (md.get("available_translated_languages") or [])],
                "alternative_titles": mg.get("alternative_titles") or [],
            }
            poster = str(mg.get("poster") or "").strip()
            if _is_remote_image_url(poster):
                enriched["cover_original_url"] = poster
                enriched.update(_refresh_cover_fields(enriched))
            if not _item_has_cover(enriched):
                recovered = _recover_cover_url(str(title or mg.get("title") or ""))
                if _is_remote_image_url(recovered):
                    enriched["cover_original_url"] = recovered
                    enriched.update(_refresh_cover_fields(enriched))
            if not (
                reader.hq_plugin.is_source(source_url)
                or reader.hq_now_plugin.is_source(source_url)
                or reader.light_novel_plugin.is_source(source_url)
                or reader.novel_mania_plugin.is_source(source_url)
                or reader.central_novel_plugin.is_source(source_url)
                or reader.tensura_fan_plugin.is_source(source_url)
                or reader.pleiades_translations_plugin.is_source(source_url)
            ):
                metadata_title = str(title or mg.get("title") or "")
                _complete_missing_authors(enriched, metadata_title)
                _complete_with_mangaupdates(enriched, metadata_title)
            _finalize_descriptions(enriched)
        except Exception:
            enriched = {
                "description": "",
                "descriptions_map": {},
                "genres": [],
                "authors": [],
                "status": "",
                "rating": None,
                "chapter_languages": [],
                "alternative_titles": [],
            }
            _complete_missing_authors(enriched, title)
            _complete_with_mangaupdates(enriched, title)
            _finalize_descriptions(enriched)
    return _manga_meta_payload(enriched)


@app.get("/api/manga-meta")
def manga_meta(
    request: Request,
    source_url: str = Query(..., description="URL ou source id da obra."),
    title: str = Query(default=""),
) -> dict:
    _enforce_rate_limit(request, "author-lookup", EXPENSIVE_RATE_LIMIT, resource=name.lower())
    source = unquote(source_url).strip()
    _enforce_rate_limit(request, "manga-meta", EXPENSIVE_RATE_LIMIT, resource=source)
    if not source:
        raise HTTPException(status_code=400, detail="source_url vazio.")
    _ensure_source_allowed(source)
    confirmed = None if (
        reader.hq_now_plugin.is_source(source)
        or reader.novel_mania_plugin.is_source(source)
        or reader.central_novel_plugin.is_source(source)
        or reader.tensura_fan_plugin.is_source(source)
        or reader.pleiades_translations_plugin.is_source(source)
    ) else _confirmed_source_override(title)
    confirmed_url = str((confirmed or {}).get("source_url") or "").strip()
    if confirmed_url:
        source = confirmed_url
    key = f"{source}|{normalize_match_text(title)}"
    cached = manga_meta_cache.get(key)
    if _cache_is_fresh(cached, MANGA_META_CACHE_TTL_SECONDS):
        return copy.deepcopy(cached.data)

    item = _find_catalog_item(source) or _current_source_item(title, source)
    payload = _build_manga_meta(item, source, title)
    manga_meta_cache[key] = CacheEntry(time.time(), copy.deepcopy(payload))
    return payload


@app.get("/api/chapters")
def list_chapters(
    request: Request,
    source_url: str = Query(..., description="URL ou source id da obra."),
    title: str = Query(default="", description="Titulo usado para escolher fonte mais completa."),
    lang: str = Query(default="pt-br"),
    auto_source: bool = Query(default=True, description="Troca MangaDex por fonte com mais capitulos quando possivel."),
) -> dict:
    requested_source = unquote(source_url).strip()
    _enforce_rate_limit(
        request,
        "chapters",
        EXPENSIVE_RATE_LIMIT,
        resource=requested_source,
    )
    source = requested_source
    if not source:
        raise HTTPException(status_code=400, detail="source_url vazio.")
    _ensure_source_allowed(source)
    resolved_item = None
    requested_lang = (lang or "").strip().lower()
    confirmed = None if (
        reader.hq_now_plugin.is_source(source)
        or reader.novel_mania_plugin.is_source(source)
        or reader.central_novel_plugin.is_source(source)
        or reader.tensura_fan_plugin.is_source(source)
        or reader.pleiades_translations_plugin.is_source(source)
    ) else _confirmed_source_override(title)
    confirmed_url = str((confirmed or {}).get("source_url") or "").strip()
    if confirmed_url:
        source = confirmed_url
        resolved_item = {
            "title": title,
            **confirmed,
        }
    requested_provider = _guess_provider({"url": requested_source})
    # Auto-troca pra fonte PT-completa SÓ quando o usuario quer pt-br.
    # Pra EN/JP/etc, mantem a fonte (MangaDex) e puxa capitulos naquele idioma.
    if (
        auto_source
        and not confirmed_url
        and not reader.hq_plugin.is_source(requested_source)
        and not reader.hq_now_plugin.is_source(requested_source)
        and not reader.light_novel_plugin.is_source(requested_source)
        and not reader.novel_mania_plugin.is_source(requested_source)
        and not reader.central_novel_plugin.is_source(requested_source)
        and not reader.tensura_fan_plugin.is_source(requested_source)
        and not reader.pleiades_translations_plugin.is_source(requested_source)
        and title.strip()
        and requested_lang in ("", "pt-br", "pt")
        and requested_provider not in _pt_complete_sources()
    ):
        resolved_item = _cached_source_resolution(title, source, lang)
        if resolved_item is None and _cached_chapter_count(source, lang) > 0:
            resolved_item = _current_source_item(title, source)
            _schedule_source_resolution(title, source, lang)
        elif resolved_item is None:
            resolved_item = _resolve_best_source_for_title(title, source, lang)
        resolved_url = str((resolved_item or {}).get("source_url") or "").strip()
        if resolved_url:
            source = resolved_url

    preferred_source = source
    fallback_source = (
        ONE_PIECE_MANGALIVRE_URL
        if reader._is_pieceproject_source(preferred_source)
        else ""
    )
    fallback_reason = ""
    outage = source_outage_cache.get(preferred_source)
    if fallback_source and _cache_is_fresh(outage, PIECEPROJECT_OUTAGE_TTL_SECONDS):
        source = fallback_source
        fallback_reason = str((outage.data if outage else {}).get("reason") or "piecePROJECT indisponivel")

    try:
        payload = _load_chapters_source_payload(source, lang)
    except Exception as exc:
        source_outage_cache[source] = CacheEntry(time.time(), {"reason": str(exc)})
        chapter_audit_failures[source] = CacheEntry(time.time(), {"error": str(exc)})
        source_resolution_cache.pop(_source_resolution_key(title, requested_source, lang), None)
        if fallback_source and source != fallback_source:
            source_outage_cache[preferred_source] = CacheEntry(time.time(), {"reason": str(exc)})
            source = fallback_source
            fallback_reason = str(exc)
            try:
                payload = _load_chapters_source_payload(source, lang)
            except Exception as fallback_exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"piecePROJECT: {exc}; MangaLivre: {fallback_exc}",
                ) from fallback_exc
        elif title.strip():
            # Fonte resolvida falhou. Tenta achar fonte
            # alternativa via alt titles do MangaDex ou busca direta pelo titulo.
            alt_source = _fallback_source_via_alt_titles(title, requested_source, source, lang)
            if alt_source:
                fallback_reason = str(exc)
                source = alt_source
                try:
                    payload = _load_chapters_source_payload(source, lang)
                    chapter_audit_failures.pop(source, None)
                    alt_item = _current_source_item(title, source)
                    if alt_item:
                        source_resolution_cache[_source_resolution_key(title, requested_source, lang)] = CacheEntry(
                            time.time(),
                            {"item": alt_item},
                        )
                except Exception as fallback_exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Fonte primaria ({exc}); fallback ({fallback_exc})",
                    ) from fallback_exc
            else:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        else:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        source_outage_cache.pop(source, None)
        chapter_audit_failures.pop(source, None)

    if fallback_reason:
        payload["fallback"] = {
            "from": preferred_source,
            "to": source,
            "reason": fallback_reason,
        }
    payload["preferred_source_url"] = preferred_source
    if fallback_source:
        payload["fallback_source_url"] = fallback_source
    payload["requested_source_url"] = requested_source
    payload["resolved_source_url"] = source
    payload["resolved_source"] = _source_label(
        payload.get("provider") or (resolved_item or {}).get("provider")
    )

    # Metadados completos da obra (sinopse multi-idioma, generos, autores, status,
    # idiomas de capitulo) p/ o painel de detalhe — sem inchar a LISTA da home.
    meta_item = _find_catalog_item(source) or _find_catalog_item(requested_source) or resolved_item
    payload["manga"] = _build_manga_meta_fast(meta_item)
    return payload


@app.post("/api/chapter-cards/refresh")
def refresh_chapter_cards(
    request: ChapterCardRefreshRequest,
    http_request: Request,
) -> dict:
    """Atualiza cards persistidos sem refazer catalogo inteiro."""
    _enforce_rate_limit(http_request, "chapter-refresh", EXPENSIVE_RATE_LIMIT)
    result: dict[str, dict] = {}
    pending: list[dict] = []
    seen: set[str] = set()

    for raw_item in request.items[:80]:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        key = str(item.get("source_url") or item.get("id") or item.get("title") or "").strip()
        if not key:
            continue

        confirmed = _confirmed_source_override(str(item.get("title") or ""))
        confirmed_url = str((confirmed or {}).get("source_url") or "").strip()
        if confirmed_url:
            item.update(confirmed)
            item["source_url"] = confirmed_url

        source_url = str(item.get("source_url") or "").strip()
        language = _item_chapter_language(item)
        cached = _cached_chapters_payload(source_url, language) if source_url else None
        update = {
            "source_url": source_url,
            "source": str(item.get("source") or ""),
            "chapter_languages": item.get("chapter_languages") or [language],
        }
        if cached is not None:
            _apply_verified_chapters(update, cached)
            update["chapter_status"] = "ready" if update["chapter_count"] > 0 else "unavailable"
        else:
            failure = chapter_audit_failures.get(source_url)
            update["chapter_status"] = (
                "unavailable"
                if _cache_is_fresh(failure, CHAPTER_AUDIT_FAILURE_TTL_SECONDS)
                else "loading"
            )
            if source_url and source_url not in seen:
                pending.append(item)
                seen.add(source_url)
        result[key] = update

    scheduled = _schedule_home_chapter_audit(pending, priority=True)
    return {"items": result, "refreshing": scheduled}


def _reader_image_url(index: int) -> str:
    return f"/api/reader-image/{index}?v={int(time.time())}"


def _chapter_payload_cache_key(source: str, lang: str) -> str:
    return f"{source.strip()}|{normalize_match_text(lang)}"


def _cached_open_chapter(source: str, lang: str) -> dict | None:
    key = _chapter_payload_cache_key(source, lang)
    with _chapter_payload_cache_lock:
        entry = chapter_payload_cache.get(key)
        if not _cache_is_fresh(entry, CHAPTER_PAYLOAD_CACHE_TTL_SECONDS):
            return None
        return copy.deepcopy(entry.data)


def _store_open_chapter(source: str, lang: str, payload: dict) -> None:
    images = payload.get("images") or []
    if payload.get("mode") != "text" and (
        not images
        or not all(_is_remote_image_url(str(image.get("source_url") or "")) for image in images)
    ):
        return
    key = _chapter_payload_cache_key(source, lang)
    with _chapter_payload_cache_lock:
        chapter_payload_cache[key] = CacheEntry(time.time(), copy.deepcopy(payload))


def _sanitize_web_chapter_payload(payload: dict) -> None:
    if not settings.is_web:
        return
    payload.pop("cache", None)
    if payload.get("mode") == "text":
        return
    images = payload.get("images") or []
    if not images or not all(
        isinstance(image, dict)
        and _is_remote_image_url(str(image.get("source_url") or ""))
        for image in images
    ):
        raise HTTPException(
            status_code=502,
            detail="Esta fonte requer estado local e nao esta disponivel no runtime web.",
        )


def _cached_chapter_neighbors(
    manga_source: str,
    lang: str,
    current_url: str,
    chapter_number: str,
) -> tuple[str | None, str | None]:
    payloads: list[tuple[dict, bool]] = []
    direct = _cached_chapters_payload(manga_source, lang) if manga_source else None
    if direct:
        payloads.append((direct, True))
    with _chapters_cache_lock:
        cached_entries = list(chapters_cache.values())
    for entry in cached_entries:
        if isinstance(entry.data, dict) and entry.data is not direct:
            payloads.append((entry.data, False))

    wanted_number = _number_value(chapter_number)
    for payload, allow_number_match in payloads:
        chapters = [chapter for chapter in payload.get("chapters") or [] if chapter.get("url")]
        if not chapters:
            continue
        current = next(
            (
                chapter for chapter in chapters
                if str(chapter.get("url") or "").rstrip("/") == current_url.rstrip("/")
            ),
            None,
        )
        if current is None and allow_number_match and wanted_number is not None:
            current = next(
                (
                    chapter for chapter in chapters
                    if _number_value(chapter.get("number")) is not None
                    and abs(float(chapter["number"]) - wanted_number) < 0.0001
                ),
                None,
            )
        if current is None:
            continue
        ordered = sorted(
            chapters,
            key=lambda chapter: (
                _number_value(chapter.get("number")) is None,
                _number_value(chapter.get("number")) or 0.0,
            ),
        )
        index = ordered.index(current)
        previous_url = str(ordered[index - 1].get("url") or "") if index > 0 else None
        next_url = str(ordered[index + 1].get("url") or "") if index + 1 < len(ordered) else None
        return previous_url or None, next_url or None
    return None, None


def _apply_cached_chapter_neighbors(
    payload: dict,
    manga_source: str,
    lang: str,
    current_url: str,
    chapter_number: str,
) -> None:
    previous_url, next_url = _cached_chapter_neighbors(
        manga_source,
        lang,
        current_url,
        chapter_number,
    )
    if previous_url or next_url:
        payload["previous"] = previous_url
        payload["next"] = next_url
        chapter = payload.get("chapter")
        if isinstance(chapter, dict):
            chapter["previous"] = previous_url
            chapter["next"] = next_url


@app.get("/api/chapter")
def open_chapter(
    request: Request,
    source_url: str = Query(..., description="URL do capitulo."),
    lang: str = Query(default="pt-br"),
    fallback_source_url: str = Query(default="", description="Fonte original para fallback."),
    chapter_number: str = Query(default="", description="Numero do capitulo para fallback."),
    title: str = Query(default="", description="Titulo da obra p/ achar fonte alternativa quando a fonte cai."),
) -> dict:
    source = unquote(source_url).strip()
    _enforce_rate_limit(request, "chapter-open", EXPENSIVE_RATE_LIMIT, resource=source)
    if not source:
        raise HTTPException(status_code=400, detail="source_url vazio.")
    _ensure_source_allowed(source)
    pieceproject_source = reader._is_pieceproject_source(source)
    manga_source = unquote(fallback_source_url or "").strip()
    if manga_source:
        _ensure_source_allowed(manga_source)
    if not manga_source:
        mangasbrasuka_parts = reader._mangasbrasuka_chapter_parts(source)
        mangageek_parts = reader._mangageek_chapter_parts(source)
        if mangasbrasuka_parts:
            manga_source = reader._mangasbrasuka_manga_url(mangasbrasuka_parts[0])
        elif mangageek_parts:
            manga_source = reader._mangageek_manga_url(mangageek_parts[0])
        elif pieceproject_source:
            manga_source = ONE_PIECE_PIECEPROJECT_URL

    payload = _cached_open_chapter(source, lang)
    if payload is None:
        try:
            payload = _coordinated_chapter_metadata(
                source,
                include_neighbors=False,
            )
        except Exception as exc:
            wanted = chapter_number.strip()
            if pieceproject_source and not wanted:
                wanted = reader._pieceproject_chapter_number_from_source(source) or ""
            if pieceproject_source:
                effective_fallback = ONE_PIECE_MANGALIVRE_URL
            else:
                # Fonte caiu (ex.: mangalivre.blog fora do ar): marca outage e
                # resolve outra fonte PT-completa (Nexus/MangasBrasuka/Sakura) com a
                # MESMA obra, abrindo o capitulo equivalente por numero.
                for outaged in {source, manga_source}:
                    if outaged:
                        source_outage_cache[outaged] = CacheEntry(time.time(), {"reason": str(exc)})
                effective_fallback = ""
                if title.strip():
                    failed_ref = manga_source or source
                    alt_source = _fallback_source_via_alt_titles(
                        title.strip(), failed_ref, failed_ref, lang
                    )
                    if alt_source and alt_source not in {source, manga_source}:
                        effective_fallback = alt_source
                if not effective_fallback:
                    effective_fallback = fallback_source_url
            try:
                payload = _open_fallback_chapter(
                    effective_fallback,
                    lang,
                    wanted,
                    source,
                    exc,
                )
            except Exception:
                payload = None
            if payload is None:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if pieceproject_source and "encaminhou este capitulo" not in str(exc):
                source_outage_cache[ONE_PIECE_PIECEPROJECT_URL] = CacheEntry(
                    time.time(),
                    {"reason": str(exc)},
                )
            # Fallback bem-sucedido p/ outra fonte: fixa a resolucao p/ que a LISTA
            # de capitulos passe a vir da fonte nova nas proximas consultas.
            elif not pieceproject_source and title.strip():
                resolved_alt = str(payload.get("fallback", {}).get("to") or "").strip()
                alt_item = _current_source_item(title.strip(), resolved_alt) if resolved_alt else None
                if alt_item:
                    source_resolution_cache[_source_resolution_key(title.strip(), manga_source or source, lang)] = CacheEntry(
                        time.time(), {"item": alt_item}
                    )

        effective_number = str(
            chapter_number
            or payload.get("number_text")
            or payload.get("number")
            or (payload.get("chapter") or {}).get("number_text")
            or (payload.get("chapter") or {}).get("number")
            or ""
        )
        _apply_cached_chapter_neighbors(
            payload,
            manga_source,
            lang,
            source,
            effective_number,
        )
        _store_open_chapter(source, lang, payload)

    _sanitize_web_chapter_payload(payload)

    if pieceproject_source and payload.get("provider") == "pieceproject":
        source_outage_cache.pop(ONE_PIECE_PIECEPROJECT_URL, None)

    if payload.get("mode") == "text":
        payload["images"] = []
        payload["count"] = 1
        payload["language"] = payload.get("language") or lang
        return payload

    images = []
    for image in payload.get("images") or []:
        image = dict(image)
        source_image_url = str(image.get("source_url") or "").strip()
        if _is_remote_image_url(source_image_url):
            # CDNs sem bloqueio de hotlink vao direto ao browser: HTTP/2, cache
            # proprio e zero ocupacao dos workers do FastAPI.
            if _can_load_image_directly(source_image_url):
                image["src"] = source_image_url
            else:
                image["src"] = _proxy_image_url(source_image_url)
        else:
            image["src"] = _reader_image_url(int(image.get("index") or len(images) + 1))
        images.append(image)

    payload["images"] = images
    payload["count"] = len(images)
    payload["language"] = payload.get("language") or lang
    return payload


@app.get("/api/reader-image/{index}")
def reader_image(index: int, request: Request) -> FileResponse:
    _require_desktop_capability()
    _enforce_rate_limit(request, "reader-image", IMAGE_RATE_LIMIT, resource=str(index))
    try:
        path, content_type = reader.get_image(index)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=content_type)


@app.get("/api/image")
def proxy_image(
    request: Request,
    url: str = Query(..., description="URL remota da imagem."),
) -> Response:
    remote_url = unquote(url).strip()
    _enforce_rate_limit(request, "image-proxy", IMAGE_RATE_LIMIT, resource=remote_url)
    if not _is_remote_image_url(remote_url):
        raise HTTPException(status_code=400, detail="URL de imagem invalida.")
    try:
        image = _fetch_image(remote_url)
    except UnsafeRemoteURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=image.content,
        media_type=image.media_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-MangaTemp-Image-Cache": "memory",
        },
    )
