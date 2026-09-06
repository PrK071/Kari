from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend import main
from backend.config import ConfigurationError, load_settings
from backend.media_storage import (
    DisabledProfileMediaStorage,
    LocalProfileMediaStorage,
    MediaStorageUnavailable,
    S3ProfileMediaStorage,
)


class _FakeS3Client:
    def __init__(self, objects: dict[str, tuple[bytes, str]] | None = None) -> None:
        self.deletes: list[dict] = []
        self.puts: list[dict] = []
        self.objects: dict[str, tuple[bytes, str]] = objects or {}

    def delete_objects(self, **kwargs) -> None:
        self.deletes.append(kwargs)
        for obj in kwargs.get("Delete", {}).get("Objects", []):
            self.objects.pop(obj["Key"], None)

    def put_object(self, **kwargs) -> None:
        self.puts.append(kwargs)
        self.objects[kwargs["Key"]] = (
            kwargs["Body"] if isinstance(kwargs["Body"], bytes) else bytes(kwargs["Body"]),
            kwargs.get("ContentType", ""),
        )

    def get_object(self, **kwargs) -> dict:
        key = kwargs["Key"]
        if key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}},
                "GetObject",
            )
        content, content_type = self.objects[key]
        return {"Body": io.BytesIO(content), "ContentType": content_type}


