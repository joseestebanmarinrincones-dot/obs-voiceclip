#!/usr/bin/env python3
"""
ObsVoiceClip - helper de reconocimiento de voz.

Proceso auxiliar sin ventana que escucha un microfono o el audio del sistema
(WASAPI loopback), reconoce comandos con Vosk y avisa al plugin de OBS por
HTTP local. Tambien expone una API minima para listar dispositivos y cambiar
la configuracion sin reiniciar OBS.

Se puede ejecutar con el EXE empaquetado (recomendado) o con Python 3.10-3.12:

    python voice_helper.py --model "C:/ruta/vosk-model-small-es-0.42" ...

Dependencias para el modo Python: pyaudiowpatch>=0.2.12.6
"""

from __future__ import annotations

import argparse
import audioop
import ctypes
import json
import logging
import logging.handlers
import math
import os
import re
import signal
import socket
import sys
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

try:
    import pyaudiowpatch as pyaudio
except Exception:  # pragma: no cover - el error se reporta por HTTP
    pyaudio = None

APP_NAME = "ObsVoiceClip"
VERSION = "1.0.0"
DEFAULT_TRIGGERS = [

    "clip",
    "guarda clip",
    "corta clip",
    "haz clip",
    "nuevo clip",
    "crea clip",
    "guarda el clip",
    "corta el clip",
]

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base:
        base = str(Path.home())
    return Path(base) / APP_NAME


def setup_logging(log_path: str | None) -> None:
    path = Path(log_path) if log_path else local_appdata() / "logs" / "voice_helper.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        path = Path(os.environ.get("TEMP", ".")) / "ObsVoiceClip-helper.log"
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [handler]
    logging.info("=" * 60)
    logging.info("ObsVoiceClip helper %s iniciando", VERSION)
    logging.info("Python=%s frozen=%s exe=%s", sys.version.split()[0], getattr(sys, "frozen", False), sys.executable)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_triggers(raw: str | list[str] | None) -> list[str]:
    if isinstance(raw, list):
        items = raw
    else:
        items = re.split(r"[,;\n]+", raw or "")
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        phrase = normalize_text(str(item))
        if phrase and phrase not in seen:
            seen.add(phrase)
            result.append(phrase)
    return result or list(DEFAULT_TRIGGERS)


def phrase_matches(text: str, phrases: list[str]) -> str | None:
    normalized = f" {normalize_text(text)} "
    for phrase in phrases:
        needle = f" {phrase} "
        if needle in normalized:
            return phrase
    return None


def is_parent_alive(pid: int | None) -> bool:
    if not pid or os.name != "nt":
        return True
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


def path_has_model(path: str | os.PathLike[str] | None) -> bool:
    if not path:
        return False
    root = Path(path)
    return (root / "am" / "final.mdl").is_file() and (root / "conf" / "model.conf").is_file()


# ---------------------------------------------------------------------------
# Envoltorio minimo de la API C de Vosk (sin cffi ni paquetes extra)
# ---------------------------------------------------------------------------


