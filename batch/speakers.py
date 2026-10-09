from __future__ import annotations

from collections import defaultdict
from itertools import combinations, islice
import math


def block_nodes(part):
    import numpy as np
    nodes = []
    index = part["block"]["index"]
    for label, embedding in part["embeddings"].items():
        vector = np.asarray(embedding, dtype=float) if embedding is not None else np.array([])
        norm = np.linalg.norm(vector)
        vector = vector / norm if len(vector) and np.isfinite(vector).all() and norm > 0 else None
        intervals = [turn for turn in part["turns"] if turn["speaker"] == label]
        nodes.append({"members": {(index, label)}, "blocks": {index}, "vector": vector,
                      "weight": max(0.01, sum(t["end"] - t["start"] for t in intervals)),
                      "first": min((t["start"] for t in intervals), default=float("inf"))})
    return nodes


def retained_turns(part, key):
    block = part["block"]
    result = []
    for turn in part[key]:
        start, end = max(turn["start"], block["core_start"]), min(turn["end"], block["core_end"])
        if end > start:
            result.append({"start": start, "end": end, "local": (block["index"], turn["speaker"])})
    return result


def closest_nodes(nodes, threshold):
    import numpy as np
    best = None
    for a, b in combinations(range(len(nodes)), 2):
        left, right = nodes[a], nodes[b]
        if left["vector"] is None or right["vector"] is None or left["blocks"] & right["blocks"]:
            continue
        distance = float(np.linalg.norm(left["vector"] - right["vector"]))
        if distance < threshold and (best is None or distance < best[0]):
            best = distance, a, b
    return best


def combine_nodes(nodes, best):
    import numpy as np
    distance, a, b = best
    left, right = nodes[a], nodes.pop(b)
    total = left["weight"] + right["weight"]
    vector = (left["vector"] * left["weight"] + right["vector"] * right["weight"]) / total
    norm = np.linalg.norm(vector)
    left.update(vector=vector / norm if norm > 0 else None, weight=total,
                members=left["members"] | right["members"], blocks=left["blocks"] | right["blocks"],
                first=min(left["first"], right["first"]))
    return {"distance": distance, "members": sorted(left["members"])}


def label_nodes(nodes, raw, exclusive):
    first = {}
    for turn in raw:
        first[turn["local"]] = min(first.get(turn["local"], float("inf")), turn["start"])
    nodes = [node for node in nodes if any(member in first for member in node["members"])]
    nodes.sort(key=lambda node: min(first.get(member, float("inf")) for member in node["members"]))
    mapping, speakers = {}, []
    for number, node in enumerate(nodes, 1):
        name = f"Mówca {number}"
        mapping.update(dict.fromkeys(node["members"], name))
        speakers.append({"label": name, "members": sorted(node["members"]),
                         "first": min(first.get(member, float("inf")) for member in node["members"]),
                         "embedding": node["vector"].tolist() if node["vector"] is not None else None})
    for turns in (raw, exclusive):
        for turn in turns:
            turn["speaker"] = mapping.get(turn.pop("local"), "Mówca nieustalony")
        turns.sort(key=lambda turn: (turn["start"], turn["end"]))
    return speakers


def merge_speakers(parts):
    """Constrained agglomeration of normalized block speaker centroids.

    Use Euclidean distances, as in Community-1's initial clustering. Cross-block
    linking is a heuristic, not a guarantee of identity. Cannot-link constraints
    prohibit combining distinct speakers observed in the same block.
    """
    thresholds = {float(p["threshold"]) for p in parts}
    if len(thresholds) != 1 or not all(math.isfinite(t) and t > 0 for t in thresholds):
        raise ValueError("Brak spójnego progu grupowania z konfiguracji modelu.")
    threshold = thresholds.pop()
    nodes, raw, exclusive = [], [], []
    for part in parts:
        nodes.extend(block_nodes(part))
        raw.extend(retained_turns(part, "turns"))
        exclusive.extend(retained_turns(part, "exclusive"))
    merges = []
    # One centroid per speaker per 30-minute block, not per sliding window.
    while True:
        best = closest_nodes(nodes, threshold)
        if best is None:
            break
        merges.append(combine_nodes(nodes, best))
    # Label by the first retained occurrence, excluding context-only hypotheses.
    speakers = label_nodes(nodes, raw, exclusive)
    return {"turns": raw, "exclusive": exclusive, "speakers": speakers,
            "threshold": threshold, "metric": "euclidean_normalized_centroids",
            "linking": "constrained_agglomerative_v1", "merges": merges}


def active_turns(turns, left, start, end):
    while left < len(turns) and turns[left]["end"] <= start:
        left += 1
    active = []
    for turn in islice(turns, left, None):
        if turn["start"] >= end:
            break
        active.append(turn)
    return active, left


def word_speaker(coverage, start, end):
    ranked = sorted(coverage.items(), key=lambda item: item[1], reverse=True)
    best = ranked[0][1] if ranked else 0
    uncertain = not ranked or best < (end - start) * .5 or best <= 0
    uncertain |= len(ranked) > 1 and ranked[1][1] >= best * .9
    return "Mówca nieustalony" if uncertain else ranked[0][0]


def overlapping_speakers(turns, start, end):
    candidates = [turn for turn in turns if turn["end"] > start]
    overlaps = []
    for turn, other in combinations(candidates, 2):
        overlap_start = max(start, turn["start"], other["start"])
        overlap_end = min(end, turn["end"], other["end"])
        if turn["speaker"] != other["speaker"] and overlap_end > overlap_start:
            overlaps.append({"start": overlap_start, "end": overlap_end,
                             "speakers": sorted({turn["speaker"], other["speaker"]})})
    return overlaps


def assign_words(words, diarization):
    raw, exclusive = diarization["turns"], diarization["exclusive"]
    assigned = []
    left_raw = left_exclusive = 0
    for original in words:
        word = dict(original)
        start, end = word["start"], word["end"]
        # Zero-length word timestamps cannot establish temporal coverage.
        coverage = defaultdict(float)
        turns, left_exclusive = active_turns(exclusive, left_exclusive, start, end)
        for turn in turns:
            coverage[turn["speaker"]] += max(0, min(end, turn["end"]) - max(start, turn["start"]))
        # Require at least half a word covered; a near tie remains unresolved.
        word["speaker"] = word_speaker(coverage, start, end)
        word["speaker_coverage"] = dict(coverage)
        turns, left_raw = active_turns(raw, left_raw, start, end)
        word["overlap"] = overlapping_speakers(turns, start, end)
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
