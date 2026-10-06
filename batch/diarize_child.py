"""Run only with the isolated pyannote Python environment."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
import traceback

# Set before importing any pyannote/Hugging Face modules.
os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["DO_NOT_TRACK"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "true"

from .asr import checkpoint, valid_checkpoint
from .common import RATE, atomic_json, blocks, digest, read_json, signature


def emit(message, percent=0):
    print(json.dumps({"message": message, "percent": percent}, ensure_ascii=False), flush=True)


def gpu_probe():
    import torch
    if not torch.cuda.is_available():
        return {"device": "cpu", "message": "CUDA niedostępna. Diarizacja będzie korzystać z CPU."}
    try:
        torch.set_num_threads(max(1, min(8, os.cpu_count() or 4)))
        layer = torch.nn.Conv1d(1, 8, 5).to("cuda")
        with torch.inference_mode():
            result = layer(torch.randn(1, 1, RATE, device="cuda"))
            result.square().mean().item()
            torch.fft.rfft(torch.randn(1, 1024, device="cuda"))
            torch.cuda.synchronize()
        return {"device": "cuda", "message": "GPU: " + torch.cuda.get_device_name(0),
                "capability": list(torch.cuda.get_device_capability(0)), "torch": torch.__version__}
    except Exception as exc:
        return {"device": "cpu", "message": "Test CUDA nieudany, używam CPU: " + str(exc)[:500]}


def load_pipeline(model):
    from pyannote.audio import Pipeline
    pipeline = Pipeline.from_pretrained(str(model), token=False)
    if pipeline is None:
        raise RuntimeError("Nie można załadować lokalnego modelu Community-1.")
    # Read the instantiated model parameters, never guess its threshold.
    threshold = float(pipeline.parameters(instantiated=True)["clustering"]["threshold"])
    return pipeline, threshold


def run(request):
    import numpy as np
    import torch

    config = request["config"]
    model, work = Path(config["diar_model"]), Path(request["work"])
    versions = {name: importlib.metadata.version(name) for name in ("pyannote.audio", "torch", "torchaudio")}
    base = signature({"pcm": request["pcm_key"], "revision": config["diar_revision"],
                      "config": digest(model / "config.yaml"), "versions": versions, "version": 1})
    block_list = list(blocks(request["duration"], config["diar_seconds"], config["diar_context"]))
    pipeline, device_info, threshold = None, None, None
    audio = None
    output = []
    try:
        for block in block_list:
            key = signature({"base": base, "block": block})
            path = work / "diar" / f"{block['index']:06}.json"
            part = valid_checkpoint(path, key)
            if part is None:
                if pipeline is None:
                    emit("Ładowanie lokalnego modelu mówców")
                    pipeline, threshold = load_pipeline(model)
                    device_info = gpu_probe()
                    pipeline.to(torch.device(device_info["device"]))
                    # Exercise actual model kernels before selecting CUDA for a film.
                    if device_info["device"] == "cuda":
                        try:
                            test = torch.from_numpy(np.fromfile(request["pcm"], dtype="<f4", count=RATE * 5)).unsqueeze(0)
                            pipeline({"waveform": test, "sample_rate": RATE})
                            torch.cuda.synchronize()
                        except Exception as exc:
                            pipeline.to(torch.device("cpu"))
                            torch.cuda.empty_cache()
                            device_info = {"device": "cpu", "message": "Model nie przeszedł próby GPU; CPU: " + str(exc)[:500]}
                    emit(device_info["message"])
                    atomic_json(work / "diar-device.json", device_info)
                start_time = time.perf_counter()
                message = f"Mówcy: blok {block['index'] + 1}/{len(block_list)} ({device_info['device']})"
                def hook(step_name, step_artifact, file=None, total=None, completed=None, **kwargs):
                    fraction = completed / total if total and completed is not None else 0
                    emit(f"{message}: {step_name}", (block["index"] + fraction) / len(block_list) * 100)
                # At most 30 minutes + boundary context in RAM, no Python float list.
                offset = round(block["start"] * RATE)
                count = round(block["end"] * RATE) - offset
                audio = np.memmap(request["pcm"], dtype="<f4", mode="r", offset=offset * 4, shape=(count,))
                samples = np.array(audio, copy=True)
                audio._mmap.close()
                audio = None
                waveform = torch.from_numpy(samples).unsqueeze(0)
                with torch.inference_mode():
                    try:
                        result = pipeline({"waveform": waveform, "sample_rate": RATE}, hook=hook)
                    except torch.cuda.OutOfMemoryError:
                        pipeline.to(torch.device("cpu"))
                        torch.cuda.empty_cache()
                        device_info = {"device": "cpu", "message": "Za mało pamięci GPU. Dalsza diarizacja na CPU."}
                        emit(device_info["message"])
                        atomic_json(work / "diar-device.json", device_info)
                        result = pipeline({"waveform": waveform, "sample_rate": RATE}, hook=hook)
                def turns(annotation):
                    return [{"start": block["start"] + float(turn.start),
                             "end": block["start"] + float(turn.end), "speaker": str(label)}
                            for turn, _, label in annotation.itertracks(yield_label=True)]
                labels = result.speaker_diarization.labels()
                embeddings = {}
                for index, label in enumerate(labels):
                    vector = result.speaker_embeddings[index] if result.speaker_embeddings is not None else None
                    embeddings[str(label)] = vector.tolist() if vector is not None and np.isfinite(vector).all() else None
                part = {"signature": key, "block": block, "turns": turns(result.speaker_diarization),
                        "exclusive": turns(result.exclusive_speaker_diarization), "embeddings": embeddings,
                        "threshold": threshold, "device": device_info, "versions": versions,
                        "seconds": time.perf_counter() - start_time}
                checkpoint(path, part)
                del result, waveform, samples
            output.append(part)
            emit(f"Mówcy: zapisano {block['index'] + 1}/{len(block_list)} bloków", (block["index"] + 1) / len(block_list) * 100)
    finally:
        if audio is not None:
            audio._mmap.close()
    checkpoint(work / "diar-result.json", {"signature": request["signature"], "blocks": output})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", nargs="?")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if args.probe:
        from pyannote.audio import Pipeline  # Verify dependencies as well as CUDA.
        print(json.dumps(gpu_probe(), ensure_ascii=False))
    else:
        run(read_json(args.request))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