class VoskLibrary:
    def __init__(self, dll_dir: str | os.PathLike[str]):
        self.dll_dir = Path(dll_dir)
        self.dll_path = self.dll_dir / "libvosk.dll"
        if os.name == "nt":
            os.environ["PATH"] = str(self.dll_dir) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                # Mantener el objeto vivo: Windows elimina el directorio de busqueda
                # cuando se recolecta el handle.
                self._dll_directory = os.add_dll_directory(str(self.dll_dir))
        self.lib = ctypes.CDLL(str(self.dll_path))
        self.lib.vosk_model_new.argtypes = [ctypes.c_char_p]
        self.lib.vosk_model_new.restype = ctypes.c_void_p
        self.lib.vosk_model_free.argtypes = [ctypes.c_void_p]
        self.lib.vosk_model_free.restype = None
        self.lib.vosk_recognizer_new.argtypes = [ctypes.c_void_p, ctypes.c_float]
        self.lib.vosk_recognizer_new.restype = ctypes.c_void_p
        self.lib.vosk_recognizer_new_grm.argtypes = [
            ctypes.c_void_p,
            ctypes.c_float,
            ctypes.c_char_p,
        ]
        self.lib.vosk_recognizer_new_grm.restype = ctypes.c_void_p
        self.lib.vosk_recognizer_free.argtypes = [ctypes.c_void_p]
        self.lib.vosk_recognizer_free.restype = None
        self.lib.vosk_recognizer_accept_waveform.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self.lib.vosk_recognizer_accept_waveform.restype = ctypes.c_int
        for name in ("result", "partial_result", "final_result"):
            fn = getattr(self.lib, f"vosk_recognizer_{name}")
            fn.argtypes = [ctypes.c_void_p]
            fn.restype = ctypes.c_char_p
        self.lib.vosk_recognizer_reset.argtypes = [ctypes.c_void_p]
        self.lib.vosk_recognizer_reset.restype = None

    def open_model(self, path: str | os.PathLike[str]) -> int:
        return self.lib.vosk_model_new(str(path).encode("utf-8"))

    def new_recognizer(self, model, rate: float, grammar: list[str] | None):
        if grammar:
            payload = json.dumps(grammar, ensure_ascii=False).encode("utf-8")
            return self.lib.vosk_recognizer_new_grm(model, float(rate), payload)
        return self.lib.vosk_recognizer_new(model, float(rate))


# ---------------------------------------------------------------------------
# Motor / hilo de escucha
# ---------------------------------------------------------------------------


