from __future__ import annotations

from collections import defaultdict
from itertools import islice
import math


def merge_speakers(parts):
    """Constrained agglomeration of normalized block speaker centroids.

    Use Euclidean distances, as in Community-1's initial clustering. Cross-block
    linking is a heuristic, not a guarantee of identity. Cannot-link constraints
    prohibit combining distinct speakers observed in the same block.
    """
    import numpy as np

    thresholds = {float(p["threshold"]) for p in parts}
    if len(thresholds) != 1 or not all(math.isfinite(t) and t > 0 for t in thresholds):
        raise ValueError("Brak spójnego progu grupowania z konfiguracji modelu.")
    threshold = thresholds.pop()
    nodes, raw, exclusive = [], [], []
    for part in parts:
        block = part["block"]
        index = block["index"]
        for label, embedding in part["embeddings"].items():
            vector = np.asarray(embedding, dtype=float) if embedding is not None else np.array([])
            norm = np.linalg.norm(vector)
            vector = vector / norm if len(vector) and np.isfinite(vector).all() and norm > 0 else None
            intervals = [turn for turn in part["turns"] if turn["speaker"] == label]
            weight = max(0.01, sum(t["end"] - t["start"] for t in intervals))
            nodes.append({"members": {(index, label)}, "blocks": {index}, "vector": vector,
                          "weight": weight, "first": min((t["start"] for t in intervals), default=float("inf"))})
        for key, target in (("turns", raw), ("exclusive", exclusive)):
            for turn in part[key]:
                start, end = max(turn["start"], block["core_start"]), min(turn["end"], block["core_end"])
                if end > start:
                    target.append({"start": start, "end": end, "local": (index, turn["speaker"])})
    merges = []
    # One centroid per speaker per 30-minute block, not per sliding window.
    while True:
        best = None
        for a, left in enumerate(nodes):
            if left["vector"] is None:
                continue
            for b in range(a + 1, len(nodes)):
                right = nodes[b]
                if right["vector"] is None or left["blocks"] & right["blocks"]:
                    continue
                distance = float(np.linalg.norm(left["vector"] - right["vector"]))
                if distance < threshold and (best is None or distance < best[0]):
                    best = distance, a, b
        if best is None:
            break
        distance, a, b = best
        left, right = nodes[a], nodes.pop(b)
        total = left["weight"] + right["weight"]
        vector = (left["vector"] * left["weight"] + right["vector"] * right["weight"]) / total
        norm = np.linalg.norm(vector)
        left.update(vector=vector / norm if norm > 0 else None, weight=total,
                    members=left["members"] | right["members"], blocks=left["blocks"] | right["blocks"],
                    first=min(left["first"], right["first"]))
        merges.append({"distance": distance, "members": sorted(left["members"])})
    # Label by the first retained occurrence, excluding context-only hypotheses.
    first = {}
    for turn in raw:
        first[turn["local"]] = min(first.get(turn["local"], float("inf")), turn["start"])
    nodes = [node for node in nodes if any(member in first for member in node["members"])]
    nodes.sort(key=lambda node: min(first.get(member, float("inf")) for member in node["members"]))
    mapping, speakers = {}, []
    for number, node in enumerate(nodes, 1):
        name = f"Mówca {number}"
        mapping.update({member: name for member in node["members"]})
        speakers.append({"label": name, "members": sorted(node["members"]),
                         "first": min(first.get(member, float("inf")) for member in node["members"]),
                         "embedding": node["vector"].tolist() if node["vector"] is not None else None})
    for turns in (raw, exclusive):
        for turn in turns:
            turn["speaker"] = mapping.get(turn.pop("local"), "Mówca nieustalony")
        turns.sort(key=lambda turn: (turn["start"], turn["end"]))
    return {"turns": raw, "exclusive": exclusive, "speakers": speakers,
            "threshold": threshold, "metric": "euclidean_normalized_centroids",
            "linking": "constrained_agglomerative_v1", "merges": merges}


def assign_words(words, diarization):
    raw, exclusive = diarization["turns"], diarization["exclusive"]
    assigned = []
    left_raw = left_exclusive = 0
    for original in words:
        word = dict(original)
        start, end = word["start"], word["end"]
        # Zero-length word timestamps cannot establish temporal coverage.
        coverage = defaultdict(float)
        while left_exclusive < len(exclusive) and exclusive[left_exclusive]["end"] <= start:
            left_exclusive += 1
        for turn in islice(exclusive, left_exclusive, None):
            if turn["start"] >= end:
                break
            coverage[turn["speaker"]] += max(0, min(end, turn["end"]) - max(start, turn["start"]))
        ranked = sorted(coverage.items(), key=lambda item: item[1], reverse=True)
        # Require at least half a word covered; a near tie remains unresolved.
        best = ranked[0][1] if ranked else 0
        uncertain = not ranked or best < (end - start) * .5 or best <= 0
        uncertain |= len(ranked) > 1 and ranked[1][1] >= best * .9
        word["speaker"] = "Mówca nieustalony" if uncertain else ranked[0][0]
        word["speaker_coverage"] = dict(coverage)
        while left_raw < len(raw) and raw[left_raw]["end"] <= start:
            left_raw += 1
        candidates = []
        for turn in islice(raw, left_raw, None):
            if turn["start"] >= end:
                break
            if turn["end"] > start:
                candidates.append(turn)
        overlaps = []
        for a, turn in enumerate(candidates):
            for other in candidates[a + 1:]:
                overlap_start = max(start, turn["start"], other["start"])
                overlap_end = min(end, turn["end"], other["end"])
                if turn["speaker"] != other["speaker"] and overlap_end > overlap_start:
                    overlaps.append({"start": overlap_start, "end": overlap_end,
                                     "speakers": sorted({turn["speaker"], other["speaker"]})})
        word["overlap"] = overlaps
        assigned.append(word)
    return assigned


def cues_from_words(words):
    cues, current = [], None
    for word in words:
        if current and (word["speaker"] != current["speaker"] or word["start"] - current["end"] > 1.2
                        or word["end"] - current["start"] > 7 or len(current["text"]) > 110):
            cues.append(current)
            current = None
        if current is None:
            current = {"start": word["start"], "end": word["end"], "speaker": word["speaker"], "text": word["text"]}
        else:
            current["end"] = max(current["end"], word["end"])
            text = word["text"]
            # Whisper usually includes leading spaces; preserve those conventions.
            if text and not text[0].isspace() and text[0].isalnum() and current["text"] and not current["text"][-1].isspace():
                current["text"] += " "
            current["text"] += text
    if current:
        cues.append(current)
    for cue in cues:
        cue["text"] = cue["text"].strip()
    return cues
