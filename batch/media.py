from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

from .common import (NO_WINDOW, RATE, ResourceError, atomic_json, check_disk,
                     digest, read_json, signature)
from .youtube_access import DEFAULTS, QuietLogger, error_message, validate_preferences, youtube_client


class MediaError(RuntimeError):
    def __init__(self, message, status="error"):
        super().__init__(message)
        self.status = status


def classify_error(message):
    message = message.casefold()
    if any(part in message for part in ("not a bot", "confirm you're not", "too many requests",
                                        "http error 429", "rate limit", "po token", "sign in to confirm you’re not",
                                        "this content isn't available, try again later")):
        return "blocked"
    if any(part in message for part in ("private video", "video unavailable", "video is not available", "has been removed",
                                        "not available in your country", "copyright", "account has been terminated")):
        return "unavailable"
    if any(part in message for part in ("sign in", "login", "age-restricted", "members-only", "members only")):
        return "login_required"
    if any(part in message for part in ("will begin", "premieres in", "scheduled", "not yet started")):
        return "scheduled"
    return "error"


def youtube_options(config, access):
    return {
        "format": "bestaudio/best", "noplaylist": True, "quiet": True, "no_warnings": True,
        "logger": QuietLogger(), "continuedl": True, "overwrites": False,
        "retries": 0, "fragment_retries": 0, "extractor_retries": 0,
        "socket_timeout": 30, "concurrent_fragment_downloads": 1,
        "skip_unavailable_fragments": False,
        "sleep_interval_requests": access["request_delay_seconds"],
        "js_runtimes": {"node": {"path": config["node"]}},
        "ffmpeg_location": str(Path(config["ffmpeg"]).parent),
        "cookiefile": None, "cookiesfrombrowser": None,
    }


def wait_before_youtube(access, progress):
    delay = int(access["delay_seconds"])
    for remaining in range(delay, 0, -1):
        progress(f"Przerwa przed żądaniami YouTube: {remaining} s", 0)
        time.sleep(1)


def download(job, work, config, progress, access=None):
    import yt_dlp

    work = Path(work)
    marker = work / "download.json"
    saved = read_json(marker)
    if saved and saved.get("identity") == job["identity"]:
        path = work / saved["filename"]
        if path.is_relative_to(work) and path.is_file() and digest(path) == saved["sha256"]:
            progress("Pobrane audio: używam zapisanej kopii", 100)
            return path
    work.mkdir(parents=True, exist_ok=True)
    access = validate_preferences(access or DEFAULTS)

    def hook(state):
        total = state.get("total_bytes") or state.get("total_bytes_estimate") or 0
        value = 100 * state.get("downloaded_bytes", 0) / total if total else 0
        if total:
            check_disk(work, max(0, total - state.get("downloaded_bytes", 0)), config["min_free_gb"])
        progress("Pobieranie audio", min(100, value))

    options = youtube_options(config, access)
    options.update(outtmpl=str(work / "download.%(ext)s"), progress_hooks=[hook])
    for attempt in range(1, 4):
        try:
            wait_before_youtube(access, progress)
            progress(f"Sprawdzanie YouTube (próba {attempt}/3)", 0)
            with youtube_client(options, access) as ydl:
                info = ydl.extract_info(job["source"], download=False)
                live = info.get("live_status")
                if live in {"is_live", "post_live"}:
                    raise MediaError("Transmisja trwa lub YouTube przetwarza jej zapis.", "live")
                if live == "is_upcoming":
                    raise MediaError("Materiał jest zaplanowany, ale jeszcze niedostępny.", "scheduled")
                estimate = info.get("filesize") or info.get("filesize_approx") or 0
                check_disk(work, estimate, config["min_free_gb"])
                ydl.process_info(info)
                path = Path(ydl.prepare_filename(info)).resolve()
                if not path.is_relative_to(work.resolve()) or not path.is_file() or not path.stat().st_size:
                    raise RuntimeError("yt-dlp nie zapisał kompletnego pliku audio.")
                atomic_json(marker, {"identity": job["identity"], "filename": path.name,
                                    "sha256": digest(path), "duration": info.get("duration"),
                                    "format_id": info.get("format_id"), "ext": info.get("ext"),
                                    "yt_dlp_version": yt_dlp.version.__version__})
                return path
        except (MediaError, ResourceError):
            raise
        except Exception as exc:
            message = str(exc)
            status = classify_error(message)
            if status != "error" or attempt == 3:
                raise MediaError(error_message(message), status) from None
            progress(f"Błąd przejściowy. Ponawianie za {attempt * 5} s ({attempt}/3)", 0)
            time.sleep(attempt * 5)


