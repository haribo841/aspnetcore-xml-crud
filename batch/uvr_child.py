"""Local UVR models via audio-separator, isolated from ASR and pyannote."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

os.environ["DO_NOT_TRACK"] = "1"
from .asr import checkpoint, valid_checkpoint
from .common import (NO_WINDOW, WorkerLock, atomic_json, blocks, check_disk, digest,
                     read_json, remove_work_file, settings, signature)
from .media import probe
from .processes import ChildGuard

UVR_RATE = 44100


def emit(message, percent=0):
    print(json.dumps({"message": message, "percent": percent}, ensure_ascii=False), flush=True)


def separator(config, output, online=False, force_cpu=False):
    import torch
    import onnxruntime as ort
    # Load matching CUDA/cuDNN DLLs from the isolated PyTorch installation.
    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
    if not online:
        import requests
        def offline(*args, **kwargs):
            raise RuntimeError("UVR pracuje offline. Pobierz kompletny model przed uruchomieniem kolejki.")
        requests.sessions.Session.request = offline
    from audio_separator.separator import Separator
    engine = Separator(log_level=logging.WARNING, model_file_dir=config["uvr_model_dir"],
        output_dir=str(output), output_format="WAV", output_single_stem="Vocals",
        use_soundfile=True, normalization_threshold=1.0, amplification_threshold=0.0,
        sample_rate=UVR_RATE, mdx_params={"hop_length": 1024, "segment_size": 256,
                                       "overlap": .25, "batch_size": 1, "enable_denoise": False})
    if force_cpu:
        engine.torch_device = torch.device("cpu")
        engine.onnx_execution_provider = ["CPUExecutionProvider"]
    engine.load_model(config["uvr_model"])
    return engine


def setup(root):
    root = Path(root)
    config = settings(root)
    directory = Path(config["uvr_model_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    lock = WorkerLock(directory)
    if not lock.acquire():
        return
    try:
        atomic_json(root / "konfiguracja-uvr.json", {"state": "running", "message": "Pobieranie i sprawdzanie modelu UVR"})
        engine = separator(config, directory / "test", online=True)
        manifest = {"model": config["uvr_model"], "version": importlib.metadata.version("audio-separator"),
                    "files": {name: digest(directory / name) for name in (config["uvr_model"],
                        "download_checks.json", "vr_model_data.json", "mdx_model_data.json")}}
        atomic_json(directory / "gotowe.json", manifest)
        atomic_json(root / "konfiguracja-uvr.json", {"state": "ready", "message": "Model UVR gotowy"})
    except Exception as exc:
        atomic_json(root / "konfiguracja-uvr.json", {"state": "error", "message": str(exc)[:1500]})
        raise
    finally:
        lock.close()


def run(request):
    import numpy as np
    import soundfile as sf
    config = request["config"]
    source, work, output = Path(request["source"]), Path(request["work"]), Path(request["output"])
    duration = float(probe(source, config)["format"]["duration"])
    jobs = list(blocks(duration, config["uvr_seconds"], config["uvr_context"]))
    block_dir = work / "uvr"
    block_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    check_disk(output.parent, duration * UVR_RATE * 2 * 3 * 2, config["min_free_gb"])
    engine = None
    parts = []
    for block in jobs:
        index = block["index"]
        key = signature({"request": request["signature"], "block": block})
        manifest_path = block_dir / f"{index:06}.json"
        stem = block_dir / f"{index:06}.flac"
        saved = valid_checkpoint(manifest_path, key)
        if saved and stem.is_file() and digest(stem) == saved["sha256"]:
            parts.append(saved)
            emit(f"UVR: odtworzono blok {index + 1}/{len(jobs)}", (index + 1) / len(jobs) * 100)
            continue
        emit(f"UVR: separacja bloku {index + 1}/{len(jobs)}", index / len(jobs) * 100)
        stage = block_dir / "biezacy"
        stage.mkdir(exist_ok=True)
        input_file = stage / "input.wav"
        command = [config["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(block["start"]), "-i", str(source), "-t", str(block["end"] - block["start"]),
            "-map", f"0:a:{request['track']}", "-vn", "-ac", "2", "-ar", str(UVR_RATE),
            "-c:a", "pcm_f32le", str(input_file)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=sys.stderr, creationflags=NO_WINDOW)
        with ChildGuard(process):
            if process.wait():
                raise RuntimeError("FFmpeg nie przygotował bloku do UVR.")
        if engine is None:
            engine = separator(config, stage)
        started = time.perf_counter()
        try:
            filenames = engine.separate(str(input_file))
        except Exception as exc:
            if not any(term in str(exc).casefold() for term in ("cuda", "cudnn", "cublas", "gpu", "out of memory")):
                raise
            emit("UVR: próba GPU nieudana, ponawiam blok na CPU: " + str(exc)[:250])
            engine = separator(config, stage, force_cpu=True)
            filenames = engine.separate(str(input_file))
        if len(filenames) != 1:
            raise RuntimeError("UVR nie zwrócił dokładnie jednej ścieżki Vocals.")
        generated = Path(filenames[0])
        if not generated.is_absolute():
            generated = stage / generated
        samples, rate = sf.read(generated, dtype="float32", always_2d=True)
        if rate != UVR_RATE or not np.isfinite(samples).all():
            raise RuntimeError("UVR zwrócił niepoprawne audio.")
        # Cut context only, no silence removal or time stretching. Sample indexes
        # are computed from absolute boundaries to prevent accumulated drift.
        left = round(block["core_start"] * rate) - round(block["start"] * rate)
        expected = round(block["core_end"] * rate) - round(block["core_start"] * rate)
        if len(samples) < left + expected:
            deficit = left + expected - len(samples)
            if deficit > rate * .05:
                raise RuntimeError("UVR skrócił nagranie; nie można zachować timestampów.")
            samples = np.pad(samples, ((0, deficit), (0, 0)))
        temp = stem.with_suffix(".partial")
        sf.write(temp, samples[left:left + expected], rate, format="FLAC", subtype="PCM_24")
        with temp.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temp, stem)
        saved = {"signature": key, "block": block, "frames": expected, "sha256": digest(stem),
                 "device": str(engine.torch_device), "seconds": time.perf_counter() - started}
        checkpoint(manifest_path, saved)
        parts.append(saved)
        remove_work_file(input_file, work)
        remove_work_file(generated, work)
        emit(f"UVR: zapisano blok {index + 1}/{len(jobs)}", (index + 1) / len(jobs) * 100)
    temporary = output.with_suffix(".partial")
    with sf.SoundFile(temporary, "w", samplerate=UVR_RATE, channels=2, format="FLAC", subtype="PCM_24") as writer:
        for block in jobs:
            with sf.SoundFile(block_dir / f"{block['index']:06}.flac") as reader:
                for samples in reader.blocks(blocksize=UVR_RATE * 30, dtype="float32", always_2d=True):
                    writer.write(samples)
    with temporary.open("r+b") as stream:
        os.fsync(stream.fileno())
    if sf.info(temporary).frames != round(duration * UVR_RATE):
        raise RuntimeError("Długość połączonego wokalu jest niepoprawna.")
    os.replace(temporary, output)
    atomic_json(work / "uvr-result.json", {"signature": request["signature"], "sha256": digest(output),
                "model": config["uvr_model"], "version": importlib.metadata.version("audio-separator"),
                "duration": duration, "blocks": parts, "output": str(output)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", nargs="?")
    parser.add_argument("--setup", type=Path)
    args = parser.parse_args()
    if args.setup:
        setup(args.setup)
    else:
        run(read_json(args.request))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
