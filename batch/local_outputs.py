"""Stable destinations for local jobs and scans that exclude our own outputs."""
from __future__ import annotations

import os
from pathlib import Path

from .common import ResourceError, read_json, slug
from .paths import local_path

OUTPUT_MODES = {"beside": "Obok źródła", "central": "Folder centralny", "custom": "Wskazany folder"}
OWNER_FILE = ".kolejka-nagranie.json"


def output_root_for(source, registry, mode, custom=""):
    if mode == "beside":
        return local_path(source).parent
    if mode == "central":
        return local_path(registry) / "wyniki"
    if mode == "custom" and str(custom).strip():
        return local_path(custom)
    raise ValueError("Wybierz miejsce wyników; dla własnego folderu podaj ścieżkę.")


def compact_folder(title, identity, root):
    # Leave room for named exports and intermediate files in native Windows tools.
    key = identity.partition(":")[2][:12]
    budget = min(32, 240 - len(str(root)) - 115)
    if budget < 1:
        raise ResourceError("Ścieżka wyników jest zbyt długa. Wybierz krótszy folder wyników.")
    return slug(title, budget) + "__" + key


def owned_folder(path):
    if (path / OWNER_FILE).is_file():
        return True
    for name in ("gotowe.json", "transkrypcja.json"):
        try:
            data = read_json(path / name, {})
            if isinstance(data, dict) and str(data.get("identity", "")).startswith(("local:", "yt:")):
                return True
        except (OSError, ValueError):
            continue
    return False


def scan_media(folder, extensions, recursive=True, excluded=()):
    path = local_path(folder, exists=True)
    if not path.is_dir():
        raise ValueError("Wskaż folder z nagraniami.")
    excluded = {local_path(p) for p in excluded}
    result = []
    for current, dirs, files in os.walk(path, followlinks=False):
        current = Path(current)
        if current in excluded or owned_folder(current):
            dirs[:] = []
            continue
        dirs[:] = [name for name in dirs if not (current / name).is_symlink()
                   and current / name not in excluded and not owned_folder(current / name)] if recursive else []
        result.extend(current / name for name in files if Path(name).suffix.casefold() in extensions
                      and (current / name).is_file())
    return sorted(result)
