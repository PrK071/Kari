from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlparse


class ConfigurationError(RuntimeError):
    """Raised when environment configuration is unsafe or incomplete."""


_ENVIRONMENTS = {"development", "production"}
_RUNTIMES = {"desktop", "web"}
_PERSISTENCE_BACKENDS = {"json", "postgres"}
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_DEVELOPMENT_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _choice(name: str, value: str, allowed: set[str]) -> str:
    normalized = value.strip().lower()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigurationError(f"{name} deve ser um de: {choices}.")
    return normalized


def _http_url(name: str, value: str, *, require_https: bool) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"{name} deve ser uma URL http(s) absoluta.")
    if require_https and parsed.scheme != "https":
        raise ConfigurationError(f"{name} deve usar HTTPS em producao.")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} deve conter somente scheme e host.")
    return normalized


def _public_asset_url(name: str, value: str, *, require_https: bool) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"{name} deve ser uma URL http(s) absoluta.")
    if require_https and parsed.scheme != "https":
        raise ConfigurationError(f"{name} deve usar HTTPS em producao.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} nao aceita credenciais, query ou fragmento.")
    return normalized


def _allowed_origins(raw: str, *, production: bool) -> tuple[str, ...]:
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values:
        if production:
            raise ConfigurationError(
                "KARI_ALLOWED_ORIGINS e obrigatorio em producao."
            )
        return _DEVELOPMENT_ORIGINS

    origins: list[str] = []
    for value in values:
        if value == "*":
            raise ConfigurationError("KARI_ALLOWED_ORIGINS nao aceita wildcard '*'.")
        origin = _http_url(
            "KARI_ALLOWED_ORIGINS",
            value,
            require_https=production,
        )
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


