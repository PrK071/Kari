from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from backend.config import load_settings
from backend.media_storage import ProfileMediaStorage, build_profile_media_storage
from backend.persistence import PersistenceRepositories, build_repositories, token_digest


LEGACY_FILES = ("profiles.json", "users.json", "tokens.json")
PROFILE_MEDIA_FIELDS = {
    "avatar_url": "avatar",
    "background_url": "background",
    "home_background_url": "home_background",
}
MAX_LEGACY_MEDIA_BYTES = 96 * 1024 * 1024


@dataclass
class MigrationReport:
    users_found: int = 0
    users_migrated: int = 0
    profiles_found: int = 0
    profiles_migrated: int = 0
    sessions_found: int = 0
    sessions_migrated: int = 0
    media_found: int = 0
    media_migrated: int = 0
    errors: list[str] = field(default_factory=list)
    backup_dir: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {**asdict(self), "ok": self.ok}


def _strict_read(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} deve conter um objeto JSON.")
    return payload


def _backup_sources(source_dir: Path, backup_root: Path | None) -> Path | None:
    existing = [source_dir / name for name in LEGACY_FILES if (source_dir / name).is_file()]
    if not existing:
        return None
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    destination = (backup_root or source_dir / "migration-backups") / timestamp
    suffix = 1
    while destination.exists():
        destination = destination.with_name(f"{timestamp}-{suffix}")
        suffix += 1
    destination.mkdir(parents=True, exist_ok=False)
    hashes: dict[str, str] = {}
    for source in existing:
        target = destination / source.name
        shutil.copy2(source, target)
        hashes[source.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "manifest.json").write_text(
        json.dumps({"files": hashes}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return destination


def _migrate_profile_media(
    profile: dict,
    *,
    media_storage: ProfileMediaStorage | None,
    static_dir: Path | None,
    report: MigrationReport,
) -> None:
    profile_id = str(profile["id"])
    for field_name, kind in PROFILE_MEDIA_FIELDS.items():
        raw_url = str(profile.get(field_name) or "").strip()
        parsed = urlparse(raw_url)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/static/profiles/"):
            continue
        report.media_found += 1
        if media_storage is None or static_dir is None:
            raise ValueError("legacy-media-storage-required")

        root = static_dir.resolve()
        candidate = (root / unquote(parsed.path.removeprefix("/static/"))).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("legacy-media-path-invalid") from exc
        if not candidate.is_file():
            raise FileNotFoundError("legacy-media-file-missing")
        if candidate.stat().st_size > MAX_LEGACY_MEDIA_BYTES:
            raise ValueError("legacy-media-file-too-large")

        suffix = candidate.suffix.lower()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        profile[field_name] = media_storage.replace(
            profile_id,
            kind,
            suffix,
            candidate.read_bytes(),
            content_type,
        )
        report.media_migrated += 1


def migrate_identity_data(
    source_dir: Path,
    repositories: PersistenceRepositories,
    *,
    backup_root: Path | None = None,
    media_storage: ProfileMediaStorage | None = None,
    static_dir: Path | None = None,
) -> MigrationReport:
    source_dir = source_dir.resolve()
    report = MigrationReport()
    try:
        backup = _backup_sources(source_dir, backup_root.resolve() if backup_root else None)
        report.backup_dir = str(backup) if backup else ""
    except OSError as exc:
        report.errors.append(f"backup: {type(exc).__name__}")
        return report

    try:
        profiles = _strict_read(source_dir / "profiles.json")
        users = _strict_read(source_dir / "users.json")
        sessions = _strict_read(source_dir / "tokens.json")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report.errors.append(f"source: {type(exc).__name__}")
        return report

    report.profiles_found = len(profiles)
    report.users_found = len(users)
    report.sessions_found = len(sessions)

    for position, (profile_id, raw_profile) in enumerate(profiles.items(), start=1):
        if not isinstance(raw_profile, dict):
            report.errors.append(f"profile[{position}]: invalid-record")
            continue
        profile = dict(raw_profile)
        profile["id"] = str(profile.get("id") or profile_id)
        try:
            _migrate_profile_media(
                profile,
                media_storage=media_storage,
                static_dir=static_dir,
                report=report,
            )
            repositories.profiles.save(profile)
            report.profiles_migrated += 1
        except Exception as exc:
            report.errors.append(f"profile[{position}]: {type(exc).__name__}")

    for position, (login_key, raw_user) in enumerate(users.items(), start=1):
        if not isinstance(raw_user, dict):
            report.errors.append(f"user[{position}]: invalid-record")
            continue
        try:
            repositories.users.save(str(login_key), dict(raw_user))
            report.users_migrated += 1
        except Exception as exc:
            report.errors.append(f"user[{position}]: {type(exc).__name__}")

    for position, (token, raw_session) in enumerate(sessions.items(), start=1):
        if not isinstance(raw_session, dict):
            report.errors.append(f"session[{position}]: invalid-record")
            continue
        try:
            repositories.sessions.save(str(token), dict(raw_session))
            report.sessions_migrated += 1
        except Exception as exc:
            report.errors.append(f"session[{position}]: {type(exc).__name__}")

    expected_session_digests = {token_digest(str(token)) for token in sessions}
    stored_session_digests = set(repositories.sessions.all())
    if report.profiles_migrated and len(repositories.profiles.all()) < report.profiles_migrated:
        report.errors.append("verify: profile-count")
    if report.users_migrated and len(repositories.users.all()) < report.users_migrated:
        report.errors.append("verify: user-count")
    if report.sessions_migrated and not expected_session_digests.issubset(stored_session_digests):
        report.errors.append("verify: session-digest")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Migra identidade JSON do Kari para PostgreSQL.")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument(
        "--static-dir",
        type=Path,
        help="Diretorio static legado; obrigatorio se profiles.json referenciar /static/profiles.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "").strip()
    secret_key = os.environ.get("KARI_SECRET_KEY", "").strip()
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        parser.error("DATABASE_URL PostgreSQL e obrigatorio.")
    if len(secret_key) < 32:
        parser.error("KARI_SECRET_KEY deve ter ao menos 32 caracteres.")

    unused = lambda: Path("unused.json")
    repositories = build_repositories(
        backend="postgres",
        database_url=database_url,
        secret_key=secret_key,
        users_path=unused,
        profiles_path=unused,
        sessions_path=unused,
    )
    media_storage = None
    if os.environ.get("KARI_STORAGE_BACKEND", "").strip().lower() == "object_storage":
        settings = load_settings()
        media_storage = build_profile_media_storage(
            settings,
            args.static_dir or Path("unused-static"),
        )
    report = migrate_identity_data(
        args.source_dir,
        repositories,
        backup_root=args.backup_dir,
        media_storage=media_storage,
        static_dir=args.static_dir,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
