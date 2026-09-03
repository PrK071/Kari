from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse


class ConfigurationError(RuntimeError):
    """Raised when environment configuration is unsafe or incomplete."""


_ENVIRONMENTS = {"development", "production"}
_RUNTIMES = {"desktop", "web"}
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
    database_url: str
    storage_backend: str
    secret_key: str

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

    return Settings(
        environment=environment,
        runtime=runtime,
        backend_url=backend_url,
        frontend_url=frontend_url,
        allowed_origins=allowed_origins,
        database_url=source.get("DATABASE_URL", "").strip(),
        storage_backend=storage_backend,
        secret_key=source.get("KARI_SECRET_KEY", "").strip(),
    )