@dataclass(frozen=True)
class Settings:
    environment: str
    runtime: str
    backend_url: str
    frontend_url: str
    allowed_origins: tuple[str, ...]
    database_url: str = field(repr=False)
    persistence_backend: str
    storage_backend: str
    object_storage_bucket: str
    object_storage_endpoint: str
    object_storage_region: str
    object_storage_access_key_id: str = field(repr=False)
    object_storage_secret_access_key: str = field(repr=False)
    object_storage_public_base_url: str
    secret_key: str = field(repr=False)
    session_ttl_seconds: int
    rate_limit_backend: str
    scraper_max_concurrency: int
    scraper_max_per_source: int
    background_max_concurrency: int
    log_level: str

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_web(self) -> bool:
        return self.runtime == "web"

    def public_capabilities(self) -> dict[str, bool | str]:
        desktop = self.runtime == "desktop"
        return {
            "runtime": self.runtime,
            "remote_sources": True,
            "accounts": True,
            "local_libraries": desktop,
            "local_file_imports": desktop,
            "sakura": desktop,
            "dragontea": desktop,
        }


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if environ is None else environ
    environment = _choice(
        "KARI_ENV",
        source.get("KARI_ENV", "development"),
        _ENVIRONMENTS,
    )
    production = environment == "production"
    runtime_default = "web" if production else "desktop"
    runtime = _choice(
        "KARI_RUNTIME",
        source.get("KARI_RUNTIME", runtime_default),
        _RUNTIMES,
    )

    backend_raw = source.get("KARI_BACKEND_URL", "").strip()
    frontend_raw = source.get("KARI_FRONTEND_URL", "").strip()
    if production and (not backend_raw or not frontend_raw):
        raise ConfigurationError(
            "KARI_BACKEND_URL e KARI_FRONTEND_URL sao obrigatorios em producao."
        )

    backend_url = _http_url(
        "KARI_BACKEND_URL",
        backend_raw or "http://127.0.0.1:8000",
        require_https=production,
    )
    frontend_url = _http_url(
        "KARI_FRONTEND_URL",
        frontend_raw or "http://127.0.0.1:5173",
        require_https=production,
    )
    allowed_origins = _allowed_origins(
        source.get("KARI_ALLOWED_ORIGINS", ""),
        production=production,
    )

    storage_backend = source.get("KARI_STORAGE_BACKEND", "filesystem").strip().lower()
    if storage_backend not in {"filesystem", "object_storage"}:
        raise ConfigurationError(
            "KARI_STORAGE_BACKEND deve ser filesystem ou object_storage."
        )
    object_storage_bucket = source.get("KARI_OBJECT_STORAGE_BUCKET", "").strip()
    object_storage_endpoint_raw = source.get("KARI_OBJECT_STORAGE_ENDPOINT", "").strip()
    object_storage_region = source.get("KARI_OBJECT_STORAGE_REGION", "").strip()
    object_storage_access_key_id = source.get("KARI_OBJECT_STORAGE_ACCESS_KEY_ID", "").strip()
    object_storage_secret_access_key = source.get("KARI_OBJECT_STORAGE_SECRET_ACCESS_KEY", "").strip()
    object_storage_public_base_raw = source.get("KARI_OBJECT_STORAGE_PUBLIC_BASE_URL", "").strip()
    object_storage_endpoint = ""
    object_storage_public_base_url = ""
    if storage_backend == "object_storage":
        required = {
            "KARI_OBJECT_STORAGE_BUCKET": object_storage_bucket,
            "KARI_OBJECT_STORAGE_ENDPOINT": object_storage_endpoint_raw,
            "KARI_OBJECT_STORAGE_REGION": object_storage_region,
            "KARI_OBJECT_STORAGE_ACCESS_KEY_ID": object_storage_access_key_id,
            "KARI_OBJECT_STORAGE_SECRET_ACCESS_KEY": object_storage_secret_access_key,
            "KARI_OBJECT_STORAGE_PUBLIC_BASE_URL": object_storage_public_base_raw,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError(
                "Configuracao de Object Storage incompleta: " + ", ".join(missing)
            )
        object_storage_endpoint = _http_url(
            "KARI_OBJECT_STORAGE_ENDPOINT",
            object_storage_endpoint_raw,
            require_https=production,
        )
        object_storage_public_base_url = _public_asset_url(
            "KARI_OBJECT_STORAGE_PUBLIC_BASE_URL",
            object_storage_public_base_raw,
            require_https=production,
        )

    persistence_default = "postgres" if production else "json"
    persistence_backend = _choice(
        "KARI_PERSISTENCE_BACKEND",
        source.get("KARI_PERSISTENCE_BACKEND", persistence_default),
        _PERSISTENCE_BACKENDS,
    )
    database_url = source.get("DATABASE_URL", "").strip()
    secret_key = source.get("KARI_SECRET_KEY", "").strip()
    if persistence_backend == "postgres" and not database_url:
        raise ConfigurationError(
            "DATABASE_URL e obrigatorio com persistencia PostgreSQL."
        )
    if production and not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ConfigurationError("DATABASE_URL deve apontar para PostgreSQL em producao.")
    if persistence_backend == "postgres" and len(secret_key) < 32:
        raise ConfigurationError(
            "KARI_SECRET_KEY deve ter ao menos 32 caracteres com persistencia PostgreSQL."
        )
    try:
        session_ttl_hours = int(source.get("KARI_SESSION_TTL_HOURS", "720"))
    except ValueError as exc:
        raise ConfigurationError("KARI_SESSION_TTL_HOURS deve ser inteiro.") from exc
    if not 1 <= session_ttl_hours <= 720:
        raise ConfigurationError("KARI_SESSION_TTL_HOURS deve estar entre 1 e 720.")
    rate_limit_backend = source.get("KARI_RATE_LIMIT_BACKEND", "memory").strip().lower()
    if rate_limit_backend != "memory":
        raise ConfigurationError(
            "KARI_RATE_LIMIT_BACKEND suporta somente memory nesta versao."
        )
    try:
        scraper_max_concurrency = int(source.get("KARI_SCRAPER_MAX_CONCURRENCY", "12"))
        scraper_max_per_source = int(source.get("KARI_SCRAPER_MAX_PER_SOURCE", "2"))
    except ValueError as exc:
        raise ConfigurationError("Limites de scraper devem ser inteiros.") from exc
    if not 1 <= scraper_max_concurrency <= 64:
        raise ConfigurationError("KARI_SCRAPER_MAX_CONCURRENCY deve estar entre 1 e 64.")
    if not 1 <= scraper_max_per_source <= scraper_max_concurrency:
        raise ConfigurationError(
            "KARI_SCRAPER_MAX_PER_SOURCE deve estar entre 1 e o limite global."
        )
    try:
        background_max_concurrency = int(source.get("KARI_BACKGROUND_MAX_CONCURRENCY", "4"))
    except ValueError as exc:
        raise ConfigurationError("KARI_BACKGROUND_MAX_CONCURRENCY deve ser inteiro.") from exc
    if not 1 <= background_max_concurrency <= 16:
        raise ConfigurationError(
            "KARI_BACKGROUND_MAX_CONCURRENCY deve estar entre 1 e 16."
        )
    log_level = source.get("KARI_LOG_LEVEL", "INFO").strip().upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigurationError(
            "KARI_LOG_LEVEL deve ser DEBUG, INFO, WARNING ou ERROR."
        )

    return Settings(
        environment=environment,
        runtime=runtime,
        backend_url=backend_url,
        frontend_url=frontend_url,
        allowed_origins=allowed_origins,
        database_url=database_url,
        persistence_backend=persistence_backend,
        storage_backend=storage_backend,
        object_storage_bucket=object_storage_bucket,
        object_storage_endpoint=object_storage_endpoint,
        object_storage_region=object_storage_region,
        object_storage_access_key_id=object_storage_access_key_id,
        object_storage_secret_access_key=object_storage_secret_access_key,
        object_storage_public_base_url=object_storage_public_base_url,
        secret_key=secret_key,
        session_ttl_seconds=session_ttl_hours * 60 * 60,
        rate_limit_backend=rate_limit_backend,
        scraper_max_concurrency=scraper_max_concurrency,
        scraper_max_per_source=scraper_max_per_source,
        background_max_concurrency=background_max_concurrency,
        log_level=log_level,
    )