class ProfileMediaStorageTests(unittest.TestCase):
    def test_local_storage_is_scoped_and_replaceable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalProfileMediaStorage(Path(directory))
            url = storage.replace("profile-1", "avatar", ".png", b"image", "image/png")

            self.assertEqual(url, "/static/profiles/profile-1/avatar.png")
            self.assertEqual(
                (Path(directory) / "profiles" / "profile-1" / "avatar.png").read_bytes(),
                b"image",
            )
            storage.delete("profile-1", "avatar")
            self.assertFalse((Path(directory) / "profiles" / "profile-1" / "avatar.png").exists())

        with self.assertRaises(ValueError):
            storage.replace("../escape", "avatar", ".png", b"bad", "image/png")

    def test_s3_storage_replaces_known_variants_without_exposing_secrets(self) -> None:
        client = _FakeS3Client()
        storage = S3ProfileMediaStorage(
            client,
            bucket="kari-media",
            public_base_url="https://media.example.test/public",
        )

        url = storage.replace("profile-1", "background", ".webp", b"image", "image/webp")

        self.assertEqual(
            url,
            "/api/profiles/profile-1/media/background",
        )
        self.assertEqual(len(client.deletes), 1)
        self.assertEqual(client.puts[0]["Bucket"], "kari-media")
        self.assertEqual(client.puts[0]["Key"], "profiles/profile-1/background.webp")
        self.assertEqual(client.puts[0]["ContentType"], "image/webp")

    def test_s3_storage_reads_object_back_with_content_type(self) -> None:
        client = _FakeS3Client(
            {"profiles/profile-1/avatar.png": (b"image-bytes", "image/png")}
        )
        storage = S3ProfileMediaStorage(
            client,
            bucket="kari-media",
            public_base_url="https://media.example.test/public",
        )

        payload = storage.read("profile-1", "avatar")

        self.assertEqual(payload, (b"image-bytes", "image/png"))

    def test_s3_storage_read_missing_object_returns_none(self) -> None:
        storage = S3ProfileMediaStorage(
            _FakeS3Client(),
            bucket="kari-media",
            public_base_url="https://media.example.test/public",
        )

        self.assertIsNone(storage.read("profile-1", "avatar"))

    def test_s3_storage_failures_never_expose_secrets(self) -> None:
        class ExplodingClient:
            def get_object(self, **kwargs):
                raise RuntimeError("access-key-12345 leaked upstream")

        storage = S3ProfileMediaStorage(
            ExplodingClient(),
            bucket="kari-media",
            public_base_url="https://media.example.test/public",
        )

        with self.assertRaises(MediaStorageUnavailable) as raised:
            storage.read("profile-1", "avatar")
        self.assertNotIn("access-key-12345", str(raised.exception))

    def test_local_storage_reads_stored_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalProfileMediaStorage(Path(directory))
            storage.replace("profile-1", "avatar", ".png", b"local-bytes", "image/png")

            payload = storage.read("profile-1", "avatar")

            self.assertEqual(payload, (b"local-bytes", "image/png"))
            self.assertIsNone(storage.read("profile-1", "background"))

    def test_web_filesystem_rejects_binary_upload(self) -> None:
        disabled = DisabledProfileMediaStorage()
        with self.assertRaises(MediaStorageUnavailable):
            disabled.replace("profile-1", "avatar", ".png", b"image", "image/png")

        request = SimpleNamespace(data="aW1hZ2U=", url=None)
        with patch.object(main, "profile_media_storage", disabled):
            with self.assertRaises(HTTPException) as raised:
                main._apply_profile_image({"id": "profile-1"}, "avatar", request)
        self.assertEqual(raised.exception.status_code, 503)

    def test_web_profile_url_requires_safe_transport(self) -> None:
        web_settings = load_settings(
            {
                "KARI_RUNTIME": "web",
                "KARI_BACKEND_URL": "http://127.0.0.1:8000",
                "KARI_FRONTEND_URL": "http://127.0.0.1:5173",
            }
        )
        disabled = DisabledProfileMediaStorage()
        with patch.object(main, "settings", web_settings), patch.object(
            main, "profile_media_storage", disabled
        ):
            with self.assertRaises(HTTPException) as raised:
                main._apply_profile_image(
                    {"id": "profile-1"},
                    "avatar",
                    SimpleNamespace(data=None, url="http://images.example.test/avatar.png"),
                )
            self.assertEqual(raised.exception.status_code, 422)
            url = main._apply_profile_image(
                {"id": "profile-1"},
                "avatar",
                SimpleNamespace(data=None, url="https://images.example.test/avatar.png"),
            )
        self.assertEqual(url, "https://images.example.test/avatar.png")

    def test_web_static_mount_blocks_legacy_profile_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage_root = Path(directory)
            profile_file = storage_root / "profiles" / "profile-1" / "avatar.png"
            profile_file.parent.mkdir(parents=True)
            profile_file.write_bytes(b"private")
            static_files = main.CachedStaticFiles(directory=directory)
            web_settings = load_settings(
                {
                    "KARI_RUNTIME": "web",
                    "KARI_BACKEND_URL": "http://127.0.0.1:8000",
                    "KARI_FRONTEND_URL": "http://127.0.0.1:5173",
                }
            )
            with patch.object(main, "settings", web_settings):
                response = asyncio.run(static_files.get_response("profiles/profile-1/avatar.png", {}))
        self.assertEqual(response.status_code, 404)

    def test_object_storage_configuration_is_explicit(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_STORAGE_BACKEND": "object_storage"})

        settings = load_settings(
            {
                "KARI_STORAGE_BACKEND": "object_storage",
                "KARI_OBJECT_STORAGE_BUCKET": "kari-media",
                "KARI_OBJECT_STORAGE_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
                "KARI_OBJECT_STORAGE_REGION": "us-east-005",
                "KARI_OBJECT_STORAGE_ACCESS_KEY_ID": "access",
                "KARI_OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret",
            }
        )
        self.assertEqual(settings.object_storage_bucket, "kari-media")
        self.assertEqual(settings.object_storage_public_base_url, "")
        rendered = repr(settings)
        self.assertNotIn("test-secret", rendered)
        self.assertNotIn("access", rendered)

    def test_object_storage_public_base_url_optional_but_validated(self) -> None:
        settings = load_settings(
            {
                "KARI_STORAGE_BACKEND": "object_storage",
                "KARI_OBJECT_STORAGE_BUCKET": "kari-media",
                "KARI_OBJECT_STORAGE_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
                "KARI_OBJECT_STORAGE_REGION": "us-east-005",
                "KARI_OBJECT_STORAGE_ACCESS_KEY_ID": "access",
                "KARI_OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret",
                "KARI_OBJECT_STORAGE_PUBLIC_BASE_URL": "https://media.example.test/public",
            }
        )
        self.assertEqual(
            settings.object_storage_public_base_url,
            "https://media.example.test/public",
        )
        with self.assertRaises(ConfigurationError):
            load_settings(
                {
                    "KARI_STORAGE_BACKEND": "object_storage",
                    "KARI_OBJECT_STORAGE_BUCKET": "kari-media",
                    "KARI_OBJECT_STORAGE_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
                    "KARI_OBJECT_STORAGE_REGION": "us-east-005",
                    "KARI_OBJECT_STORAGE_ACCESS_KEY_ID": "access",
                    "KARI_OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret",
                    "KARI_OBJECT_STORAGE_PUBLIC_BASE_URL": "https://media.example.test/public?token=1",
                }
            )


if __name__ == "__main__":
    unittest.main()
