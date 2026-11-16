#!/usr/bin/env python3
"""
ObsVoiceClip - plugin/script para OBS Studio.

Escucha un comando de voz (helper Vosk), guarda los ultimos N segundos del
Replay Buffer de OBS y sube el clip a un repositorio publico de GitHub.
Incluye panel web/dock, overlay para OBS y actualizaciones desde GitHub.

Instalacion: Tools > Scripts > + y selecciona este archivo.
El panel se abre en http://127.0.0.1:17877/panel (o desde el dock de OBS).
"""

from __future__ import annotations

import base64
import ctypes
import http.client
import json
import logging
import logging.handlers
import os
import queue
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import obspython as obs

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

APP_NAME = "ObsVoiceClip"
VERSION = "1.0.0"
PLUGIN_REPO = "joseestebanmarinrincones-dot/obs-voiceclip"
PLUGIN_RELEASE_ASSET_PREFIX = "obs-voiceclip-"
DEFAULT_PORT = 17877
GITHUB_API = "https://api.github.com"
USER_AGENT = f"ObsVoiceClip/{VERSION}"

SCRIPT_DIR = Path(__file__).resolve().parent
WEB_DIR = SCRIPT_DIR / "web"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())) / APP_NAME
LOG_DIR = DATA_DIR / "logs"
MODELS_DIR = DATA_DIR / "models"
CONFIG_PATH = DATA_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "history.json"
LOG_PATH = LOG_DIR / "plugin.log"
HELPER_LOG_PATH = LOG_DIR / "voice_helper.log"

