from __future__ import annotations

import mimetypes
import re
import threading
from pathlib import Path
from typing import Protocol
from urllib.parse import quote


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"}


class MediaStorageUnavailable(RuntimeError):
    pass


class ProfileMediaStorage(Protocol):
    writable: bool

    def replace(
        self,
        profile_id: str,
        kind: str,
        suffix: str,
        content: bytes,
        content_type: str,
    ) -> str: ...

    def delete(self, profile_id: str, kind: str) -> None: ...

    def read(self, profile_id: str, kind: str) -> tuple[bytes, str] | None: ...


def _validate_components(profile_id: str, kind: str, suffix: str = ".jpg") -> None:
    if not _SAFE_COMPONENT.fullmatch(profile_id):
        raise ValueError("Identificador de perfil invalido para storage.")
    if kind not in {"avatar", "background", "home_background"}:
        raise ValueError("Tipo de midia de perfil invalido.")
    if suffix not in _SUFFIXES:
        raise ValueError("Extensao de midia de perfil invalida.")


class LocalProfileMediaStorage:
    writable = True

    def __init__(self, static_dir: Path) -> None:
        self.static_dir = static_dir.resolve()
        self.profile_dir = self.static_dir / "profiles"
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def delete(self, profile_id: str, kind: str) -> None:
        _validate_components(profile_id, kind)
        directory = self.profile_dir / profile_id
        if not directory.exists():
            return
        for suffix in _SUFFIXES:
            target = directory / f"{kind}{suffix}"
            try:
                target.unlink()
            except FileNotFoundError:
                continue

    def replace(
        self,
        profile_id: str,
        kind: str,
        suffix: str,
        content: bytes,
        content_type: str,
    ) -> str:
        del content_type
        _validate_components(profile_id, kind, suffix)
        directory = self.profile_dir / profile_id
        directory.mkdir(parents=True, exist_ok=True)
        self.delete(profile_id, kind)
        target = directory / f"{kind}{suffix}"
        target.write_bytes(content)
        relative = target.relative_to(self.static_dir).as_posix()
        return f"/static/{relative}"

    def read(self, profile_id: str, kind: str) -> tuple[bytes, str] | None:
        _validate_components(profile_id, kind)
        directory = self.profile_dir / profile_id
        for suffix in _SUFFIXES:
            target = directory / f"{kind}{suffix}"
            if not target.is_file():
                continue
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return target.read_bytes(), content_type
        return None


class DisabledProfileMediaStorage:
    writable = False

    def delete(self, profile_id: str, kind: str) -> None:
        _validate_components(profile_id, kind)

    def read(self, profile_id: str, kind: str) -> tuple[bytes, str] | None:
        del profile_id, kind
        raise MediaStorageUnavailable(
            "Uploads persistentes exigem KARI_STORAGE_BACKEND=object_storage no runtime web."
        )

    def replace(
        self,
        profile_id: str,
        kind: str,
        suffix: str,
        content: bytes,
        content_type: str,
    ) -> str:
        del profile_id, kind, suffix, content, content_type
        raise MediaStorageUnavailable(
            "Uploads persistentes exigem KARI_STORAGE_BACKEND=object_storage no runtime web."
        )


class S3ProfileMediaStorage:
    writable = True

    def __init__(self, client, *, bucket: str, public_base_url: str) -> None:
        self.client = client
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/")

    @staticmethod
    def _key(profile_id: str, kind: str, suffix: str) -> str:
        _validate_components(profile_id, kind, suffix)
        return f"profiles/{profile_id}/{kind}{suffix}"

    def delete(self, profile_id: str, kind: str) -> None:
        _validate_components(profile_id, kind)
        try:
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [
                        {"Key": self._key(profile_id, kind, suffix)}
                        for suffix in sorted(_SUFFIXES)
                    ],
                    "Quiet": True,
                },
            )
        except Exception as exc:
            raise MediaStorageUnavailable("Object Storage indisponivel.") from exc

    def read(self, profile_id: str, kind: str) -> tuple[bytes, str] | None:
        _validate_components(profile_id, kind)
        import botocore.exceptions

        for suffix in _SUFFIXES:
            key = self._key(profile_id, kind, suffix)
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=key)
            except botocore.exceptions.ClientError as exc:
                code = (exc.response or {}).get("Error", {}).get("Code", "")
                if code in {"NoSuchKey", "404", "NotFound"}:
                    continue
                raise MediaStorageUnavailable("Object Storage indisponivel.") from exc
            except Exception as exc:
                raise MediaStorageUnavailable("Object Storage indisponivel.") from exc
            content_type = (response.get("ContentType") or "").strip()
            try:
                content = response["Body"].read()
            except Exception as exc:
                raise MediaStorageUnavailable("Object Storage indisponivel.") from exc
            return content, content_type
        return None

    def replace(
        self,
        profile_id: str,
        kind: str,
        suffix: str,
        content: bytes,
        content_type: str,
    ) -> str:
        key = self._key(profile_id, kind, suffix)
        self.delete(profile_id, kind)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                CacheControl="private, max-age=31536000, immutable",
            )
        except Exception as exc:
            raise MediaStorageUnavailable("Object Storage indisponivel.") from exc
        return f"/api/profiles/{quote(profile_id, safe='')}/media/{quote(kind, safe='')}"


class LazyS3ProfileMediaStorage:
    """Build the boto3 client only when profile media is actually accessed."""

    writable = True

    def __init__(self, settings) -> None:
        self._settings = settings
        self._storage: S3ProfileMediaStorage | None = None
        self._lock = threading.Lock()

    def _get(self) -> S3ProfileMediaStorage:
        if self._storage is not None:
            return self._storage
        with self._lock:
            if self._storage is not None:
                return self._storage
            try:
                import boto3

                client = boto3.client(
                    "s3",
                    endpoint_url=self._settings.object_storage_endpoint,
                    region_name=self._settings.object_storage_region,
                    aws_access_key_id=self._settings.object_storage_access_key_id,
                    aws_secret_access_key=self._settings.object_storage_secret_access_key,
                )
            except Exception as exc:
                raise MediaStorageUnavailable("Object Storage indisponivel.") from exc
            self._storage = S3ProfileMediaStorage(
                client,
                bucket=self._settings.object_storage_bucket,
                public_base_url=self._settings.object_storage_public_base_url,
            )
            return self._storage

    def delete(self, profile_id: str, kind: str) -> None:
        self._get().delete(profile_id, kind)

    def read(self, profile_id: str, kind: str) -> tuple[bytes, str] | None:
        return self._get().read(profile_id, kind)

    def replace(
        self,
        profile_id: str,
        kind: str,
        suffix: str,
        content: bytes,
        content_type: str,
    ) -> str:
        return self._get().replace(profile_id, kind, suffix, content, content_type)


def build_profile_media_storage(settings, static_dir: Path) -> ProfileMediaStorage:
    if settings.storage_backend == "filesystem":
        if settings.is_web:
            return DisabledProfileMediaStorage()
        return LocalProfileMediaStorage(static_dir)
    if settings.storage_backend != "object_storage":
        raise ValueError("Backend de midia desconhecido.")

    return LazyS3ProfileMediaStorage(settings)
