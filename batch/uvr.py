from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

from .common import APP, NO_WINDOW, ResourceError, atomic_json, check_disk, digest, read_json, signature
from .media import probe
from .processes import ChildGuard


def validate_uvr(config):
    if not Path(config["uvr_python"]).is_file():
        raise ResourceError("Brak środowiska UVR. Uruchom Instaluj-UVR.ps1.")
    directory = Path(config["uvr_model_dir"])
    for name in (config["uvr_model"], "download_checks.json", "mdx_model_data.json", "vr_model_data.json", "gotowe.json"):
        if not (directory / name).is_file():
            raise ResourceError("Brak modelu UVR. Kliknij Pobierz model UVR.")


def separate(source, work, folder, track, config, progress):
    validate_uvr(config)
    source, work, folder = Path(source), Path(work), Path(folder)
    key = signature({"source": digest(source), "track": track, "model": config["uvr_model"],
                     "model_sha256": digest(Path(config["uvr_model_dir"]) / config["uvr_model"]),
                     "seconds": config["uvr_seconds"], "context": config["uvr_context"], "version": 1})
    output = folder / "audio" / "wokal.flac"
    manifest = read_json(work / "uvr-result.json")
    if manifest and manifest.get("signature") == key and output.is_file() and digest(output) == manifest["sha256"]:
        progress("UVR: używam zachowanego wokalu", 100)
        return output, manifest
    request = {"source": str(source), "work": str(work), "output": str(output), "track": track,
               "config": config, "signature": key}
    atomic_json(work / "uvr-request.json", request)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DO_NOT_TRACK="1")
    env["PATH"] = str(Path(config["ffmpeg"]).parent) + os.pathsep + env.get("PATH", "")
    with (work / "uvr.log").open("ab") as log:
        process = subprocess.Popen([config["uvr_python"], "-m", "batch.uvr_child", str(work / "uvr-request.json")],
            cwd=APP, stdout=subprocess.PIPE, stderr=log, encoding="utf-8", errors="replace", env=env,
            creationflags=NO_WINDOW)
        error = ""
        with ChildGuard(process):
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if "error" in event:
                    error = event["error"]
                else:
                    progress(event.get("message", "Separacja UVR"), event.get("percent", 0))
            code = process.wait()
            process.stdout.close()
    if code:
        raise ResourceError("Separacja UVR nie została ukończona: " + (error or "sprawdź uvr.log"))
    manifest = read_json(work / "uvr-result.json")
    if not manifest or manifest.get("signature") != key or not output.is_file() or digest(output) != manifest["sha256"]:
        raise ResourceError("Niekompletny wynik UVR. Zachowano pobrane audio i ukończone bloki.")
    return output, manifest


def archive_source(source, folder, track, config):
    source, folder = Path(source), Path(folder)
    audio_dir = folder / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    source_hash = digest(source)
    saved = read_json(audio_dir / "zrodlo.json")
    if saved and saved.get("source_sha256") == source_hash and saved.get("track") == track:
        path = audio_dir / saved["file"]
        if path.is_file() and digest(path) == saved["sha256"]:
            return path
    metadata = probe(source, config)
    muxed = any(s.get("codec_type") == "video" for s in metadata["streams"])
    destination = audio_dir / ("zrodlo.mka" if muxed else "zrodlo" + source.suffix.lower())
    temporary = destination.with_name(destination.name + ".partial")
    check_disk(folder, source.stat().st_size, config["min_free_gb"])
    if muxed:
        with (audio_dir / "archiwizacja.log").open("wb") as log:
            process = subprocess.Popen([config["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-map", f"0:a:{track}", "-vn", "-c:a", "copy", "-f", "matroska", str(temporary)],
                stderr=log, stdout=subprocess.DEVNULL, creationflags=NO_WINDOW)
            with ChildGuard(process):
                code = process.wait()
            if code:
                raise ResourceError("Nie udało się zachować źródłowej ścieżki audio.")
        if not temporary.stat().st_size:
            raise ResourceError("Archiwum audio jest puste.")
    else:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if digest(temporary) != source_hash:
            raise ResourceError("Kopia źródłowego audio nie przeszła weryfikacji.")
    os.replace(temporary, destination)
    atomic_json(audio_dir / "zrodlo.json", {"source_sha256": source_hash, "track": track,
                "file": destination.name, "sha256": digest(destination), "stream_copy": muxed})
    return destination