DURATION_OPTIONS = [10, 15, 20, 30, 45, 60, 90, 120, 180, 300]
DEFAULT_PHRASES_ES = [
    "clip",
    "guarda clip",
    "corta clip",
    "haz clip",
    "nuevo clip",
    "crea clip",
    "guarda el clip",
    "corta el clip",
]
DEFAULT_PHRASES_EN = [
    "clip",
    "save clip",
    "make clip",
    "new clip",
    "create clip",
    "cut clip",
]
MODELS = {
    "es": {
        "name": "vosk-model-small-es-0.42",
        "zip": "vosk-model-small-es-0.42.zip",
        "size": 39817833,
        "label": "Español (España)",
        "phrases": DEFAULT_PHRASES_ES,
        "urls": [
            "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip",
            f"https://github.com/{PLUGIN_REPO}/releases/download/models-v1/vosk-model-small-es-0.42.zip",
        ],
    },
    "en": {
        "name": "vosk-model-small-en-us-0.15",
        "zip": "vosk-model-small-en-us-0.15.zip",
        "size": 41925141,
        "label": "English (US)",
        "phrases": DEFAULT_PHRASES_EN,
        "urls": [
            "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
            f"https://github.com/{PLUGIN_REPO}/releases/download/models-v1/vosk-model-small-en-us-0.15.zip",
        ],
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "duration": 30,
    "auto_start_replay": True,
    "auto_size_replay": True,
    "auto_upload": True,
    "voice": {
        "enabled": True,
        "language": "es",
        "device": "default",
        "device_name": "",
        "sensitivity": 55,
        "cooldown": 4.0,
        "phrases": list(DEFAULT_PHRASES_ES),
    },
    "github": {
        "token": "",
        "owner": "",
        "repo": "obs-voiceclip-media",
        "make_public": True,
        "auto_create": True,
    },
    "overlay": {
        "position": "top-right",
        "show_voice": True,
        "show_partial": True,
        "source_name": "VoiceClip Overlay",
    },
    "server": {"port": DEFAULT_PORT},
    "update": {
        "repo": PLUGIN_REPO,
        "auto_check": True,
        "include_prerelease": False,
    },
    "install": {"dock_uuid": "", "last_window_url": ""},
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _setup_logging() -> logging.Logger:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
        root = logging.getLogger("voiceclip")
        root.setLevel(logging.INFO)
        root.handlers[:] = [handler]
        root.propagate = False
        return root
    except Exception:
        logger = logging.getLogger("voiceclip")
        logger.handlers[:] = [logging.NullHandler()]
        return logger


LOG = _setup_logging()


def log(message: str, level: int = logging.INFO) -> None:
    try:
        LOG.log(level, message)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Utilidades generales
# ---------------------------------------------------------------------------


def deep_merge(base: dict, override: dict | None) -> dict:
    result = json.loads(json.dumps(base))
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_json_file(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return fallback
    except Exception as exc:
        log(f"No se pudo leer {path}: {exc}", logging.WARNING)
        return fallback


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def load_config() -> dict:
    return deep_merge(DEFAULT_CONFIG, load_json_file(CONFIG_PATH, {}))


def save_config() -> None:
    global CONFIG
    try:
        atomic_write_json(CONFIG_PATH, CONFIG)
    except Exception as exc:
        log(f"No se pudo guardar la configuracion: {exc}", logging.ERROR)


def load_history() -> list[dict]:
    data = load_json_file(HISTORY_PATH, [])
    return data if isinstance(data, list) else []


def save_history(items: list[dict]) -> None:
    try:
        atomic_write_json(HISTORY_PATH, items[:30])
    except Exception as exc:
        log(f"No se pudo guardar el historial: {exc}", logging.ERROR)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_phrases(raw: Any) -> list[str]:
    if isinstance(raw, list):
        items = raw
    else:
        items = re.split(r"[,;\n]+", str(raw or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        phrase = normalize_text(str(item))
        if phrase and phrase not in seen:
            seen.add(phrase)
            result.append(phrase)
    return result


def version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", (value or "").lstrip("vV"))
    return tuple(int(part) for part in parts[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    a = version_tuple(latest)
    b = version_tuple(current)
    length = max(len(a), len(b))
    return a + (0,) * (length - len(a)) > b + (0,) * (length - len(b))


def human_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def find_free_port(preferred: int, host: str = "127.0.0.1") -> int:
    for port in range(int(preferred), int(preferred) + 40):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                return port
        except OSError:
            continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def is_our_process(pid: int) -> bool:
    if os.name != "nt":
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def message_box(title: str, text: str, yes_no: bool = False) -> bool:
    if os.name != "nt":
        log(f"{title}: {text}")
        return False
    flags = 0x00000040 | 0x00040000 | 0x00010000  # ICONINFO | TOPMOST | SETFOREGROUND
    if yes_no:
        flags |= 0x00000004  # YESNO
    else:
        flags |= 0x00000000  # OK
    try:
        result = ctypes.windll.user32.MessageBoxW(None, text, title, flags)
        return result == 6  # IDYES
    except Exception as exc:
        log(f"No se pudo mostrar MessageBox: {exc}", logging.ERROR)
        return False


def open_path(path: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import webbrowser

            webbrowser.open(path)
    except Exception as exc:
        log(f"No se pudo abrir {path}: {exc}", logging.WARNING)


# ---------------------------------------------------------------------------
# HTTP local sin dependencias externas
# ---------------------------------------------------------------------------

LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
GITHUB_SSL = ssl.create_default_context()


class HTTPErrorInfo(Exception):
    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


def local_request(
    method: str,
    url: str,
    payload: dict | None = None,
    token: str = "",
    timeout: float = 3.0,
) -> dict:
    data = None
    headers = {"User-Agent": USER_AGENT}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["X-VoiceClip-Token"] = token
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with LOCAL_OPENER.open(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw.decode("utf-8")).get("error")
        except Exception:
            detail = raw.decode("utf-8", "replace")[:300]
        raise HTTPErrorInfo(detail or f"HTTP {exc.code}", exc.code, url) from exc
    except Exception as exc:
        raise HTTPErrorInfo(str(exc), None, url) from exc


# ---------------------------------------------------------------------------
# Estado compartido / SSE
# ---------------------------------------------------------------------------


class StateHub:
    def __init__(self):
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {}
        self.clients: set[queue.Queue] = set()
        self._last_broadcast = 0.0
        self._pending = False
        self.server_port = DEFAULT_PORT
        self._notices: list[dict] = []
        self._history: list[dict] = load_history()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.data = {
                "app": APP_NAME,
                "version": VERSION,
                "now": time.time(),
                "server": {
                    "port": self.server_port,
                    "panel_url": f"http://127.0.0.1:{self.server_port}/panel",
                    "overlay_url": f"http://127.0.0.1:{self.server_port}/overlay",
                    "helper_url": "",
                },
                "settings": {},
                "obs": {
                    "profile": "",
                    "scene": "",
                    "recording": False,
                    "streaming": False,
                    "replay_active": False,
                },
                "replay": {
                    "output_exists": False,
                    "configured": False,
                    "active": False,
                    "duration": 30,
                    "restart_required": False,
                    "restarting": False,
                },
                "voice": {
                    "enabled": False,
                    "state": "off",
                    "device": "default",
                    "device_id": "",
                    "level": 0.0,
                    "speaking": False,
                    "partial": "",
                    "last_command": "",
                    "last_command_at": 0,
                    "error": None,
                    "helper_running": False,
                    "helper_pid": 0,
                    "model": "",
                },
                "model": {
                    "language": "es",
                    "name": MODELS["es"]["name"],
                    "label": MODELS["es"]["label"],
                    "ready": False,
                    "downloading": False,
                    "progress": 0.0,
                    "downloaded": 0,
                    "total": MODELS["es"]["size"],
                    "error": None,
                    "path": "",
                    "log_path": str(HELPER_LOG_PATH),
                },
                "clip": {
                    "phase": "idle",
                    "duration": 30,
                    "path": "",
                    "filename": "",
                    "size": 0,
                    "url": "",
                    "repo_url": "",
                    "error": None,
                    "saved_at": 0,
                    "uploaded_at": 0,
                    "voice_text": "",
                },
                "history": [],
                "github": {
                    "configured": False,
                    "verified": False,
                    "login": "",
                    "owner": "",
                    "repo": "",
                    "repo_url": "",
                    "private": None,
                    "error": None,
                    "last_upload_url": "",
                },
                "update": {
                    "available": False,
                    "current": VERSION,
                    "latest": "",
                    "notes": "",
                    "url": "",
                    "asset_url": "",
                    "published_at": "",
                    "checked_at": 0,
                    "installing": False,
                    "progress": 0.0,
                    "error": None,
                },
                "overlay": {
                    "position": "top-right",
                    "show_voice": True,
                    "show_partial": True,
                },
                "notices": [],
                "log_path": str(LOG_PATH),
            }
            self._notices = []
            self._history = load_history()
            self.data["history"] = list(self._history)

    # -- configuracion visible ------------------------------------------
    def set_settings(self, config: dict, server_port: int) -> None:
        token = str(config.get("github", {}).get("token") or "")
        owner = str(config.get("github", {}).get("owner") or "")
        repo = str(config.get("github", {}).get("repo") or "")
        model_info = MODELS.get(config.get("voice", {}).get("language", "es"), MODELS["es"])
        with self.lock:
            self.server_port = server_port
            self.data["settings"] = {
                "duration": int(config.get("duration", 30)),
                "durations": list(DURATION_OPTIONS),
                "auto_start_replay": bool(config.get("auto_start_replay", True)),
                "auto_size_replay": bool(config.get("auto_size_replay", True)),
                "auto_upload": bool(config.get("auto_upload", True)),
                "voice_enabled": bool(config.get("voice", {}).get("enabled", True)),
                "voice_language": str(config.get("voice", {}).get("language", "es")),
                "voice_language_label": str(model_info.get("label", "Espanol")),
                "voice_device": str(config.get("voice", {}).get("device", "default")),
                "voice_device_name": str(config.get("voice", {}).get("device_name", "")),
                "voice_sensitivity": int(config.get("voice", {}).get("sensitivity", 55)),
                "voice_cooldown": float(config.get("voice", {}).get("cooldown", 4.0)),
                "voice_phrases": list(config.get("voice", {}).get("phrases", [])),
                "github_configured": bool(token and repo),
                "github_token_tail": token[-4:] if token else "",
                "github_owner": owner,
                "github_repo": repo,
                "github_public": bool(config.get("github", {}).get("make_public", True)),
                "github_repo_url": f"https://github.com/{owner}/{repo}" if owner and repo else "",
                "overlay_position": str(config.get("overlay", {}).get("position", "top-right")),
                "overlay_show_voice": bool(config.get("overlay", {}).get("show_voice", True)),
                "overlay_show_partial": bool(config.get("overlay", {}).get("show_partial", True)),
            }
            self.data["server"]["port"] = server_port
            self.data["server"]["panel_url"] = f"http://127.0.0.1:{server_port}/panel"
            self.data["server"]["overlay_url"] = f"http://127.0.0.1:{server_port}/overlay"
            self.data["model"]["language"] = str(config.get("voice", {}).get("language", "es"))
            self.data["model"]["name"] = model_info["name"]
            self.data["model"]["label"] = model_info["label"]
            self.data["overlay"] = {
                "position": str(config.get("overlay", {}).get("position", "top-right")),
                "show_voice": bool(config.get("overlay", {}).get("show_voice", True)),
                "show_partial": bool(config.get("overlay", {}).get("show_partial", True)),
            }
        self.refresh_github(config)
        self.refresh_model(config)

    def refresh_github(self, config: dict) -> None:
        token = str(config.get("github", {}).get("token") or "")
        repo = str(config.get("github", {}).get("repo") or "")
        owner = str(config.get("github", {}).get("owner") or "")
        with self.lock:
            self.data["github"].update(
                {
                    "configured": bool(token and repo),
                    "owner": owner,
                    "repo": repo,
                    "repo_url": f"https://github.com/{owner}/{repo}" if owner and repo else "",
                }
            )
            if not token:
                self.data["github"]["verified"] = False

    def refresh_model(self, config: dict) -> None:
        language = str(config.get("voice", {}).get("language", "es"))
        info = MODELS.get(language, MODELS["es"])
        path = MODELS_DIR / info["name"]
        ready = (path / "am" / "final.mdl").is_file()
        with self.lock:
            self.data["model"].update(
                {
                    "language": language,
                    "name": info["name"],
                    "label": info["label"],
                    "ready": ready,
                    "path": str(path) if ready else "",
                    "total": int(info.get("size", 0)),
                }
            )

    def add_notice(self, text: str, kind: str = "info", url: str = "") -> None:
        notice = {
            "id": f"{int(time.time() * 1000)}-{secrets.token_hex(3)}",
            "kind": kind,
            "text": text,
            "url": url or "",
            "at": time.time(),
        }
        with self.lock:
            self._notices.insert(0, notice)
            self._notices = self._notices[:20]
            self.data["notices"] = list(self._notices)
        log(f"AVISO [{kind}] {text}")
        self.broadcast(force=True)

    def get_notices(self) -> list[dict]:
        with self.lock:
            return list(self._notices)

    def set_history(self, items: list[dict]) -> None:
        with self.lock:
            self._history = list(items[:30])
            self.data["history"] = list(self._history)

    def add_history(self, item: dict) -> None:
        with self.lock:
            self._history.insert(0, item)
            self._history = self._history[:30]
            self.data["history"] = list(self._history)
        save_history(self._history)

    def snapshot(self) -> dict:
        with self.lock:
            self.data["now"] = time.time()
            return json.loads(json.dumps(self.data, ensure_ascii=False))

    def update(self, section: str | None = None, force: bool = False, **values) -> None:
        with self.lock:
            target = self.data if section is None else self.data.setdefault(section, {})
            target.update(values)
        self.broadcast(force=force)

    def update_nested(self, section: str, values: dict, force: bool = False) -> None:
        with self.lock:
            self.data.setdefault(section, {}).update(values)
        self.broadcast(force=force)

    def broadcast(self, force: bool = False) -> None:
        now = time.time()
        with self.lock:
            if not force and now - self._last_broadcast < 0.12:
                self._pending = True
                return
            self._last_broadcast = now
            self.data["now"] = now
            payload = json.dumps(self.data, ensure_ascii=False)
            clients = list(self.clients)
        for client in clients:
            try:
                client.put_nowait(payload)
            except queue.Full:
                try:
                    client.get_nowait()
                    client.put_nowait(payload)
                except Exception:
                    pass

    def flush_pending(self) -> None:
        with self.lock:
            pending = self._pending
            self._pending = False
        if pending:
            self.broadcast(force=True)

    def has_clients(self) -> bool:
        with self.lock:
            return bool(self.clients)

    def add_client(self, client: queue.Queue) -> None:
        with self.lock:
            self.clients.add(client)

    def remove_client(self, client: queue.Queue) -> None:
        with self.lock:
            self.clients.discard(client)


STATE = StateHub()
CONFIG: dict = load_config()
UNLOADING = False
LOADED = False
OBS_SETTINGS = None
EVENT_CALLBACK = None
SERVER: "LocalServer | None" = None
HELPER: "HelperProcess | None" = None
GITHUB: "GitHubClient | None" = None
UPDATE: "UpdateManager | None" = None
MODEL_DOWNLOADER: "ModelDownloader | None" = None
ACTIONS: queue.Queue = queue.Queue()
ACTION_LOCK = threading.Lock()
CLIP_LOCK = threading.Lock()
TICK_LAST = 0.0
PENDING_CLIP_PATH = ""
PENDING_CLIP_UNTIL = 0.0
REPLAY_RESTART_AT = 0.0
STATE_UPDATE_LAST = 0.0
AUTO_UPDATE_PROMPTED = False


# ---------------------------------------------------------------------------
# Helper de voz
# ---------------------------------------------------------------------------


class HelperProcess:
    def __init__(self):
        self.lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.token = ""
        self.last_error = ""
        self.starting = False
        self._monitor: threading.Thread | None = None

    def helper_command(self) -> list[str] | None:
        exe = SCRIPT_DIR / "helper" / "voice_helper.exe"
        if exe.is_file():
            return [str(exe)]
        script = SCRIPT_DIR / "helper" / "voice_helper.py"
        if not script.is_file():
            return None
        candidates = [
            SCRIPT_DIR / "helper" / "python.exe",
            Path(sys.executable) if getattr(sys, "frozen", False) is False else None,
            Path(shutil.which("pythonw.exe") or ""),
            Path(shutil.which("python.exe") or ""),
            Path(shutil.which("python") or ""),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                name = Path(candidate).name.lower()
                if name.startswith("obs") or name.startswith("obs64"):
                    continue
                return [str(candidate), str(script)]
        return None

    def is_running(self) -> bool:
        with self.lock:
            return self.proc is not None and self.proc.poll() is None

    def start_async(self, config: dict) -> None:
        with self.lock:
            if self.starting:
                return
            self.starting = True
        threading.Thread(target=self._start_worker, args=(config,), daemon=True, name="VoiceHelperStart").start()

    def _start_worker(self, config: dict) -> None:
        try:
            self.start(config)
        finally:
            with self.lock:
                self.starting = False

    def start(self, config: dict) -> bool:
        self.stop(wait=True)
        if UNLOADING:
            return False
        voice = config.get("voice", {})
        language = str(voice.get("language", "es"))
        info = MODELS.get(language, MODELS["es"])
        model_path = MODELS_DIR / info["name"]
        if not (model_path / "am" / "final.mdl").is_file():
            self.last_error = "Modelo de voz no descargado"
            STATE.update_nested(
                "voice",
                {
                    "state": "model_missing",
                    "error": self.last_error,
                    "helper_running": False,
                    "helper_pid": 0,
                    "model": str(model_path),
                },
            )
            return False
        command = self.helper_command()
        if not command:
            self.last_error = "No se encontro voice_helper.exe ni Python para ejecutarlo"
            STATE.update_nested(
                "voice", {"state": "error", "error": self.last_error, "helper_running": False, "helper_pid": 0}
            )
            return False
        port = find_free_port(17878)
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.port = port
            self.token = token
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            helper_log = HELPER_LOG_PATH.open("a", encoding="utf-8")
            creationflags = 0x08000000 if os.name == "nt" else 0
            cmd = list(command) + [
                "--port",
                str(port),
                "--token",
                token,
                "--model",
                str(model_path),
                "--device",
                str(voice.get("device") or "default"),
                "--phrases",
                ",".join(parse_phrases(voice.get("phrases")) or info["phrases"]),
                "--sensitivity",
                str(int(voice.get("sensitivity", 55))),
                "--cooldown",
                str(float(voice.get("cooldown", 4.0))),
                "--parent-pid",
                str(os.getpid()),
                "--callback",
                f"http://127.0.0.1:{STATE.server_port}/api/voice/event",
                "--log",
                str(HELPER_LOG_PATH),
            ]
            log(f"Iniciando helper: {' '.join(cmd[:3])} ... port={port}")
            proc = subprocess.Popen(
                cmd,
                cwd=str(SCRIPT_DIR),
                stdin=subprocess.DEVNULL,
                stdout=helper_log,
                stderr=helper_log,
                creationflags=creationflags,
                close_fds=True,
            )
            helper_log.close()
            with self.lock:
                self.proc = proc
            STATE.update_nested(
                "voice",
                {
                    "state": "starting",
                    "helper_running": True,
                    "helper_pid": proc.pid,
                    "model": str(model_path),
                    "error": None,
                },
            )
            STATE.update_nested("server", {"helper_url": f"http://127.0.0.1:{port}"})
            # Esperar la API (el modelo puede tardar unos segundos en cargar).
            deadline = time.time() + 15.0
            while time.time() < deadline and not UNLOADING:
                if proc.poll() is not None:
                    break
                try:
                    health = local_request("GET", f"http://127.0.0.1:{port}/health", timeout=1.0)
                    self.apply_health(health)
                    log(f"Helper listo en http://127.0.0.1:{port}")
                    return True
                except Exception:
                    time.sleep(0.25)
            if proc.poll() is not None:
                self.last_error = f"El helper termino con codigo {proc.returncode}. Revisa {HELPER_LOG_PATH}"
            else:
                self.last_error = "El helper no respondio a tiempo"
            log(self.last_error, logging.ERROR)
            self.stop(wait=True)
            STATE.update_nested(
                "voice",
                {"state": "error", "error": self.last_error, "helper_running": False, "helper_pid": 0},
            )
            return False
        except Exception as exc:
            self.last_error = f"No se pudo iniciar el helper: {exc}"
            log(self.last_error, logging.ERROR)
            self.stop(wait=True)
            STATE.update_nested(
                "voice",
                {"state": "error", "error": self.last_error, "helper_running": False, "helper_pid": 0},
            )
            return False

    def stop(self, wait: bool = False) -> None:
        with self.lock:
            proc = self.proc
            token = self.token
            port = self.port
            self.proc = None
            self.token = ""
            self.port = 0
        if proc is None:
            return
        try:
            if proc.poll() is None and token and port:
                try:
                    local_request(
                        "POST",
                        f"http://127.0.0.1:{port}/shutdown",
                        payload={},
                        token=token,
                        timeout=1.5,
                    )
                except Exception:
                    pass
            if wait:
                try:
                    proc.wait(timeout=3.0)
                except Exception:
                    try:
                        proc.terminate()
                        proc.wait(timeout=2.0)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
        finally:
            STATE.update_nested("voice", {"helper_running": False, "helper_pid": 0})
            STATE.update_nested("server", {"helper_url": ""})

    def request(self, method: str, path: str, payload: dict | None = None, timeout: float = 3.0) -> dict:
        with self.lock:
            port = self.port
            token = self.token
        if not port or not token:
            raise HTTPErrorInfo("El helper de voz no esta iniciado")
        return local_request(method, f"http://127.0.0.1:{port}{path}", payload, token, timeout)

    def health(self, timeout: float = 1.0) -> dict:
        return self.request("GET", "/health", timeout=timeout)

    def devices(self) -> list[dict]:
        data = self.request("GET", "/devices", timeout=8.0)
        items = data.get("devices")
        return items if isinstance(items, list) else []

    def configure(self, config: dict) -> None:
        voice = config.get("voice", {})
        self.request(
            "POST",
            "/config",
            {
                "device": str(voice.get("device") or "default"),
                "triggers": parse_phrases(voice.get("phrases")),
                "sensitivity": int(voice.get("sensitivity", 55)),
                "cooldown": float(voice.get("cooldown", 4.0)),
            },
            timeout=5.0,
        )

    def apply_health(self, health: dict) -> None:
        if not isinstance(health, dict):
            return
        state = str(health.get("state") or "error")
        values = {
            "enabled": bool(CONFIG.get("voice", {}).get("enabled", True)),
            "state": state,
            "device": str(health.get("device") or ""),
            "device_id": str(health.get("device_id") or ""),
            "level": float(health.get("level") or 0.0),
            "speaking": bool(health.get("speaking")),
            "partial": str(health.get("partial") or ""),
            "last_command": str(health.get("last_command") or ""),
            "last_command_at": float(health.get("last_command_at") or 0.0),
            "error": health.get("error"),
            "helper_running": self.is_running(),
            "helper_pid": self.proc.pid if self.proc else 0,
            "model": str(health.get("model") or ""),
        }
        old = STATE.snapshot().get("voice", {})
        changed = any(old.get(key) != value for key, value in values.items() if key not in ("level",))
        STATE.update_nested("voice", values, force=changed)

    def poll_once(self) -> None:
        if not self.is_running():
            state = STATE.snapshot().get("voice", {}).get("state")
            if state not in ("off", "error", "model_missing"):
                STATE.update_nested(
                    "voice",
                    {
                        "state": "error",
                        "error": self.last_error or "El helper de voz se detuvo",
                        "helper_running": False,
                        "helper_pid": 0,
                    },
                    force=True,
                )
            return
        try:
            self.apply_health(self.health(timeout=1.2))
        except Exception as exc:
            log(f"Health del helper fallo: {exc}", logging.WARNING)


# ---------------------------------------------------------------------------
# Descarga del modelo Vosk
# ---------------------------------------------------------------------------


class ModelDownloader:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False

    def ensure_async(self, language: str) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
        threading.Thread(target=self._run, args=(language,), daemon=True, name="ModelDownloader").start()

    def _run(self, language: str) -> None:
        try:
            info = MODELS.get(language, MODELS["es"])
            target = MODELS_DIR / info["name"]
            if (target / "am" / "final.mdl").is_file():
                STATE.update_nested(
                    "model",
                    {
                        "ready": True,
                        "downloading": False,
                        "progress": 100.0,
                        "error": None,
                        "path": str(target),
                    },
                    force=True,
                )
                return
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            part = MODELS_DIR / (info["zip"] + ".part")
            last_error = ""
            for url in info.get("urls", []):
                try:
                    log(f"Descargando modelo {language}: {url}")
                    STATE.update_nested(
                        "model",
                        {
                            "ready": False,
                            "downloading": True,
                            "progress": 0.0,
                            "downloaded": 0,
                            "error": None,
                            "language": language,
                            "name": info["name"],
                            "label": info["label"],
                            "total": int(info.get("size", 0)),
                        },
                        force=True,
                    )
                    self._download(url, part, int(info.get("size", 0)))
                    self._extract(part, target, info["name"])
                    try:
                        part.unlink(missing_ok=True)
                    except Exception:
                        pass
                    STATE.update_nested(
                        "model",
                        {
                            "ready": True,
                            "downloading": False,
                            "progress": 100.0,
                            "error": None,
                            "path": str(target),
                        },
                        force=True,
                    )
                    STATE.add_notice("Modelo de voz listo.", "success")
                    if CONFIG.get("voice", {}).get("enabled", True):
                        restart_voice()
                    return
                except Exception as exc:
                    last_error = str(exc)
                    log(f"Fallo descargando {url}: {exc}", logging.ERROR)
                    try:
                        part.unlink(missing_ok=True)
                    except Exception:
                        pass
            STATE.update_nested(
                "model",
                {
                    "ready": False,
                    "downloading": False,
                    "error": last_error or "No se pudo descargar el modelo",
                },
                force=True,
            )
            STATE.add_notice(f"No se pudo descargar el modelo de voz: {last_error}", "error")
        finally:
            with self.lock:
                self.running = False

    def _download(self, url: str, part: Path, expected: int) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        last_emit = 0.0
        with urllib.request.urlopen(request, timeout=40) as response:
            total = int(response.headers.get("Content-Length") or expected or 0)
            downloaded = 0
            with part.open("wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_emit >= 0.25:
                        last_emit = now
                        percent = (downloaded / total * 100.0) if total else 0.0
                        STATE.update_nested(
                            "model",
                            {
                                "downloading": True,
                                "downloaded": downloaded,
                                "total": total,
                                "progress": round(min(99.5, percent), 1),
                            },
                        )
        if part.stat().st_size < 1024:
            raise RuntimeError("La descarga quedo vacia")

    def _extract(self, part: Path, target: Path, expected_name: str) -> None:
        temp_dir = MODELS_DIR / f".extract-{int(time.time())}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(part) as archive:
                for name in archive.namelist():
                    normalized = name.replace("\\", "/")
                    if normalized.startswith("/") or ".." in Path(normalized).parts:
                        raise RuntimeError("ZIP con rutas no seguras")
                archive.extractall(temp_dir)
            candidates = [p for p in temp_dir.iterdir() if p.is_dir()]
            source = None
            for candidate in candidates:
                if candidate.name == expected_name or (candidate / "am" / "final.mdl").is_file():
                    source = candidate
                    break
            if source is None and (temp_dir / "am" / "final.mdl").is_file():
                source = temp_dir
            if source is None or not (source / "am" / "final.mdl").is_file():
                raise RuntimeError("El ZIP no contiene un modelo Vosk valido")
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(source), str(target))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


class GitHubClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.repo_cache: dict[str, Any] = {}
        self.repo_cache_at = 0.0

    def token(self) -> str:
        return str(CONFIG.get("github", {}).get("token") or "").strip()

    def repo_name(self) -> str:
        return str(CONFIG.get("github", {}).get("repo") or "obs-voiceclip-media").strip()

    def owner(self) -> str:
        return str(CONFIG.get("github", {}).get("owner") or "").strip()

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if extra:
            headers.update(extra)
        return headers

    def api(self, method: str, path: str, payload: dict | None = None, timeout: float = 40.0) -> Any:
        if not self.token():
            raise HTTPErrorInfo("Falta el token de GitHub")
        data = None
        headers = self._headers()
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(GITHUB_API + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=GITHUB_SSL) as response:
                raw = response.read()
                if not raw:
                    return {}
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                obj = json.loads(raw.decode("utf-8"))
                detail = obj.get("message") or str(obj)
                errors = obj.get("errors")
                if errors:
                    detail += " " + json.dumps(errors, ensure_ascii=False)
            except Exception:
                detail = raw.decode("utf-8", "replace")[:500]
            raise HTTPErrorInfo(f"GitHub HTTP {exc.code}: {detail}", exc.code, GITHUB_API + path) from exc
        except Exception as exc:
            raise HTTPErrorInfo(f"No se pudo conectar con GitHub: {exc}", None, GITHUB_API + path) from exc

    def whoami(self) -> dict:
        user = self.api("GET", "/user")
        if not isinstance(user, dict) or not user.get("login"):
            raise HTTPErrorInfo("GitHub no devolvio el usuario")
        login = str(user["login"])
        with STATE.lock:
            STATE.data["github"]["login"] = login
            if not STATE.data["github"].get("owner"):
                STATE.data["github"]["owner"] = login
        if not CONFIG.get("github", {}).get("owner"):
            CONFIG["github"]["owner"] = login
            save_config()
            STATE.set_settings(CONFIG, STATE.server_port)
        return user

    def ensure_repo(self, force: bool = False) -> dict:
        with self.lock:
            if not force and self.repo_cache and time.time() - self.repo_cache_at < 120:
                return self.repo_cache
        user = self.whoami()
        login = str(user.get("login") or "")
        owner = self.owner() or login
        repo = self.repo_name()
        if not repo:
            raise HTTPErrorInfo("Falta el nombre del repositorio de clips")
        if not CONFIG.get("github", {}).get("owner"):
            CONFIG["github"]["owner"] = owner
            save_config()
        repo_path = f"/repos/{owner}/{repo}"
        created = False
        try:
            data = self.api("GET", repo_path)
        except HTTPErrorInfo as exc:
            if exc.status != 404:
                raise
            if not CONFIG.get("github", {}).get("auto_create", True):
                raise HTTPErrorInfo(
                    f"El repositorio {owner}/{repo} no existe. Creelo o activa la creacion automatica.",
                    404,
                ) from exc
            payload = {
                "name": repo,
                "description": "Clips subidos automaticamente por ObsVoiceClip",
                "private": not bool(CONFIG.get("github", {}).get("make_public", True)),
                "auto_init": True,
                "has_issues": False,
                "has_wiki": False,
                "has_projects": False,
            }
            if owner.lower() == login.lower():
                data = self.api("POST", "/user/repos", payload)
            else:
                data = self.api("POST", f"/orgs/{owner}/repos", payload)
            created = True
            time.sleep(1.5)
            data = data if isinstance(data, dict) else {}
            try:
                data = self.api("GET", repo_path)
            except Exception:
                pass
        if not isinstance(data, dict):
            data = {}
        if data.get("private") and CONFIG.get("github", {}).get("make_public", True):
            try:
                data = self.api("PATCH", repo_path, {"private": False})
                STATE.add_notice("El repositorio de clips se ha hecho publico.", "info")
            except Exception as exc:
                raise HTTPErrorInfo(
                    f"El repositorio {owner}/{repo} es privado y no se pudo hacer publico: {exc}"
                ) from exc
        # Asegurar que hay un commit inicial para poder crear releases.
        if created:
            try:
                self.api("PUT", f"{repo_path}/contents/README.md", {
                    "message": "Initial commit - ObsVoiceClip",
                    "content": base64.b64encode(
                        b"# ObsVoiceClip clips\n\nClips publicos generados con ObsVoiceClip.\n"
                    ).decode("ascii"),
                })
            except HTTPErrorInfo:
                pass
        info = {
            "owner": owner,
            "repo": repo,
            "html_url": str(data.get("html_url") or f"https://github.com/{owner}/{repo}"),
            "private": bool(data.get("private")),
            "full_name": str(data.get("full_name") or f"{owner}/{repo}"),
        }
        with self.lock:
            self.repo_cache = info
            self.repo_cache_at = time.time()
        with STATE.lock:
            STATE.data["github"].update(
                {
                    "configured": bool(self.token()),
                    "verified": True,
                    "owner": owner,
                    "repo": repo,
                    "repo_url": info["html_url"],
                    "private": info["private"],
                    "error": None,
                }
            )
        STATE.broadcast(force=True)
        return info

    def test_connection(self) -> dict:
        try:
            info = self.ensure_repo(force=True)
            STATE.add_notice(
                f"GitHub conectado: {info['full_name']} ({'privado' if info['private'] else 'publico'})",
                "success",
                info["html_url"],
            )
            return info
        except Exception as exc:
            with STATE.lock:
                STATE.data["github"].update({"verified": False, "error": str(exc)})
            STATE.add_notice(f"GitHub: {exc}", "error")
            STATE.broadcast(force=True)
            raise

    def upload_clip(self, path: Path, duration: int, voice_text: str = "") -> dict:
        info = self.ensure_repo()
        owner = info["owner"]
        repo = info["repo"]
        size = path.stat().st_size
        if size <= 0:
            raise HTTPErrorInfo("El archivo del clip esta vacio")
        if size > 2 * 1024 * 1024 * 1024:
            raise HTTPErrorInfo("El clip supera el limite de 2 GB por archivo de GitHub")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime()) + "-" + secrets.token_hex(2)
        tag = f"clip-{stamp}"
        title = f"Clip {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"
        body_lines = [
            "Clip generado automaticamente por **ObsVoiceClip**.",
            "",
            f"- Duracion configurada: **{duration} s**",
            f"- Archivo: `{path.name}`",
            f"- Tamano: **{human_size(size)}**",
        ]
        if voice_text:
            body_lines.append(f"- Comando de voz: `{voice_text}`")
        release = self.api(
            "POST",
            f"/repos/{owner}/{repo}/releases",
            {
                "tag_name": tag,
                "name": title,
                "body": "\n".join(body_lines),
                "draft": False,
                "prerelease": False,
                "make_latest": "false",
            },
        )
        release_id = release.get("id")
        if not release_id:
            raise HTTPErrorInfo("GitHub no devolvio el ID del release")
        asset_name = self.asset_name(path, stamp)
        try:
            asset = self._upload_asset(owner, repo, int(release_id), asset_name, path)
        except Exception:
            try:
                self.api("DELETE", f"/repos/{owner}/{repo}/releases/{release_id}")
            except Exception:
                pass
            raise
        result = {
            "url": str(asset.get("browser_download_url") or ""),
            "release_url": str(release.get("html_url") or ""),
            "repo_url": info["html_url"],
            "asset": asset_name,
            "tag": tag,
            "size": size,
        }
        with STATE.lock:
            STATE.data["github"].update(
                {
                    "last_upload_url": result["url"],
                    "verified": True,
                    "error": None,
                    "repo_url": info["html_url"],
                }
            )
        return result

    def asset_name(self, path: Path, stamp: str) -> str:
        suffix = path.suffix.lower() or ".mp4"
        return f"voiceclip-{stamp}{suffix}"

    def _upload_asset(self, owner: str, repo: str, release_id: int, name: str, path: Path) -> dict:
        size = path.stat().st_size
        content_type = "video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream"
        query = urllib.parse.urlencode({"name": name})
        request_path = f"/repos/{owner}/{repo}/releases/{release_id}/assets?{query}"
        connection = http.client.HTTPSConnection("uploads.github.com", timeout=180, context=GITHUB_SSL)
        try:
            connection.putrequest("POST", request_path)
            for key, value in self._headers(
                {
                    "Content-Type": content_type,
                    "Content-Length": str(size),
                }
            ).items():
                connection.putheader(key, value)
            connection.endheaders()
            with path.open("rb") as handle:
                sent = 0
                last = 0.0
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    connection.send(chunk)
                    sent += len(chunk)
                    now = time.time()
                    if now - last > 0.4:
                        last = now
                        STATE.update_nested("update", {})  # no-op, evita bloqueos
                        with STATE.lock:
                            STATE.data["clip"]["upload_progress"] = round(sent / size * 100.0, 1)
                        STATE.broadcast()
            response = connection.getresponse()
            raw = response.read()
            if response.status not in (200, 201):
                detail = raw.decode("utf-8", "replace")[:500]
                raise HTTPErrorInfo(f"Error subiendo el clip: HTTP {response.status} {detail}", response.status)
            data = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(data, dict):
                raise HTTPErrorInfo("Respuesta invalida al subir el clip")
            return data
        finally:
            connection.close()


# ---------------------------------------------------------------------------
# Actualizaciones
# ---------------------------------------------------------------------------


class UpdateManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.checking = False
        self.installing = False
        self.last_prompt_version = ""
        self.first_prompt_done = False

    def check_async(self, manual: bool = False, prompt: bool = False) -> None:
        with self.lock:
            if self.checking:
                return
            self.checking = True
        threading.Thread(target=self._check_worker, args=(manual, prompt), daemon=True, name="UpdateCheck").start()

    def _check_worker(self, manual: bool, prompt: bool) -> None:
        try:
            repo = str(CONFIG.get("update", {}).get("repo") or PLUGIN_REPO)
            url = f"{GITHUB_API}/repos/{repo}/releases/latest"
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            }
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=20, context=GITHUB_SSL) as response:
                release = json.loads(response.read().decode("utf-8"))
            tag = str(release.get("tag_name") or "").strip()
            latest = tag.lstrip("vV")
            available = is_newer(latest, VERSION) and latest != VERSION
            asset_url = ""
            for asset in release.get("assets") or []:
                name = str(asset.get("name") or "")
                if name.lower().endswith(".zip") and (
                    name.startswith(PLUGIN_RELEASE_ASSET_PREFIX) or "voiceclip" in name.lower()
                ):
                    asset_url = str(asset.get("browser_download_url") or "")
                    break
            if not asset_url:
                for asset in release.get("assets") or []:
                    if str(asset.get("name") or "").lower().endswith(".zip"):
                        asset_url = str(asset.get("browser_download_url") or "")
                        break
            values = {
                "available": bool(available and asset_url),
                "latest": latest,
                "notes": str(release.get("body") or "")[:4000],
                "url": str(release.get("html_url") or ""),
                "asset_url": asset_url,
                "published_at": str(release.get("published_at") or ""),
                "checked_at": time.time(),
                "error": None,
            }
            STATE.update_nested("update", values, force=True)
            if manual:
                if available and asset_url:
                    STATE.add_notice(f"Actualizacion disponible: v{latest}", "info", values["url"])
                else:
                    STATE.add_notice(f"Ya tienes la ultima version (v{VERSION}).", "success")
            if prompt and available and asset_url and not UNLOADING:
                self._prompt(latest, values)
        except Exception as exc:
            log(f"No se pudo comprobar actualizaciones: {exc}", logging.WARNING)
            STATE.update_nested("update", {"checked_at": time.time(), "error": str(exc)}, force=True)
            if manual:
                STATE.add_notice(f"No se pudo comprobar actualizaciones: {exc}", "error")
        finally:
            with self.lock:
                self.checking = False

    def _prompt(self, latest: str, values: dict) -> None:
        with self.lock:
            if self.last_prompt_version == latest and self.first_prompt_done:
                return
            self.last_prompt_version = latest
            self.first_prompt_done = True
        text = (
            f"Hay una nueva version de ObsVoiceClip disponible.\n\n"
            f"Instalada: v{VERSION}\n"
            f"Disponible: v{latest}\n\n"
            "¿Quieres descargarla e instalarla ahora?\n"
            "OBS se reiniciara para aplicar los cambios."
        )
        if message_box("ObsVoiceClip - Actualizacion disponible", text, yes_no=True):
            self.install_async()
        else:
            STATE.add_notice(
                f"Actualizacion v{latest} disponible. Puedes instalarla desde el panel.",
                "info",
                values.get("url") or "",
            )

    def install_async(self) -> None:
        with self.lock:
            if self.installing:
                return
            self.installing = True
        threading.Thread(target=self._install_worker, daemon=True, name="UpdateInstall").start()

    def _install_worker(self) -> None:
        try:
            snapshot = STATE.snapshot().get("update", {})
            url = str(snapshot.get("asset_url") or "")
            latest = str(snapshot.get("latest") or "")
            if not url:
                raise RuntimeError("No hay un ZIP de actualizacion disponible")
            STATE.update_nested(
                "update", {"installing": True, "progress": 0.0, "error": None}, force=True
            )
            target_zip = Path(tempfile.gettempdir()) / f"ObsVoiceClip-{latest}.zip"
            self._download(url, target_zip)
            if GITHUB is not None and helper_is_running():
                HELPER.stop(wait=True)
                time.sleep(0.6)
            staging = Path(tempfile.gettempdir()) / f"ObsVoiceClip-stage-{int(time.time())}"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target_zip) as archive:
                for name in archive.namelist():
                    normalized = name.replace("\\", "/")
                    if normalized.startswith("/") or ".." in Path(normalized).parts:
                        raise RuntimeError("El ZIP de actualizacion contiene rutas no seguras")
                archive.extractall(staging)
            root = staging
            entries = [p for p in staging.iterdir()]
            if len(entries) == 1 and entries[0].is_dir():
                root = entries[0]
            if not (root / "obs_voice_clip.py").is_file():
                raise RuntimeError("El ZIP no contiene obs_voice_clip.py")
            STATE.update_nested("update", {"progress": 70.0}, force=True)
            self._copy_tree(root, SCRIPT_DIR)
            STATE.update_nested(
                "update",
                {"installing": False, "progress": 100.0, "available": False, "error": None},
                force=True,
            )
            STATE.add_notice(
                f"ObsVoiceClip actualizado a v{latest}. Reinicia OBS para aplicar los cambios.",
                "success",
            )
            message_box(
                "ObsVoiceClip actualizado",
                f"Se ha instalado v{latest}.\n\nReinicia OBS para aplicar los cambios.",
            )
            try:
                target_zip.unlink(missing_ok=True)
                shutil.rmtree(staging, ignore_errors=True)
            except Exception:
                pass
        except Exception as exc:
            log(f"Error instalando actualizacion: {exc}", logging.ERROR)
            STATE.update_nested(
                "update",
                {"installing": False, "error": str(exc)},
                force=True,
            )
            STATE.add_notice(f"No se pudo actualizar: {exc}", "error")
            message_box("ObsVoiceClip - Error de actualizacion", str(exc))
        finally:
            with self.lock:
                self.installing = False

    def _download(self, url: str, target: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120, context=GITHUB_SSL) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            last = 0.0
            with target.open("wb") as handle:
                while True:
                    chunk = response.read(512 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last > 0.3:
                        last = now
                        progress = (done / total * 55.0) if total else 0.0
                        STATE.update_nested("update", {"progress": round(progress, 1)})

    def _copy_tree(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            if item.is_dir():
                continue
            rel = item.relative_to(source)
            target = destination / rel
            if target.name.lower() in ("config.json", "history.json"):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if target.exists():
                    target.unlink()
                shutil.copy2(item, target)
            except PermissionError:
                old = target.with_name(target.name + f".old-{int(time.time())}")
                try:
                    if old.exists():
                        old.unlink()
                    target.rename(old)
                    shutil.copy2(item, target)
                except Exception as exc:
                    raise RuntimeError(f"No se pudo reemplazar {target}: {exc}") from exc


# ---------------------------------------------------------------------------
# Servidor HTTP local
# ---------------------------------------------------------------------------


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, port: int):
        super().__init__(address, handler)
        self.port = port
        self.clients: set[queue.Queue] = set()
        self.client_lock = threading.Lock()

    def add_client(self, client: queue.Queue) -> None:
        with self.client_lock:
            self.clients.add(client)

    def remove_client(self, client: queue.Queue) -> None:
        with self.client_lock:
            self.clients.discard(client)

    def all_clients(self) -> list[queue.Queue]:
        with self.client_lock:
            return list(self.clients)


class LocalHandler(BaseHTTPRequestHandler):
    server_version = f"ObsVoiceClip/{VERSION}"
    protocol_version = "HTTP/1.1"

    @property
    def app_server(self) -> LocalServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt, *args):
        log("HTTP " + fmt % args, logging.DEBUG)

    # -- helpers ---------------------------------------------------------
    def allowed_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        port = self.app_server.port
        return origin in {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin and self.allowed_origin():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "false")
            self.send_header("Vary", "Origin")

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _html(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self._json(404, {"ok": False, "error": "archivo no encontrado"})
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _static(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self._json(404, {"ok": False, "error": "archivo no encontrado"})
            return
        types = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
        }
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", types.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0 or length > 2 * 1024 * 1024:
            return {}
        try:
            raw = self.rfile.read(length)
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _check_mutation(self) -> bool:
        if not self.allowed_origin():
            self._json(403, {"ok": False, "error": "origen no permitido"})
            return False
        if self.headers.get("X-VoiceClip") != "1":
            self._json(403, {"ok": False, "error": "cabecera X-VoiceClip requerida"})
            return False
        return True

    def do_OPTIONS(self):  # noqa: N802
        if not self.allowed_origin():
            self._json(403, {"ok": False, "error": "origen no permitido"})
            return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-VoiceClip")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- GET -------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/":
            self.send_response(302)
            self.send_header("Location", f"/panel?{parsed.query}" if parsed.query else "/panel")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/panel":
            self._html(WEB_DIR / "panel.html")
            return
        if path == "/overlay":
            self._html(WEB_DIR / "overlay.html")
            return
        if path == "/api/state":
            self._json(200, STATE.snapshot())
            return
        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "app": APP_NAME,
                    "version": VERSION,
                    "uptime": time.time(),
                },
            )
            return
        if path == "/api/events":
            self._serve_events()
            return
        if path == "/api/devices":
            try:
                devices = HELPER.devices() if HELPER and HELPER.is_running() else []
                self._json(200, {"ok": True, "devices": devices})
            except Exception as exc:
                self._json(200, {"ok": False, "error": str(exc), "devices": []})
            return
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            target = (WEB_DIR / rel).resolve()
            if not str(target).startswith(str(WEB_DIR.resolve())):
                self._json(403, {"ok": False, "error": "ruta no permitida"})
                return
            self._static(target)
            return
        self._json(404, {"ok": False, "error": "no encontrado"})

    def _serve_events(self) -> None:
        client: queue.Queue = queue.Queue(maxsize=30)
        STATE.add_client(client)
        try:
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            initial = json.dumps(STATE.snapshot(), ensure_ascii=False)
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.write(f"event: state\ndata: {initial}\n\n".encode("utf-8"))
            self.wfile.flush()
            while not UNLOADING:
                try:
                    payload = client.get(timeout=15.0)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"event: state\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            STATE.remove_client(client)

    # -- POST ------------------------------------------------------------
    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/voice/event":
            self._handle_voice_event()
            return
        if not self._check_mutation():
            return
        payload = self._body_json()
        try:
            if path == "/api/clip":
                enqueue_action("save_clip", voice_text=str(payload.get("voice_text") or ""))
                self._json(202, {"ok": True})
                return
            if path == "/api/duration":
                try:
                    seconds = int(payload.get("seconds"))
                except Exception:
                    self._json(400, {"ok": False, "error": "duracion invalida"})
                    return
                CONFIG["duration"] = max(5, min(600, seconds))
                save_config()
                STATE.set_settings(CONFIG, STATE.server_port)
                enqueue_action("set_duration", seconds=CONFIG["duration"])
                enqueue_action("sync_obs")
                self._json(200, {"ok": True, "duration": CONFIG["duration"]})
                return
            if path == "/api/settings":
                self._handle_settings(payload)
                self._json(200, {"ok": True})
                return
            if path == "/api/voice/restart":
                enqueue_action("restart_voice")
                self._json(202, {"ok": True})
                return
            if path == "/api/voice/stop":
                CONFIG["voice"]["enabled"] = False
                save_config()
                STATE.set_settings(CONFIG, STATE.server_port)
                enqueue_action("stop_voice")
                self._json(200, {"ok": True})
                return
            if path == "/api/model/download":
                language = str(payload.get("language") or CONFIG["voice"].get("language", "es"))
                MODEL_DOWNLOADER.ensure_async(language)
                self._json(202, {"ok": True})
                return
            if path == "/api/github/test":
                threading.Thread(target=github_test_worker, daemon=True, name="GitHubTest").start()
                self._json(202, {"ok": True})
                return
            if path == "/api/github/upload_last":
                enqueue_action("upload_last")
                self._json(202, {"ok": True})
                return
            if path == "/api/overlay/add":
                enqueue_action("add_overlay")
                self._json(202, {"ok": True})
                return
            if path == "/api/update/check":
                UPDATE.check_async(manual=True, prompt=False)
                self._json(202, {"ok": True})
                return
            if path == "/api/update/install":
                UPDATE.install_async()
                self._json(202, {"ok": True})
                return
            if path == "/api/open":
                target = str(payload.get("target") or "")
                if target == "logs":
                    open_path(str(LOG_DIR))
                    self._json(200, {"ok": True})
                    return
                if target == "clip_folder":
                    path_value = STATE.snapshot().get("clip", {}).get("path") or ""
                    open_path(str(Path(path_value).parent) if path_value else str(DATA_DIR))
                    self._json(200, {"ok": True})
                    return
                self._json(400, {"ok": False, "error": "destino desconocido"})
                return
            self._json(404, {"ok": False, "error": "no encontrado"})
        except Exception as exc:
            log(f"Error en API {path}: {exc}\n{traceback.format_exc()}", logging.ERROR)
            self._json(500, {"ok": False, "error": str(exc)})

    def _handle_voice_event(self) -> None:
        # Solo el helper conoce el token; tambien validamos origen local.
        token = HELPER.token if HELPER else ""
        if not token or self.headers.get("X-VoiceClip-Token") != token:
            self._json(401, {"ok": False, "error": "token invalido"})
            return
        payload = self._body_json()
        if payload.get("type") == "command":
            text = str(payload.get("text") or "")
            STATE.update_nested(
                "voice",
                {
                    "last_command": text,
                    "last_command_at": float(payload.get("ts") or time.time()),
                    "partial": "",
                },
                force=True,
            )
            enqueue_action("save_clip", voice_text=text)
            self._json(200, {"ok": True})
            return
        self._json(200, {"ok": True})

    def _handle_settings(self, payload: dict) -> None:
        changed_voice = False
        changed_duration = False
        previous_language = str(CONFIG.get("voice", {}).get("language", "es"))
        old_default_phrases = list(MODELS.get(previous_language, MODELS["es"]).get("phrases", []))
        if "duration" in payload:
            try:
                value = max(5, min(600, int(payload["duration"])))
            except Exception:
                value = CONFIG.get("duration", 30)
            if value != CONFIG.get("duration"):
                CONFIG["duration"] = value
                changed_duration = True
        for key in ("auto_start_replay", "auto_size_replay", "auto_upload"):
            if key in payload:
                CONFIG[key] = bool(payload[key])
        voice = CONFIG.setdefault("voice", {})
        # La API del panel usa nombres con prefijo voice_*; la configuracion
        # interna usa nombres cortos. Aceptamos ambos para que el panel y los
        # settings nativos sean intercambiables.
        for wire_key, key in (
            ("voice_enabled", "enabled"),
            ("enabled", "enabled"),
            ("voice_language", "language"),
            ("language", "language"),
            ("voice_device", "device"),
            ("device", "device"),
            ("voice_device_name", "device_name"),
            ("device_name", "device_name"),
        ):
            if wire_key in payload:
                value = payload[wire_key]
                value = bool(value) if key == "enabled" else str(value)
                if voice.get(key) != value:
                    voice[key] = value
                    changed_voice = True
        for wire_key, key, cast, fallback in (
            ("voice_sensitivity", "sensitivity", lambda v: max(0, min(100, int(v))), 55),
            ("sensitivity", "sensitivity", lambda v: max(0, min(100, int(v))), 55),
            ("voice_cooldown", "cooldown", lambda v: max(1.0, min(30.0, float(v))), 4.0),
            ("cooldown", "cooldown", lambda v: max(1.0, min(30.0, float(v))), 4.0),
        ):
            if wire_key in payload:
                try:
                    value = cast(payload[wire_key])
                except Exception:
                    value = voice.get(key, fallback)
                if value != voice.get(key):
                    voice[key] = value
                    changed_voice = True
        if voice.get("language") != previous_language and voice.get("phrases") in (None, [], old_default_phrases):
            voice["phrases"] = list(MODELS.get(str(voice.get("language")), MODELS["es"]).get("phrases", []))
            changed_voice = True
        for wire_key in ("voice_phrases", "phrases"):
            if wire_key in payload:
                phrases = parse_phrases(payload.get(wire_key))
                if voice.get("language") != previous_language and phrases == old_default_phrases:
                    phrases = list(MODELS.get(str(voice.get("language")), MODELS["es"]).get("phrases", phrases))
                if phrases and phrases != voice.get("phrases"):
                    voice["phrases"] = phrases
                    changed_voice = True
        github = CONFIG.setdefault("github", {})
        if "github_repo" in payload:
            repo = str(payload.get("github_repo") or "").strip()
            github["repo"] = repo
        if "github_owner" in payload:
            github["owner"] = str(payload.get("github_owner") or "").strip()
        if "github_token" in payload:
            token = str(payload.get("github_token") or "").strip()
            if token:
                github["token"] = token
        if "github_clear_token" in payload and payload.get("github_clear_token"):
            github["token"] = ""
        if "github_public" in payload:
            github["make_public"] = bool(payload["github_public"])
        overlay = CONFIG.setdefault("overlay", {})
        for key in ("position", "show_voice", "show_partial"):
            if key in payload:
                overlay[key] = payload[key] if key != "position" else str(payload[key])
        update = CONFIG.setdefault("update", {})
        if "auto_update_check" in payload:
            update["auto_check"] = bool(payload["auto_update_check"])
        save_config()
        STATE.set_settings(CONFIG, STATE.server_port)
        enqueue_action("sync_obs")
        if changed_duration:
            enqueue_action("set_duration", seconds=CONFIG["duration"])
        if changed_voice:
            enqueue_action("restart_voice")
        if "github_token" in payload or "github_repo" in payload or "github_clear_token" in payload:
            enqueue_action("verify_github")


# ---------------------------------------------------------------------------
# Logica de OBS
# ---------------------------------------------------------------------------


def enqueue_action(kind: str, **payload) -> None:
    ACTIONS.put((kind, payload))


def helper_is_running() -> bool:
    return bool(HELPER and HELPER.is_running())


def state_update_replay() -> None:
    global STATE_UPDATE_LAST
    now = time.time()
    if now - STATE_UPDATE_LAST < 0.5:
        return
    STATE_UPDATE_LAST = now
    try:
        active = bool(obs.obs_frontend_replay_buffer_active())
        output = obs.obs_frontend_get_replay_buffer_output()
        output_exists = output is not None
        if output is not None:
            try:
                obs.obs_output_release(output)  # type: ignore[attr-defined]
            except Exception:
                pass
        profile = ""
        scene = ""
        try:
            profile = str(obs.obs_frontend_get_current_profile() or "")
        except Exception:
            pass
        try:
            source = obs.obs_frontend_get_current_scene()
            if source:
                scene = str(obs.obs_source_get_name(source) or "")
                obs.obs_source_release(source)
        except Exception:
            pass
        old = STATE.snapshot()
        values = {
            "active": active,
            "output_exists": output_exists,
            "configured": bool(CONFIG.get("auto_start_replay", True)),
            "duration": int(CONFIG.get("duration", 30)),
            "restart_required": bool(old.get("replay", {}).get("restart_required")),
            "restarting": bool(REPLAY_RESTART_AT > 0),
        }
        obs_values = {
            "profile": profile,
            "scene": scene,
            "recording": bool(obs.obs_frontend_recording_active()),
            "streaming": bool(obs.obs_frontend_streaming_active()),
            "replay_active": active,
        }
        changed = old.get("replay") != values or old.get("obs") != obs_values
        STATE.update_nested("replay", values, force=False)
        STATE.update_nested("obs", obs_values, force=changed)
    except Exception as exc:
        log(f"Error actualizando estado de replay: {exc}", logging.WARNING)


def read_profile_replay_config() -> dict:
    result = {"configured": False, "duration": int(CONFIG.get("duration", 30)), "size": 0}
    try:
        config = obs.obs_frontend_get_profile_config()
        if not config:
            return result
        simple = bool(obs.config_get_bool(config, "SimpleOutput", "RecRB"))
        adv = bool(obs.config_get_bool(config, "AdvOut", "RecRB"))
        simple_time = int(obs.config_get_int(config, "SimpleOutput", "RecRBTime") or 0)
        adv_time = int(obs.config_get_int(config, "AdvOut", "RecRBTime") or 0)
        simple_size = int(obs.config_get_int(config, "SimpleOutput", "RecRBSize") or 0)
        adv_size = int(obs.config_get_int(config, "AdvOut", "RecRBSize") or 0)
        result["configured"] = simple or adv
        result["duration"] = adv_time or simple_time or result["duration"]
        result["size"] = adv_size or simple_size
    except Exception as exc:
        log(f"No se pudo leer la configuracion de replay: {exc}", logging.WARNING)
    return result


def write_replay_config(duration: int, start_replay: bool = True) -> dict:
    """Configura y arranca/reinicia el Replay Buffer desde el hilo principal de OBS."""
    global REPLAY_RESTART_AT
    duration = max(5, min(600, int(duration)))
    result = {"ok": False, "restart_required": False, "active": False, "output_exists": False}
    try:
        config = obs.obs_frontend_get_profile_config()
        if not config:
            return result
        memory = int(CONFIG.get("replay_memory_mb", 512) or 512)
        if CONFIG.get("auto_size_replay", True):
            memory = max(memory, min(4096, int(duration * 6)))
        for section in ("SimpleOutput", "AdvOut"):
            obs.config_set_bool(config, section, "RecRB", True)
            obs.config_set_int(config, section, "RecRBTime", duration)
            obs.config_set_int(config, section, "RecRBSize", memory)
        obs.config_save(config)
        output = obs.obs_frontend_get_replay_buffer_output()
        output_exists = output is not None
        if output is not None:
            try:
                obs.obs_output_release(output)  # type: ignore[attr-defined]
            except Exception:
                pass
        active = bool(obs.obs_frontend_replay_buffer_active())
        result.update({"ok": True, "output_exists": output_exists, "active": active})
        if not output_exists:
            result["restart_required"] = True
            STATE.update_nested(
                "replay",
                {
                    "output_exists": False,
                    "configured": True,
                    "restart_required": True,
                    "duration": duration,
                },
                force=True,
            )
            STATE.add_notice(
                "Replay Buffer configurado. Reinicia OBS una vez para activarlo.",
                "info",
            )
            return result
        if active:
            obs.obs_frontend_replay_buffer_stop()
            REPLAY_RESTART_AT = time.time() + 1.0
            STATE.update_nested(
                "replay",
                {"restarting": True, "duration": duration, "configured": True},
                force=True,
            )
            STATE.add_notice(f"Duracion del clip actualizada a {duration} s.", "success")
        elif start_replay:
            obs.obs_frontend_replay_buffer_start()
            time.sleep(0.2)
            result["active"] = bool(obs.obs_frontend_replay_buffer_active())
            STATE.add_notice(f"Replay Buffer iniciado con {duration} s.", "info")
            STATE.update_nested(
                "replay",
                {
                    "active": result["active"],
                    "output_exists": True,
                    "configured": True,
                    "duration": duration,
                    "restart_required": False,
                },
                force=True,
            )
        else:
            STATE.update_nested(
                "replay",
                {
                    "active": False,
                    "output_exists": True,
                    "configured": True,
                    "duration": duration,
                    "restart_required": False,
                },
                force=True,
            )
        return result
    except Exception as exc:
        log(f"Error configurando replay: {exc}", logging.ERROR)
        STATE.add_notice(f"No se pudo configurar el Replay Buffer: {exc}", "error")
        return result


def start_replay_if_possible() -> None:
    try:
        output = obs.obs_frontend_get_replay_buffer_output()
        exists = output is not None
        if output is not None:
            try:
                obs.obs_output_release(output)  # type: ignore[attr-defined]
            except Exception:
                pass
        if not exists:
            STATE.update_nested("replay", {"output_exists": False, "restart_required": True}, force=True)
            return
        if not obs.obs_frontend_replay_buffer_active():
            obs.obs_frontend_replay_buffer_start()
            time.sleep(0.15)
        STATE.update_nested(
            "replay",
            {
                "active": bool(obs.obs_frontend_replay_buffer_active()),
                "output_exists": True,
                "restart_required": False,
            },
            force=True,
        )
    except Exception as exc:
        log(f"No se pudo iniciar el replay buffer: {exc}", logging.WARNING)


def save_clip(voice_text: str = "") -> None:
    now = time.time()
    try:
        active = bool(obs.obs_frontend_replay_buffer_active())
        output = obs.obs_frontend_get_replay_buffer_output()
        exists = output is not None
        if output is not None:
            try:
                obs.obs_output_release(output)  # type: ignore[attr-defined]
            except Exception:
                pass
        if not exists:
            STATE.update_nested("replay", {"output_exists": False, "restart_required": True}, force=True)
            STATE.add_notice(
                "El Replay Buffer no esta disponible. Reinicia OBS para activarlo.",
                "error",
            )
            return
        if not active:
            obs.obs_frontend_replay_buffer_start()
            STATE.add_notice(
                "El Replay Buffer se acaba de iniciar. El primer clip puede ser mas corto.",
                "info",
            )
            STATE.update_nested("replay", {"active": True, "restart_required": False}, force=True)
        STATE.update_nested(
            "clip",
            {
                "phase": "saving",
                "duration": int(CONFIG.get("duration", 30)),
                "voice_text": voice_text,
                "error": None,
                "saved_at": now,
                "upload_progress": 0.0,
            },
            force=True,
        )
        obs.obs_frontend_replay_buffer_save()
        log(f"Clip solicitado (voice={voice_text!r}, duration={CONFIG.get('duration')})")
    except Exception as exc:
        log(f"No se pudo guardar el clip: {exc}\n{traceback.format_exc()}", logging.ERROR)
        STATE.update_nested("clip", {"phase": "error", "error": str(exc)}, force=True)
        STATE.add_notice(f"No se pudo guardar el clip: {exc}", "error")


def on_replay_saved() -> None:
    global PENDING_CLIP_PATH, PENDING_CLIP_UNTIL
    try:
        path = str(obs.obs_frontend_get_last_replay() or "")
    except Exception:
        path = ""
    log(f"Replay guardado: {path!r}")
    if path and Path(path).is_file():
        PENDING_CLIP_PATH = ""
        STATE.update_nested("clip", {"path": path, "filename": Path(path).name}, force=True)
        start_clip_processing(path)
    else:
        PENDING_CLIP_PATH = path
        PENDING_CLIP_UNTIL = time.time() + 15.0
        STATE.add_notice("Clip guardado; esperando el archivo...", "info")


def start_clip_processing(path: str) -> None:
    threading.Thread(target=process_clip_file, args=(Path(path),), daemon=True, name="ClipUpload").start()


def process_clip_file(path: Path) -> None:
    global CLIP_LOCK
    if not CLIP_LOCK.acquire(blocking=False):
        STATE.add_notice("Ya hay un clip procesandose. Espera un momento.", "info")
        return
    try:
        size = wait_for_file(path)
        if size <= 0:
            raise RuntimeError("No se encontro el archivo del clip o esta vacio")
        snapshot = STATE.snapshot()
        clip = snapshot.get("clip", {})
        duration = int(clip.get("duration") or CONFIG.get("duration", 30))
        voice_text = str(clip.get("voice_text") or "")
        STATE.update_nested(
            "clip",
            {
                "phase": "saved",
                "path": str(path),
                "filename": path.name,
                "size": size,
                "error": None,
                "saved_at": time.time(),
            },
            force=True,
        )
        history_item = {
            "path": str(path),
            "filename": path.name,
            "size": size,
            "duration": duration,
            "saved_at": time.time(),
            "url": "",
            "repo_url": "",
            "voice_text": voice_text,
        }
        if CONFIG.get("auto_upload", True) and GITHUB and GITHUB.token():
            STATE.update_nested("clip", {"phase": "uploading", "upload_progress": 0.0}, force=True)
            try:
                result = GITHUB.upload_clip(path, duration, voice_text)
                history_item.update(
                    {"url": result["url"], "repo_url": result["release_url"] or result["repo_url"]}
                )
                STATE.update_nested(
                    "clip",
                    {
                        "phase": "done",
                        "url": result["url"],
                        "repo_url": result["release_url"] or result["repo_url"],
                        "uploaded_at": time.time(),
                        "error": None,
                        "upload_progress": 100.0,
                    },
                    force=True,
                )
                STATE.add_notice("Clip subido a GitHub.", "success", result["url"])
            except Exception as exc:
                STATE.update_nested("clip", {"phase": "error", "error": str(exc)}, force=True)
                STATE.add_notice(f"El clip se guardo, pero no se pudo subir: {exc}", "error")
        else:
            STATE.update_nested(
                "clip",
                {"phase": "done", "url": "", "repo_url": "", "error": None},
                force=True,
            )
            reason = "GitHub no esta configurado" if not (GITHUB and GITHUB.token()) else "la subida automatica esta desactivada"
            STATE.add_notice(f"Clip guardado localmente ({human_size(size)}). {reason}.", "info")
        STATE.add_history(history_item)
    except Exception as exc:
        log(f"Error procesando clip: {exc}\n{traceback.format_exc()}", logging.ERROR)
        STATE.update_nested("clip", {"phase": "error", "error": str(exc)}, force=True)
        STATE.add_notice(f"Error con el clip: {exc}", "error")
    finally:
        CLIP_LOCK.release()


def wait_for_file(path: Path, timeout: float = 25.0) -> int:
    deadline = time.time() + timeout
    last_size = -1
    stable_since = 0.0
    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except Exception:
            size = -1
        if size > 0 and size == last_size:
            if stable_since == 0.0:
                stable_since = time.time()
            elif time.time() - stable_since >= 1.0:
                return size
        else:
            stable_since = 0.0
            last_size = size
        time.sleep(0.4)
    try:
        return path.stat().st_size
    except Exception:
        return 0


def add_overlay_source() -> None:
    try:
        source_name = str(CONFIG.get("overlay", {}).get("source_name") or "VoiceClip Overlay")
        overlay_url = f"http://127.0.0.1:{STATE.server_port}/overlay?overlay=1"
        vi = obs.obs_video_info()
        width, height = 1920, 1080
        try:
            if obs.obs_get_video_info(vi):
                width = int(vi.base_width or 1920)
                height = int(vi.base_height or 1080)
        except Exception:
            pass
        scene_source = obs.obs_frontend_get_current_scene()
        if not scene_source:
            raise RuntimeError("No hay una escena actual")
        scene = obs.obs_scene_from_source(scene_source)
        if not scene:
            obs.obs_source_release(scene_source)
            raise RuntimeError("No se pudo obtener la escena actual")
        existing = obs.obs_get_source_by_name(source_name)
        settings = obs.obs_data_create()
        try:
            obs.obs_data_set_string(settings, "url", overlay_url)
            obs.obs_data_set_int(settings, "width", width)
            obs.obs_data_set_int(settings, "height", height)
            obs.obs_data_set_bool(settings, "shutdown", False)
            obs.obs_data_set_bool(settings, "restart_when_active", True)
            if existing:
                obs.obs_source_update(existing, settings)
                source = existing
            else:
                source = obs.obs_source_create("browser_source", source_name, settings, None)
            if not source:
                raise RuntimeError("No se pudo crear la fuente de overlay")
            item = obs.obs_scene_find_source(scene, source_name)
            if item is None:
                item = obs.obs_scene_add(scene, source)
            if item is not None:
                try:
                    obs.obs_sceneitem_set_pos(item, (0.0, 0.0))
                    obs.obs_sceneitem_set_bounds_type(item, obs.OBS_BOUNDS_NONE)
                    obs.obs_sceneitem_set_order_position(item, 0)
                    obs.obs_sceneitem_select(item, True)
                except Exception:
                    pass
            obs.obs_source_release(source)
            obs.obs_source_release(scene_source)
            STATE.add_notice(
                f"Overlay añadido a la escena actual ({width}x{height}). Ajusta su posicion si lo necesitas.",
                "success",
            )
        finally:
            obs.obs_data_release(settings)
    except Exception as exc:
        log(f"No se pudo añadir el overlay: {exc}\n{traceback.format_exc()}", logging.ERROR)
        STATE.add_notice(f"No se pudo añadir el overlay: {exc}", "error")


def upload_last_clip() -> None:
    snapshot = STATE.snapshot()
    path = str(snapshot.get("clip", {}).get("path") or "")
    if not path or not Path(path).is_file():
        STATE.add_notice("No hay un clip reciente para subir.", "error")
        return
    if not GITHUB or not GITHUB.token():
        STATE.add_notice("Configura el token de GitHub en el panel.", "error")
        return
    threading.Thread(target=process_clip_file, args=(Path(path),), daemon=True, name="ClipUploadManual").start()


def github_test_worker() -> None:
    try:
        if GITHUB:
            GITHUB.test_connection()
    except Exception as exc:
        log(f"Test de GitHub fallo: {exc}", logging.WARNING)


def restart_voice() -> None:
    if UNLOADING or HELPER is None:
        return
    if not CONFIG.get("voice", {}).get("enabled", True):
        HELPER.stop(wait=True)
        STATE.update_nested("voice", {"state": "off", "enabled": False, "error": None}, force=True)
        return
    language = str(CONFIG.get("voice", {}).get("language", "es"))
    info = MODELS.get(language, MODELS["es"])
    model_path = MODELS_DIR / info["name"]
    STATE.set_settings(CONFIG, STATE.server_port)
    STATE.update_nested("voice", {"enabled": True, "state": "starting", "error": None}, force=True)
    if not (model_path / "am" / "final.mdl").is_file():
        STATE.update_nested(
            "voice",
            {
                "state": "model_missing",
                "error": "Descarga el modelo de voz desde el panel",
                "model": str(model_path),
            },
            force=True,
        )
        MODEL_DOWNLOADER.ensure_async(language)
        return
    HELPER.start_async(CONFIG)


def apply_config_to_obs_settings() -> None:
    if OBS_SETTINGS is None:
        return
    try:
        obs.obs_data_set_int(OBS_SETTINGS, "duration", int(CONFIG.get("duration", 30)))
        obs.obs_data_set_bool(OBS_SETTINGS, "voice_enabled", bool(CONFIG.get("voice", {}).get("enabled", True)))
        obs.obs_data_set_bool(OBS_SETTINGS, "auto_start_replay", bool(CONFIG.get("auto_start_replay", True)))
        obs.obs_data_set_bool(OBS_SETTINGS, "auto_upload", bool(CONFIG.get("auto_upload", True)))
        obs.obs_data_set_string(OBS_SETTINGS, "github_repo", str(CONFIG.get("github", {}).get("repo", "")))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Acciones del hilo principal
# ---------------------------------------------------------------------------


def process_actions() -> None:
    global REPLAY_RESTART_AT, PENDING_CLIP_PATH, PENDING_CLIP_UNTIL
    now = time.time()
    if REPLAY_RESTART_AT and now >= REPLAY_RESTART_AT:
        REPLAY_RESTART_AT = 0.0
        try:
            obs.obs_frontend_replay_buffer_start()
            time.sleep(0.15)
            STATE.update_nested(
                "replay",
                {
                    "active": bool(obs.obs_frontend_replay_buffer_active()),
                    "restarting": False,
                    "restart_required": False,
                },
                force=True,
            )
        except Exception as exc:
            log(f"No se pudo reiniciar el replay: {exc}", logging.WARNING)
    if PENDING_CLIP_PATH and now < PENDING_CLIP_UNTIL:
        try:
            path = str(obs.obs_frontend_get_last_replay() or "")
        except Exception:
            path = ""
        if path and Path(path).is_file():
            PENDING_CLIP_PATH = ""
            STATE.update_nested("clip", {"path": path, "filename": Path(path).name}, force=True)
            start_clip_processing(path)
    for _ in range(20):
        try:
            kind, payload = ACTIONS.get_nowait()
        except queue.Empty:
            break
        try:
            if kind == "save_clip":
                save_clip(str(payload.get("voice_text") or ""))
            elif kind == "set_duration":
                write_replay_config(int(payload.get("seconds") or CONFIG.get("duration", 30)))
            elif kind == "restart_voice":
                restart_voice()
            elif kind == "stop_voice":
                if HELPER:
                    HELPER.stop(wait=True)
                STATE.update_nested("voice", {"state": "off", "enabled": False}, force=True)
            elif kind == "add_overlay":
                add_overlay_source()
            elif kind == "upload_last":
                upload_last_clip()
            elif kind == "verify_github":
                if GITHUB and GITHUB.token():
                    threading.Thread(target=github_test_worker, daemon=True, name="GitHubVerify").start()
            elif kind == "set_overlay_position":
                STATE.set_settings(CONFIG, STATE.server_port)
            elif kind == "sync_obs":
                apply_config_to_obs_settings()
            else:
                log(f"Accion desconocida: {kind}", logging.WARNING)
        except Exception as exc:
            log(f"Error ejecutando accion {kind}: {exc}\n{traceback.format_exc()}", logging.ERROR)


# ---------------------------------------------------------------------------
# Callbacks de OBS
# ---------------------------------------------------------------------------


def frontend_event(event) -> None:
    if event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_SAVED:
        on_replay_saved()
    elif event in (
        obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED,
        obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED,
    ):
        state_update_replay()
        STATE.broadcast(force=True)
    elif event in (
        obs.OBS_FRONTEND_EVENT_STREAMING_STARTED,
        obs.OBS_FRONTEND_EVENT_RECORDING_STARTED,
    ):
        if CONFIG.get("auto_start_replay", True):
            start_replay_if_possible()
        state_update_replay()
    elif event in (
        obs.OBS_FRONTEND_EVENT_STREAMING_STOPPED,
        obs.OBS_FRONTEND_EVENT_RECORDING_STOPPED,
    ):
        state_update_replay()
        STATE.broadcast(force=True)
    elif event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED:
        state_update_replay()
    elif event == obs.OBS_FRONTEND_EVENT_EXIT:
        shutdown_plugin(save=False)


def script_description():
    return (
        "<h3>ObsVoiceClip</h3>"
        "<p>Guarda los ultimos segundos del Replay Buffer con un comando de voz, "
        "los sube a un repositorio publico de GitHub y muestra un overlay en OBS.</p>"
        "<p><b>Panel:</b> abre <code>http://127.0.0.1:17877/panel</code> o usa "
        "Ver &gt; Docks &gt; Docks de navegador personalizados. "
        "Añade el overlay desde el panel con un clic.</p>"
        "<p>El token de GitHub se configura solo en el panel. El Replay Buffer "
        "debe estar activado; el panel lo configura por ti y te avisa si hace falta "
        "reiniciar OBS una vez.</p>"
    )


def script_defaults(settings):
    try:
        obs.obs_data_set_default_int(settings, "duration", int(CONFIG.get("duration", 30)))
        obs.obs_data_set_default_bool(settings, "voice_enabled", bool(CONFIG.get("voice", {}).get("enabled", True)))
        obs.obs_data_set_default_bool(settings, "auto_start_replay", bool(CONFIG.get("auto_start_replay", True)))
        obs.obs_data_set_default_bool(settings, "auto_upload", bool(CONFIG.get("auto_upload", True)))
        obs.obs_data_set_default_string(settings, "github_repo", str(CONFIG.get("github", {}).get("repo", "")))
    except Exception:
        pass


def _button_open_panel(props, prop, data):
    open_path(f"http://127.0.0.1:{STATE.server_port}/panel")
    return False


def _button_open_overlay(props, prop, data):
    open_path(f"http://127.0.0.1:{STATE.server_port}/overlay?controls=1")
    return False


def _button_save_clip(props, prop, data):
    enqueue_action("save_clip", voice_text="panel")
    return False


def _button_add_overlay(props, prop, data):
    enqueue_action("add_overlay")
    return False


def _button_check_update(props, prop, data):
    UPDATE.check_async(manual=True, prompt=False)
    return False


def _button_download_model(props, prop, data):
    MODEL_DOWNLOADER.ensure_async(str(CONFIG.get("voice", {}).get("language", "es")))
    return False


def script_properties():
    props = obs.obs_properties_create()
    try:
        obs.obs_properties_add_text(
            props,
            "_info",
            (
                "Panel: http://127.0.0.1:17877/panel\n"
                "Si no ves el dock, añadelo en Ver > Docks > Docks de navegador personalizados.\n"
                "El token de GitHub y los ajustes finos se configuran en el panel."
            ),
            obs.OBS_TEXT_INFO,
        )
        duration = obs.obs_properties_add_list(
            props, "duration", "Duracion del clip", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT
        )
        for seconds in DURATION_OPTIONS:
            obs.obs_property_list_add_int(duration, f"{seconds} segundos", seconds)
        obs.obs_properties_add_bool(props, "auto_start_replay", "Iniciar Replay Buffer automaticamente")
        obs.obs_properties_add_bool(props, "auto_upload", "Subir clips a GitHub automaticamente")
        obs.obs_properties_add_button2(props, "save_now", "Guardar clip ahora", _button_save_clip, None)
        obs.obs_properties_add_button2(props, "add_overlay", "Añadir overlay a la escena actual", _button_add_overlay, None)
        obs.obs_properties_add_bool(props, "voice_enabled", "Escuchar comandos de voz")
        obs.obs_properties_add_text(
            props,
            "_voice_help",
            "Elige microfono y frases en el panel. Si falta el modelo, descargalo con el boton.",
            obs.OBS_TEXT_INFO,
        )
        obs.obs_properties_add_button2(props, "download_model", "Descargar modelo de voz", _button_download_model, None)
        obs.obs_properties_add_text(
            props,
            "github_repo",
            "Repositorio para clips (nombre)",
            obs.OBS_TEXT_DEFAULT,
        )
        obs.obs_properties_add_text(
            props,
            "_github_help",
            "El token se configura en el panel; nunca se muestra en el overlay.",
            obs.OBS_TEXT_INFO,
        )
        obs.obs_properties_add_button2(props, "open_panel", "Abrir panel de control", _button_open_panel, None)
        obs.obs_properties_add_button2(props, "open_overlay", "Abrir overlay (modo prueba)", _button_open_overlay, None)
        obs.obs_properties_add_button2(props, "check_update", "Comprobar actualizaciones", _button_check_update, None)
    except Exception as exc:
        log(f"Error creando propiedades: {exc}", logging.ERROR)
        obs.obs_properties_add_text(props, "_error", f"Error creando la interfaz: {exc}", obs.OBS_TEXT_ERROR)
    return props


def script_load(settings):
    global CONFIG, OBS_SETTINGS, EVENT_CALLBACK, SERVER, HELPER, GITHUB, UPDATE, MODEL_DOWNLOADER, LOADED
    try:
        CONFIG = load_config()
        OBS_SETTINGS = settings
        # Aplicar los valores de la interfaz nativa solo si el usuario los cambio.
        try:
            def has_value(name):
                try:
                    return bool(obs.obs_data_has_user_value(settings, name))
                except Exception:
                    return False

            if has_value("duration"):
                value = int(obs.obs_data_get_int(settings, "duration") or 0)
                if value >= 5:
                    CONFIG["duration"] = value
            if has_value("auto_start_replay"):
                CONFIG["auto_start_replay"] = bool(obs.obs_data_get_bool(settings, "auto_start_replay"))
            if has_value("auto_upload"):
                CONFIG["auto_upload"] = bool(obs.obs_data_get_bool(settings, "auto_upload"))
            if has_value("voice_enabled"):
                CONFIG["voice"]["enabled"] = bool(obs.obs_data_get_bool(settings, "voice_enabled"))
            if has_value("github_repo"):
                repo = str(obs.obs_data_get_string(settings, "github_repo") or "")
                if repo:
                    CONFIG["github"]["repo"] = repo
        except Exception:
            pass
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        save_config()
        STATE.reset()
        STATE.set_settings(CONFIG, STATE.server_port)
        GITHUB = GitHubClient()
        UPDATE = UpdateManager()
        MODEL_DOWNLOADER = ModelDownloader()
        HELPER = HelperProcess()
        STATE.set_history(load_history())
        start_server()
        EVENT_CALLBACK = frontend_event
        obs.obs_frontend_add_event_callback(EVENT_CALLBACK)
        LOADED = True
        log(f"ObsVoiceClip {VERSION} cargado; data={DATA_DIR}")
        try:
            config = obs.obs_frontend_get_profile_config()
            if config:
                for section in ("SimpleOutput", "AdvOut"):
                    obs.config_set_bool(config, section, "RecRB", True)
                    obs.config_set_int(config, section, "RecRBTime", int(CONFIG.get("duration", 30)))
                    if CONFIG.get("auto_size_replay", True):
                        size = max(int(obs.config_get_int(config, section, "RecRBSize") or 512), min(4096, int(CONFIG.get("duration", 30)) * 6))
                        obs.config_set_int(config, section, "RecRBSize", size)
                obs.config_save(config)
        except Exception as exc:
            log(f"No se pudo preconfigurar replay: {exc}", logging.WARNING)
        state_update_replay()
        write_replay_config(
            int(CONFIG.get("duration", 30)),
            start_replay=bool(CONFIG.get("auto_start_replay", True)),
        )
        if CONFIG.get("voice", {}).get("enabled", True):
            restart_voice()
        else:
            STATE.update_nested("voice", {"state": "off", "enabled": False}, force=True)
        apply_config_to_obs_settings()
        if CONFIG.get("update", {}).get("auto_check", True):
            threading.Timer(5.0, lambda: UPDATE.check_async(manual=False, prompt=True)).start()
        STATE.add_notice("ObsVoiceClip listo. Usa el panel o di un comando de voz.", "success")
        STATE.broadcast(force=True)
    except Exception as exc:
        log(f"Error cargando el plugin: {exc}\n{traceback.format_exc()}", logging.ERROR)
        STATE.add_notice(f"Error cargando el plugin: {exc}", "error")


def script_update(settings):
    global CONFIG
    if not LOADED or UNLOADING:
        return
    changed_voice = False
    changed_duration = False
    try:
        def has_value(name):
            try:
                return bool(obs.obs_data_has_user_value(settings, name))
            except Exception:
                return False

        if has_value("duration"):
            duration = int(obs.obs_data_get_int(settings, "duration") or CONFIG.get("duration", 30))
            if duration >= 5 and duration != CONFIG.get("duration"):
                CONFIG["duration"] = max(5, min(600, duration))
                changed_duration = True
        for key in ("auto_start_replay", "auto_upload"):
            if not has_value(key):
                continue
            value = bool(obs.obs_data_get_bool(settings, key))
            if CONFIG.get(key) != value:
                CONFIG[key] = value
        if has_value("voice_enabled"):
            voice_enabled = bool(obs.obs_data_get_bool(settings, "voice_enabled"))
            if CONFIG.get("voice", {}).get("enabled") != voice_enabled:
                CONFIG["voice"]["enabled"] = voice_enabled
                changed_voice = True
        if has_value("github_repo"):
            repo = str(obs.obs_data_get_string(settings, "github_repo") or "").strip()
            if repo and repo != CONFIG.get("github", {}).get("repo"):
                CONFIG["github"]["repo"] = repo
        save_config()
        STATE.set_settings(CONFIG, STATE.server_port)
        if changed_duration:
            enqueue_action("set_duration", seconds=CONFIG["duration"])
        if changed_voice:
            enqueue_action("restart_voice")
    except Exception as exc:
        log(f"Error actualizando ajustes: {exc}", logging.WARNING)


def script_unload():
    shutdown_plugin(save=True)


def script_tick(seconds):
    global TICK_LAST
    if UNLOADING:
        return
    now = time.time()
    if now - TICK_LAST < 0.10:
        return
    TICK_LAST = now
    try:
        process_actions()
        state_update_replay()
        if HELPER and HELPER.is_running():
            # Pollear el helper en un hilo separado para no bloquear el hilo de OBS.
            if not getattr(script_tick, "_polling", False):
                setattr(script_tick, "_polling", True)

                def poll():
                    try:
                        HELPER.poll_once()
                    finally:
                        setattr(script_tick, "_polling", False)

                threading.Thread(target=poll, daemon=True, name="HelperPoll").start()
        STATE.flush_pending()
    except Exception as exc:
        log(f"Error en tick: {exc}\n{traceback.format_exc()}", logging.WARNING)


# ---------------------------------------------------------------------------
# Servidor / ciclo de vida
# ---------------------------------------------------------------------------


def start_server() -> None:
    global SERVER
    if SERVER is not None:
        return
    preferred = int(CONFIG.get("server", {}).get("port") or DEFAULT_PORT)
    port = find_free_port(preferred)
    try:
        server = LocalServer(("127.0.0.1", port), LocalHandler, port)
    except Exception as exc:
        log(f"No se pudo iniciar el servidor local: {exc}", logging.ERROR)
        STATE.add_notice(f"No se pudo iniciar el panel local: {exc}", "error")
        return
    SERVER = server
    STATE.server_port = port
    CONFIG.setdefault("server", {})["port"] = port
    save_config()
    STATE.set_settings(CONFIG, port)
    thread = threading.Thread(target=server.serve_forever, name="LocalServer", daemon=True)
    thread.start()
    log(f"Panel/overlay en http://127.0.0.1:{port}/panel")


def shutdown_plugin(save: bool = True) -> None:
    global UNLOADING, SERVER, LOADED
    if UNLOADING:
        return
    UNLOADING = True
    log("Cerrando ObsVoiceClip...")
    try:
        if EVENT_CALLBACK:
            obs.obs_frontend_remove_event_callback(EVENT_CALLBACK)
    except Exception:
        pass
    try:
        if HELPER:
            HELPER.stop(wait=True)
    except Exception:
        pass
    try:
        if SERVER:
            server = SERVER
            SERVER = None
            threading.Thread(target=server.shutdown, daemon=True).start()
            time.sleep(0.2)
            try:
                server.server_close()
            except Exception:
                pass
    except Exception:
        pass
    LOADED = False
    log("ObsVoiceClip cerrado")
