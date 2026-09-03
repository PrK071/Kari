from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path


APP_DATA_ROOT = Path(
    os.environ.get("LOCALAPPDATA")
    or (Path.home() / "AppData" / "Local")
) / "Kari"
STARTUP_LOG = APP_DATA_ROOT / "startup.log"


def load_runtime_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(APP_DATA_ROOT / ".env", override=False)
        load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
        if getattr(sys, "frozen", False):
            load_dotenv(Path(sys.executable).resolve().parent / ".env", override=False)
    except Exception:
        pass


load_runtime_env()


def url_has_kari_frontend(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.8) as response:
            page = response.read(8192).lower()
            return response.status == 200 and (
                b'id="root"' in page or b"id='root'" in page
            )
    except Exception:
        return False


def port_is_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def select_app_port() -> int:
    configured = os.environ.get("KARI_APP_PORT", "").strip()
    if configured:
        return int(configured)

    for candidate in (8000, *range(8765, 8800)):
        candidate_url = f"http://127.0.0.1:{candidate}"
        if url_has_kari_frontend(candidate_url) or port_is_free(candidate):
            return candidate
    raise RuntimeError("Nenhuma porta local disponivel para iniciar Kari.")


APP_PORT = select_app_port()
APP_URL = f"http://127.0.0.1:{APP_PORT}"


def log(message: str) -> None:
    APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with STARTUP_LOG.open("a", encoding="utf-8") as stream:
        stream.write(f"{message}\n")


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


def kari_is_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{APP_URL}/health", timeout=0.8) as response:
            health_ok = response.status == 200 and b'"status":"ok"' in response.read(256)
        return health_ok and url_has_kari_frontend(APP_URL)
    except Exception:
        return False


def open_browser_when_ready() -> None:
    for _ in range(120):
        if kari_is_ready():
            webbrowser.open(APP_URL)
            return
        time.sleep(0.25)
    log("Servidor nao ficou pronto em 30 segundos.")


def prepare_runtime() -> Path:
    app_data = APP_DATA_ROOT
    data_dir = app_data / "data"
    static_dir = app_data / "static"
    data_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)

    bundled_static = resource_path("backend/static")
    if bundled_static.exists():
        shutil.copytree(bundled_static, static_dir, dirs_exist_ok=True)

    bundled_cache = resource_path("seed_cache")
    for name in ("catalog.json", "chapters.json", "custom_catalog.json"):
        source = bundled_cache / name
        target = data_dir / name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)

    os.environ["KARI_DATA_DIR"] = str(data_dir)
    os.environ["KARI_STATIC_DIR"] = str(static_dir)
    os.environ["KARI_RUNTIME"] = "desktop"
    os.environ["KARI_BACKEND_URL"] = APP_URL
    os.environ["KARI_FRONTEND_URL"] = APP_URL

    load_runtime_env()

    return resource_path("frontend_dist")


def main() -> None:
    STARTUP_LOG.unlink(missing_ok=True)

    if kari_is_ready():
        log(f"Kari ja estava aberto em {APP_URL}.")
        if os.environ.get("KARI_NO_BROWSER") != "1":
            webbrowser.open(APP_URL)
        return

    log("Preparando arquivos...")
    frontend_dir = prepare_runtime()
    log(f"Frontend: {frontend_dir}")

    log("Importando servidor...")
    import uvicorn
    from fastapi.staticfiles import StaticFiles
    from backend.main import app
    log("Servidor importado.")

    if not frontend_dir.exists():
        raise RuntimeError(f"Frontend nao encontrado: {frontend_dir}")

    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="kari-frontend")
    if os.environ.get("KARI_NO_BROWSER") != "1":
        threading.Thread(target=open_browser_when_ready, daemon=True).start()
    log(f"Iniciando {APP_URL}")
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=APP_PORT,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        details = traceback.format_exc()
        log(details)
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"Kari nao conseguiu iniciar.\n\nVeja:\n{STARTUP_LOG}",
                "Kari",
                0x10,
            )
        except Exception:
            pass
        raise