class VoiceListener(threading.Thread):
    def __init__(self, app: "App"):
        super().__init__(name="VoiceListener", daemon=True)
        self.app = app
        self.stop_event = threading.Event()
        self.pa = None
        self.stream = None
        self._last_partial = ""
        self._last_partial_at = 0.0
        self._last_trigger_at = 0.0
        self._resample_state = None
        self._level = 0.0
        self._speaking = False

    # -- estados ---------------------------------------------------------
    def set_error(self, message: str) -> None:
        self.app.update_state(state="error", error=message)
        logging.error("Error de escucha: %s", message)

    def stop(self) -> None:
        self.stop_event.set()
        stream = self.stream
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass

    def _open_stream(self):
        if pyaudio is None:
            raise RuntimeError(
                "Falta pyaudiowpatch. Instala el helper empaquetado o ejecuta: "
                "python -m pip install pyaudiowpatch"
            )
        if self.pa is None:
            self.pa = pyaudio.PyAudio()
        device_id = str(self.app.config.get("device", "default") or "default")
        info = resolve_device(self.pa, device_id)
        if info is None:
            raise RuntimeError("No se encontro el dispositivo de audio seleccionado")
        rate = int(round(float(info.get("defaultSampleRate") or 48000)))
        channels = 1 if int(info.get("maxInputChannels", 1)) <= 1 else 2
        frames = max(160, int(rate * 0.10))
        logging.info(
            "Abriendo audio: id=%s name=%r rate=%s channels=%s loopback=%s",
            info.get("index"),
            info.get("name"),
            rate,
            channels,
            bool(info.get("isLoopbackDevice")),
        )
        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            frames_per_buffer=frames,
            input_device_index=int(info["index"]),
        )
        self.app.update_state(
            state="listening",
            device_id=str(info["index"]),
            device=str(info.get("name") or ""),
            error=None,
        )
        return stream, rate, channels, frames

    def run(self) -> None:
        if self.app.vosk is None:
            self.set_error(self.app.model_error or "Modelo de voz no disponible")
            return
        try:
            self.stream, rate, channels, frames = self._open_stream()
        except Exception as exc:
            self.set_error(str(exc))
            return

        grammar = self.app.grammar_words()
        recognizer = None
        try:
            model = self.app.model_handle
            if not model:
                raise RuntimeError("El modelo de voz no esta cargado")
            recognizer = self.app.vosk.new_recognizer(model, 16000.0, grammar)
            if not recognizer:
                raise RuntimeError("Vosk no pudo crear el reconocedor")
            logging.info("Reconocedor listo; gramatica=%s", grammar)
        except Exception as exc:
            self.set_error(str(exc))
            self._close_stream()
            return

        silence_since = time.time()
        try:
            while not self.stop_event.is_set():
                try:
                    data = self.stream.read(frames, exception_on_overflow=False)
                except Exception as exc:
                    if self.stop_event.is_set():
                        break
                    self.set_error(f"Error leyendo audio: {exc}")
                    time.sleep(1.0)
                    continue
                if not data:
                    time.sleep(0.05)
                    continue

                # Nivel y VAD
                try:
                    mono = audioop.tomono(data, 2, 0.5, 0.5) if channels == 2 else data
                    rms = audioop.rms(mono, 2) / 32768.0
                except Exception:
                    mono, rms = data, 0.0
                alpha = 0.35
                self._level = (1.0 - alpha) * self._level + alpha * min(1.0, rms * 8.0)
                threshold = float(self.app.config.get("threshold", 0.012) or 0.012)
                speaking = rms >= threshold
                if speaking:
                    silence_since = time.time()
                elif time.time() - silence_since > 1.0:
                    speaking = False
                if speaking != self._speaking:
                    self._speaking = speaking
                    self.app.update_state(speaking=speaking, level=round(self._level, 4))

                # Remuestreo a 16 kHz para Vosk
                pcm = mono
                if rate != 16000:
                    pcm, self._resample_state = audioop.ratecv(
                        mono, 2, 1, rate, 16000, self._resample_state
                    )
                if pcm:
                    try:
                        final = self.app.vosk.lib.vosk_recognizer_accept_waveform(
                            recognizer, pcm, len(pcm)
                        )
                    except Exception as exc:
                        self.set_error(f"Error en Vosk: {exc}")
                        break
                    now = time.time()
                    if final:
                        text = self._result(recognizer, final=True)
                        self.app.update_state(partial="")
                        if text:
                            self._handle_text(text, now, final=True)
                    else:
                        if now - self._last_partial_at >= 0.12:
                            self._last_partial_at = now
                            text = self._result(recognizer, final=False)
                            if text and text != self._last_partial:
                                self._last_partial = text
                                self.app.update_state(partial=text)
                                self._handle_text(text, now, final=False)
        finally:
            try:
                if recognizer:
                    self.app.vosk.lib.vosk_recognizer_free(recognizer)
            except Exception:
                pass
            self._close_stream()
            if not self.stop_event.is_set():
                self.app.update_state(state="error", error="El hilo de escucha termino")

    def _result(self, recognizer: int, final: bool) -> str:
        try:
            if final:
                raw = self.app.vosk.lib.vosk_recognizer_result(recognizer)
            else:
                raw = self.app.vosk.lib.vosk_recognizer_partial_result(recognizer)
            if not raw:
                return ""
            obj = json.loads(raw.decode("utf-8", "replace"))
            return str(obj.get("text") or obj.get("partial") or "").strip()
        except Exception:
            return ""

    def _handle_text(self, text: str, now: float, final: bool) -> None:
        matched = phrase_matches(text, self.app.trigger_phrases)
        if not matched:
            return
        cooldown = float(self.app.config.get("cooldown", 4.0) or 4.0)
        if now - self._last_trigger_at < cooldown:
            return
        self._last_trigger_at = now
        logging.info("COMANDO detectado: text=%r phrase=%r final=%s", text, matched, final)
        self.app.on_command(text=text, phrase=matched)

    def _close_stream(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if self.pa is not None:
            try:
                self.pa.terminate()
            except Exception:
                pass
            self.pa = None


# ---------------------------------------------------------------------------
# Dispositivos
# ---------------------------------------------------------------------------


def _safe_default(pa, method: str):
    try:
        return getattr(pa, method)()
    except Exception:
        return None


def _friendly_device_name(name: str, loopback: bool) -> str:
    value = re.sub(r"\s*\[Loopback\]\s*$", "", name or "").strip()
    if loopback:
        value += " - audio del sistema"
    return value or "Dispositivo de audio"


def resolve_device(pa, selected: str):
    selected = str(selected or "default")
    if selected == "loopback":
        info = _safe_default(pa, "get_default_wasapi_loopback")
        if info:
            return info
        selected = "default"
    if selected == "default":
        info = _safe_default(pa, "get_default_input_device_info")
        if info:
            return info
        try:
            if pa.get_device_count():
                return pa.get_device_info_by_index(0)
        except Exception:
            pass
        return None
    # Preferimos el nombre del dispositivo porque los indices de PyAudio
    # cambian cuando se conectan/desconectan dispositivos o se abre OBS.
    if selected.startswith("name:"):
        wanted = selected[5:]
        try:
            for index in range(int(pa.get_device_count())):
                info = pa.get_device_info_by_index(index)
                if not int(info.get("maxInputChannels", 0)):
                    continue
                raw = str(info.get("name") or "")
                friendly = _friendly_device_name(raw, bool(info.get("isLoopbackDevice")))
                if raw == wanted or friendly == wanted:
                    return info
        except Exception:
            pass
        return _safe_default(pa, "get_default_input_device_info")
    try:
        index = int(selected)
        return pa.get_device_info_by_index(index)
    except Exception:
        return _safe_default(pa, "get_default_input_device_info")


def list_devices(pa) -> list[dict]:
    if pa is None:
        return []
    default_index = None
    default_loopback_index = None
    info = _safe_default(pa, "get_default_input_device_info")
    if info:
        default_index = int(info.get("index"))
    info = _safe_default(pa, "get_default_wasapi_loopback")
    if info:
        default_loopback_index = int(info.get("index"))
    try:
        wasapi_index = int(pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"])
    except Exception:
        wasapi_index = -1

    result = []
    for index in range(int(pa.get_device_count())):
        try:
            info = pa.get_device_info_by_index(index)
        except Exception:
            continue
        if wasapi_index >= 0 and int(info.get("hostApi", -1)) != wasapi_index:
            continue
        if int(info.get("maxInputChannels", 0)) <= 0:
            continue
        name = str(info.get("name") or f"Dispositivo {index}")
        loopback = bool(info.get("isLoopbackDevice"))
        result.append(
            {
                "id": str(index),
                "key": f"name:{name}",
                "name": _friendly_device_name(name, loopback),
                "raw_name": name,
                "loopback": loopback,
                "channels": int(info.get("maxInputChannels", 0)),
                "rate": int(round(float(info.get("defaultSampleRate") or 0))),
                "default": index == default_index,
                "default_loopback": index == default_loopback_index,
            }
        )
    result.sort(key=lambda item: (not item["default"], not item["loopback"], item["name"].lower()))
    return result


# ---------------------------------------------------------------------------
# Aplicacion / API
# ---------------------------------------------------------------------------


class App:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.token = args.token or ""
        self.callback = args.callback or ""
        self.model_path = args.model or ""
        self.model_handle = None
        self.model_error = ""
        self.vosk: VoskLibrary | None = None
        self.listener: VoiceListener | None = None
        self.config = {
            "device": args.device or "default",
            "triggers": parse_triggers(args.phrases),
            "sensitivity": int(args.sensitivity),
            "cooldown": float(args.cooldown),
        }
        self.config["threshold"] = sensitivity_to_threshold(int(args.sensitivity))
        self.trigger_phrases = parse_triggers(args.phrases)
        self.state_lock = threading.RLock()
        self.state = {
            "ok": True,
            "version": VERSION,
            "state": "starting",
            "device_id": str(args.device or "default"),
            "device": "",
            "level": 0.0,
            "speaking": False,
            "partial": "",
            "last_command": "",
            "last_command_at": 0,
            "error": None,
            "model": self.model_path,
            "started_at": time.time(),
        }
        self.started_at = time.time()
        self.stop_event = threading.Event()
        self.device_cache: tuple[float, list[dict]] | None = None
        self.device_lock = threading.Lock()

    # -- estado ----------------------------------------------------------
    def update_state(self, **values) -> None:
        with self.state_lock:
            self.state.update(values)
            self.state["ok"] = self.state.get("state") not in ("error", "off")

    def health(self) -> dict:
        with self.state_lock:
            data = dict(self.state)
        data["uptime"] = round(time.time() - self.started_at, 1)
        data["config"] = {
            "device": self.config.get("device"),
            "sensitivity": self.config.get("sensitivity"),
            "cooldown": self.config.get("cooldown"),
            "triggers": list(self.trigger_phrases),
        }
        return data

    # -- modelo / arranque ----------------------------------------------
    def load_model_and_start(self) -> None:
        try:
            self.update_state(state="loading", error=None)
            dll_dir = Path(self.args.vosk_dll) if self.args.vosk_dll else Path(__file__).resolve().parent / "vendor" / "vosk"
            if not dll_dir.is_dir():
                dll_dir = Path(__file__).resolve().parent / "vendor" / "vosk"
            self.vosk = VoskLibrary(dll_dir)
            if not path_has_model(self.model_path):
                raise RuntimeError(
                    "Modelo de Vosk no encontrado. Descargalo desde el panel de OBS."
                )
            logging.info("Cargando modelo Vosk: %s", self.model_path)
            self.model_handle = self.vosk.open_model(self.model_path)
            if not self.model_handle:
                raise RuntimeError("Vosk no pudo cargar el modelo")
            logging.info("Modelo cargado")
            self.start_listener()
        except Exception as exc:
            self.model_error = str(exc)
            self.update_state(state="error", error=str(exc))
            logging.exception("No se pudo iniciar el reconocimiento")

    def start_listener(self) -> None:
        self.stop_listener()
        if self.stop_event.is_set():
            return
        self.config["threshold"] = sensitivity_to_threshold(int(self.config.get("sensitivity", 55)))
        listener = VoiceListener(self)
        self.listener = listener
        listener.start()

    def stop_listener(self) -> None:
        listener = self.listener
        self.listener = None
        if listener:
            listener.stop()
            listener.join(timeout=3.0)
            if listener.is_alive():
                logging.warning("El hilo de audio no termino a tiempo")

    def configure(self, payload: dict) -> None:
        if "device" in payload:
            self.config["device"] = str(payload.get("device") or "default")
        if "triggers" in payload:
            phrases = parse_triggers(payload.get("triggers"))
            self.trigger_phrases = phrases
            self.config["triggers"] = phrases
        if "sensitivity" in payload:
            try:
                self.config["sensitivity"] = max(0, min(100, int(payload["sensitivity"])))
            except Exception:
                pass
        if "cooldown" in payload:
            try:
                self.config["cooldown"] = max(1.0, min(30.0, float(payload["cooldown"])))
            except Exception:
                pass
        self.config["threshold"] = sensitivity_to_threshold(int(self.config.get("sensitivity", 55)))
        logging.info("Nueva configuracion: %s", {
            k: self.config.get(k) for k in ("device", "sensitivity", "cooldown")
        })
        self.start_listener()

    def grammar_words(self) -> list[str]:
        words = []
        for phrase in self.trigger_phrases:
            words.append(phrase)
        # [unk] permite que Vosk represente ruido/conversacion sin forzar un comando.
        if "[unk]" not in words:
            words.append("[unk]")
        return words

    # -- comandos --------------------------------------------------------
    def on_command(self, text: str, phrase: str) -> None:
        now = time.time()
        self.update_state(last_command=text, last_command_at=now, partial="")
        if not self.callback:
            return
        body = json.dumps(
            {"type": "command", "text": text, "phrase": phrase, "ts": now},
            ensure_ascii=False,
        ).encode("utf-8")
        req = Request(
            self.callback,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-VoiceClip-Token": self.token,
                "User-Agent": f"ObsVoiceClip/{VERSION}",
            },
        )
        try:
            with urlopen(req, timeout=2.5) as response:
                response.read(64)
        except Exception as exc:
            logging.warning("No se pudo enviar el comando al plugin: %s", exc)

    def devices(self) -> list[dict]:
        with self.device_lock:
            if self.device_cache and time.time() - self.device_cache[0] < 20:
                return self.device_cache[1]
        pa = None
        try:
            if pyaudio is None:
                return []
            pa = pyaudio.PyAudio()
            devices = list_devices(pa)
        except Exception as exc:
            logging.warning("No se pudieron listar dispositivos: %s", exc)
            devices = []
        finally:
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass
        with self.device_lock:
            self.device_cache = (time.time(), devices)
        return devices

    def shutdown(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.update_state(state="off")
        self.stop_listener()
        if self.vosk is not None and self.model_handle:
            try:
                self.vosk.lib.vosk_model_free(self.model_handle)
            except Exception:
                pass
            self.model_handle = None


def sensitivity_to_threshold(sensitivity: int) -> float:
    sensitivity = max(0, min(100, int(sensitivity)))
    # 0 = solo voces muy fuertes; 100 = muy sensible.
    return max(0.0025, 0.034 - (sensitivity / 100.0) * 0.0315)


class ApiHandler(BaseHTTPRequestHandler):
    server_version = f"ObsVoiceClipHelper/{VERSION}"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # noqa: N802
        logging.debug("HTTP " + fmt, *args)

    def _send(self, code: int, payload: dict | list | str, content_type="application/json; charset=utf-8"):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = payload
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authorized(self) -> bool:
        return bool(self.token) and self.headers.get("X-VoiceClip-Token") == self.token

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0 or length > 512 * 1024:
            return {}
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-VoiceClip-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._send(200, self.app.health())
            return
        if not self._authorized():
            self._send(401, {"ok": False, "error": "token invalido"})
            return
        if path == "/devices":
            self._send(200, {"ok": True, "devices": self.app.devices()})
            return
        if path == "/config":
            self._send(200, {"ok": True, "config": self.app.config, "triggers": self.app.trigger_phrases})
            return
        self._send(404, {"ok": False, "error": "no encontrado"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if not self._authorized():
            self._send(401, {"ok": False, "error": "token invalido"})
            return
        if path == "/config":
            payload = self._read_json()
            self.app.configure(payload)
            self._send(200, {"ok": True, "config": self.app.config})
            return
        if path == "/stop":
            self.app.stop_listener()
            self.app.update_state(state="off")
            self._send(200, {"ok": True})
            return
        if path == "/start":
            self.app.start_listener()
            self._send(200, {"ok": True})
            return
        if path == "/shutdown":
            self._send(200, {"ok": True})
            self.app.stop_event.set()
            threading.Thread(target=self.server.shutdown, daemon=True).start()  # type: ignore[attr-defined]
            return
        self._send(404, {"ok": False, "error": "no encontrado"})


class HelperServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, app: App, token: str):
        super().__init__(address, handler)
        self.app = app
        self.token = token


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ObsVoiceClip voice helper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17878)
    parser.add_argument("--token", default="")
    parser.add_argument("--callback", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--vosk-dll", default="")
    parser.add_argument("--device", default="default")
    parser.add_argument("--phrases", default=",".join(DEFAULT_TRIGGERS))
    parser.add_argument("--sensitivity", type=int, default=55)
    parser.add_argument("--cooldown", type=float, default=4.0)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--log", default="")
    parser.add_argument("--version", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log)
    if args.version:
        print(VERSION)
        return 0

    app = App(args)
    server = None
    try:
        server = HelperServer((args.host, int(args.port)), ApiHandler, app, args.token)
        logging.info("API local en http://%s:%s", args.host, args.port)
        threading.Thread(target=server.serve_forever, name="HelperApi", daemon=True).start()
        threading.Thread(target=app.load_model_and_start, name="ModelLoader", daemon=True).start()

        while not app.stop_event.is_set():
            if args.parent_pid and not is_parent_alive(args.parent_pid):
                logging.info("El proceso padre ya no existe; cerrando")
                break
            time.sleep(1.0)
    except OSError as exc:
        logging.error("No se pudo iniciar el helper: %s", exc)
        return 2
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        logging.info("Helper finalizado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