def probe(source, config):
    result = subprocess.run([config["ffprobe"], "-v", "error", "-show_streams",
        "-show_format", "-of", "json", str(source)], capture_output=True,
        creationflags=NO_WINDOW, timeout=90)
    if result.returncode:
        raise MediaError("FFprobe: " + result.stderr.decode("utf-8", errors="replace")[-2000:])
    return json.loads(result.stdout)


def audio_tracks(source, config):
    return [stream for stream in probe(source, config)["streams"] if stream.get("codec_type") == "audio"]


def decode(source, work, config, track, progress):
    import numpy as np

    work, source = Path(work), Path(source)
    source_hash = digest(source)
    key = signature({"source": source_hash, "track": track, "rate": RATE, "decoder": "mono-rematrix-v1"})
    marker, pcm = work / "pcm.json", work / "audio.f32le"
    saved = read_json(marker)
    if saved and saved.get("signature") == key and pcm.is_file() and pcm.stat().st_size == saved["bytes"] and digest(pcm) == saved.get("sha256"):
        return pcm, saved["duration"], key
    data = probe(source, config)
    streams = [s for s in data["streams"] if s.get("codec_type") == "audio"]
    if track < 0 or track >= len(streams):
        raise MediaError(f"Wybrano ścieżkę audio {track + 1}, dostępnych: {len(streams)}.")
    duration = float(data.get("format", {}).get("duration") or streams[track].get("duration") or 0)
    check_disk(work, duration * RATE * 4, config["min_free_gb"])
    temporary, error_file = work / "audio.f32le.partial", work / "ffmpeg.log"
    command = [config["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
        "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", f"0:a:{track}",
        "-vn", "-sn", "-dn", "-ac", "1", "-af", "aresample=rematrix_maxval=1.0",
        "-ar", str(RATE), "-c:a", "pcm_f32le", "-f", "f32le",
        "-progress", "pipe:1", str(temporary)]
    with error_file.open("wb") as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
                                   creationflags=NO_WINDOW, text=True, encoding="utf-8")
        from .processes import ChildGuard
        guard = ChildGuard(process)
        guard.__enter__()
        try:
            for line in process.stdout:
                if line.startswith("out_time_us="):
                    try:
                        elapsed = int(line.strip().split("=", 1)[1]) / 1e6
                    except ValueError:
                        continue
                    progress("Dekodowanie mono 16 kHz", min(100, elapsed / duration * 100) if duration else 0)
                    check_disk(work, reserve_gb=config["min_free_gb"])
            code = process.wait()
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                process.wait()
            guard.__exit__(None, None, None)
    if code:
        message = error_file.read_text(encoding="utf-8", errors="replace")[-2500:]
        if "No space" in message:
            raise ResourceError(message)
        raise MediaError("FFmpeg: " + message)
    size = temporary.stat().st_size
    if not size or size % 4:
        raise MediaError("Brak poprawnych próbek audio.")
    with temporary.open("r+b") as stream:
        while True:
            audio = np.fromfile(stream, dtype="<f4", count=RATE * 60)
            if not len(audio):
                break
            if not np.isfinite(audio).all():
                raise MediaError("Audio zawiera niefinitywne wartości.")
        os.fsync(stream.fileno())
    os.replace(temporary, pcm)
    duration = size / 4 / RATE
    atomic_json(marker, {"signature": key, "bytes": size, "duration": duration,
                         "source_sha256": source_hash, "track": track, "sha256": digest(pcm)})
    return pcm, duration, key
