"""Local OpenVINO Whisper transcription; no network access during inference."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
MODEL_ID = "OpenVINO/whisper-large-v3-turbo-fp16-ov"
MODEL_REVISION = "0250c28d68c7c10d6b5cb39707e876c0c66ab6f8"
MODEL_DIR = ROOT / "models" / "whisper-large-v3-turbo-fp16-ov"
SAMPLE_RATE = 16000


def nonnegative_number(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("Wymagana skończona liczba >= 0.")
    return number


def find_ffmpeg(explicit=None):
    candidate = explicit or ROOT / "ffmpeg.exe"
    if Path(candidate).is_file():
        return str(Path(candidate).resolve())
    executable = shutil.which(str(explicit or "ffmpeg"))
    if executable:
        return executable
    raise FileNotFoundError("Nie znaleziono FFmpeg. Podaj --ffmpeg ŚCIEŻKA.")


@contextmanager
def decoded_audio(source, ffmpeg, temp_dir=None):
    """Decode to one temporary float32 file, mapped without a Python float list.

    GenAI still creates its own input/features. This is long-form, not streaming
    inference with bounded RAM. The temporary PCM file uses 64 kB per second.
    """
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="whisper-pcm-", dir=temp_dir) as folder:
        pcm = Path(folder) / "audio.f32le"
        command = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
            "-protocol_whitelist", "file,pipe", "-i", str(source),
            "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1",
            "-af", "aresample=rematrix_maxval=1.0",
            "-ar", str(SAMPLE_RATE), "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1",
        ]
        with pcm.open("wb") as output:
            status = subprocess.run(command, stdout=output, stderr=subprocess.PIPE,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if status.returncode:
            raise RuntimeError("FFmpeg: " + status.stderr.decode("utf-8", errors="replace").strip())
        if not pcm.stat().st_size or pcm.stat().st_size % 4:
            raise ValueError("Nagranie nie zawiera poprawnych próbek audio.")
        audio = np.memmap(pcm, dtype="<f4", mode="r")
        try:
            # Limit validation buffers to one minute, including for long recordings.
            for offset in range(0, len(audio), SAMPLE_RATE * 60):
                if not np.isfinite(audio[offset:offset + SAMPLE_RATE * 60]).all():
                    raise ValueError("Audio zawiera NaN lub nieskończoność.")
            yield audio
        finally:
            # Release the Windows file mapping before TemporaryDirectory cleanup.
            audio._mmap.close()


def validate_model(model_dir):
    required = ["config.json", "generation_config.json", "preprocessor_config.json"]
    for stem in ("encoder_model", "decoder_model", "tokenizer", "detokenizer"):
        required.extend(f"openvino_{stem}.{extension}" for extension in ("xml", "bin"))
    missing = [name for name in required
               if not (model_dir / name).is_file() or (model_dir / name).stat().st_size == 0]
    if missing:
        raise FileNotFoundError("Niekompletny model: " + ", ".join(missing)
                                + ". Uruchom najpierw polecenie download.")
    preprocessor = json.loads((model_dir / "preprocessor_config.json").read_text("utf-8"))
    if preprocessor.get("sampling_rate") != SAMPLE_RATE:
        raise ValueError("Skrypt wymaga modelu przyjmującego audio 16 kHz.")


def checked_device(core, requested):
    available = core.available_devices
    if requested in available:
        return requested
    if requested in {"CPU", "GPU", "NPU"} and requested + ".0" in available:
        return requested + ".0"
    raise ValueError(f"Urządzenie {requested} jest niedostępne. OpenVINO wykrył: {available}")


def timestamp(milliseconds, separator):
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def finite_timestamp(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def subtitle_cues(segments, duration):
    """Keep raw timestamps in JSON; omit unknown bounds from subtitle exports."""
    cues, warnings = [], []
    for index, segment in enumerate(segments, 1):
        start, end = segment["start"], segment["end"]
        text = " ".join(segment["text"].split())
        if not text:
            continue
        if (start is None or end is None or not math.isfinite(start)
                or not math.isfinite(end) or start < 0 or end <= start or start >= duration):
            warnings.append(f"Segment {index}: brak poprawnych granic; tekst zachowany w TXT/JSON.")
            continue
        if end > duration:
            warnings.append(f"Segment {index}: koniec napisu ograniczony do długości audio.")
        start_ms, end_ms = round(start * 1000), round(min(end, duration) * 1000)
        if end_ms <= start_ms:
            warnings.append(f"Segment {index}: czas krótszy niż rozdzielczość napisów; pominięty.")
            continue
        cues.append((start_ms, end_ms, text))
    return cues, warnings


def save_outputs(output_dir, report):
    cues, warnings = subtitle_cues(report["segments"], report["audio_duration_s"])
    if report["text"].strip() and not cues:
        warnings.append("Brak napisów z poprawnymi czasami. Pełny tekst znajduje się w TXT/JSON.")
    report["subtitle_warnings"] = warnings
    report["subtitle_cue_count"] = len(cues)
    files = {"transcript.txt": report["text"].strip() + "\n",
             "transcript.json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"}
    for extension, separator, header in (("srt", ",", ""), ("vtt", ".", "WEBVTT\n\n")):
        lines = []
        for index, (start, end, text) in enumerate(cues, 1):
            if extension == "vtt":
                text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"{index}\n{timestamp(start, separator)} --> {timestamp(end, separator)}\n{text}\n\n")
        files[f"transcript.{extension}"] = header + "".join(lines)
    # Each run gets a new directory. Never overwrite an earlier transcript.
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        (output_dir / name).write_text(content, encoding="utf-8", newline="\n")
    return warnings


def download(args):
    from huggingface_hub import snapshot_download

    destination = args.model.resolve()
    print(f"Pobieranie {MODEL_ID}, rewizja {MODEL_REVISION} (~1,63 GB).", flush=True)
    snapshot_download(repo_id=MODEL_ID, revision=MODEL_REVISION, local_dir=destination,
                      allow_patterns=["*.json", "*.xml", "*.bin", "*.txt", "README.md"])
    validate_model(destination)
    (destination / "download_info.json").write_text(json.dumps({
        "repo_id": MODEL_ID, "revision": MODEL_REVISION,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Model gotowy: {destination}")


def generation_options(pipe, args):
    generation = {"task": "transcribe", "return_timestamps": True}
    if args.language != "auto":
        language = args.language if args.language.startswith("<|") else f"<|{args.language}|>"
        if language not in pipe.get_generation_config().lang_to_id:
            raise ValueError(f"Język {args.language!r} jest niedostępny w tym modelu.")
        generation["language"] = language
    if args.word_timestamps:
        generation["word_timestamps"] = True
    for name in ("hotwords", "initial_prompt"):
        value = getattr(args, name)
        if value:
            generation[name] = value
    return generation


def transcribe(args):
    from batch.paths import input_file, local_path, media_tool
    run_started = time.perf_counter()
    source = input_file(args.audio)
    model_dir = local_path(args.model)
    validate_model(model_dir)
    ffmpeg = media_tool(find_ffmpeg(args.ffmpeg), "ffmpeg")
    output_dir = local_path(args.output or ROOT / "outputs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    if output_dir.exists():
        raise FileExistsError(f"Katalog wynikowy już istnieje: {output_dir}. Wybierz nowy.")

    started = time.perf_counter()
    import openvino as ov
    import openvino_genai as genai

    core = ov.Core()
    device = checked_device(core, args.device.upper())
    cache = local_path(args.cache)
    options = {"CACHE_DIR": str(cache)}
    cache.mkdir(parents=True, exist_ok=True)
    if args.word_timestamps:
        options["word_timestamps"] = True
    print(f"Inicjalizacja modelu: {device} ({core.get_property(device, 'FULL_DEVICE_NAME')})", flush=True)
    pipe = genai.WhisperPipeline(str(model_dir), device, **options)
    startup_s = time.perf_counter() - started
    generation = generation_options(pipe, args)

    print("Dekodowanie do mono 16 kHz...", flush=True)
    started = time.perf_counter()
    with decoded_audio(source, ffmpeg, args.temp_dir) as audio:
        audio_s = time.perf_counter() - started
        duration = len(audio) / SAMPLE_RATE
        print(f"Audio: {duration:.2f} s; PCM: {audio.nbytes / 1024**2:.1f} MiB.", flush=True)
        warmup_s = 0.0
        if args.warmup_seconds:
            print("Rozgrzewka (osobny pomiar)...", flush=True)
            started = time.perf_counter()
            # Keep timestamp/prompt settings identical to the measured call.
            sample_count = min(len(audio), max(1, round(args.warmup_seconds * SAMPLE_RATE)))
            pipe.generate(audio[:sample_count], **generation)
            warmup_s = time.perf_counter() - started
        print("Transkrypcja całego nagrania; segmentację wykonuje WhisperPipeline...", flush=True)
        started = time.perf_counter()
        result = pipe.generate(audio, **generation)
        transcription_s = time.perf_counter() - started

    def convert_chunk(chunk):
        return {"start": finite_timestamp(chunk.start_ts), "end": finite_timestamp(chunk.end_ts),
                "text": chunk.text}

    provenance_file = model_dir / "download_info.json"
    provenance = json.loads(provenance_file.read_text("utf-8")) if provenance_file.exists() else None
    report = {
        "source": str(source), "model_path": str(model_dir), "model_download": provenance,
        "device": device, "device_name": core.get_property(device, "FULL_DEVICE_NAME"),
        "versions": {name: importlib.metadata.version(name)
                     for name in ("openvino", "openvino-genai", "openvino-tokenizers", "numpy")},
        "generation": generation, "sample_rate_hz": SAMPLE_RATE, "audio_duration_s": duration,
        "text": "".join(result.texts),
        "segments": [convert_chunk(chunk) for chunk in (result.chunks or [])],
        "words": [{"start": finite_timestamp(word.start_ts), "end": finite_timestamp(word.end_ts),
                   "text": word.word} for word in (getattr(result, "words", None) or [])],
        "timings_s": {"startup": startup_s, "audio_decode_and_validation": audio_s,
                      "warmup": warmup_s, "transcription": transcription_s,
                      "run_before_export": time.perf_counter() - run_started},
        "rtf": transcription_s / duration,
        "speed_x_realtime": duration / transcription_s if transcription_s > 0 else None,
    }
    warnings = save_outputs(output_dir, report)
    print(report["text"].strip())
    print(f"Start: {startup_s:.2f} s; audio: {audio_s:.2f} s; rozgrzewka: {warmup_s:.2f} s")
    print(f"Transkrypcja: {transcription_s:.2f} s; RTF: {report['rtf']:.3f}; "
          f"szybkość: {report['speed_x_realtime'] or 0:.2f}x")
    print(f"Wyniki TXT, JSON, SRT, VTT: {output_dir}")
    if warnings:
        print(f"Uwagi dotyczące napisów: {len(warnings)}. Szczegóły w transcript.json.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Lokalna transkrypcja Whisper przez OpenVINO GenAI.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="Pokaż urządzenia wykryte przez OpenVINO.")
    get = commands.add_parser("download", help="Jednorazowo pobierz przypiętą rewizję modelu (~1,63 GB).")
    get.add_argument("--model", type=Path, default=MODEL_DIR, help="Katalog docelowy modelu.")
    run = commands.add_parser("transcribe", help="Transkrybuj lokalny plik; model musi być pobrany.")
    run.add_argument("audio", type=Path)
    run.add_argument("--model", type=Path, default=MODEL_DIR)
    run.add_argument("--device", default="CPU")
    run.add_argument("--language", default="pl", help="Kod języka, np. pl, en lub auto.")
    run.add_argument("--hotwords", help="Terminy ułatwiające rozpoznawanie we wszystkich oknach.")
    run.add_argument("--initial-prompt", help="Kontekst pierwszego okna.")
    run.add_argument("--word-timestamps", action="store_true", help="Dodatkowe czasy słów w JSON.")
    run.add_argument("--warmup-seconds", type=nonnegative_number, default=0,
                     help="Długość rozgrzewki; 0 domyślnie, np. 30 do pomiarów.")
    run.add_argument("--output", type=Path, help="Nowy katalog wynikowy; nie może już istnieć.")
    run.add_argument("--cache", type=Path, default=ROOT / "cache")
    run.add_argument("--temp-dir", type=Path, help="Istniejący katalog na tymczasowy PCM.")
    run.add_argument("--ffmpeg", help="Ścieżka do FFmpeg; domyślnie folder programu lub PATH.")
    args = parser.parse_args()
    try:
        if args.command == "download":
            download(args)
        elif args.command == "devices":
            import openvino as ov
            core = ov.Core()
            for device in core.available_devices:
                print(f"{device}: {core.get_property(device, 'FULL_DEVICE_NAME')}")
        else:
            transcribe(args)
        return 0
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(f"Błąd: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Przerwano.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
