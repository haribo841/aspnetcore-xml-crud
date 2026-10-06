"""Remove speaker annotations while preserving cue times and spoken text."""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from pathlib import Path
import re

from .common import atomic_new_bytes, slug

FORMATS = {".txt", ".srt", ".vtt"}
TIME = r"(?:\d+:)?\d{2}:\d{2}(?:[.,]\d{1,3})?"
TXT_TIME = re.compile(r"^(?P<time>[ \t]*\[" + TIME + r"(?:[ \t]*-[ \t]*" + TIME + r")?\])")
SUBTITLE_TIME = re.compile(r"^[ \t]*" + TIME + r"[ \t]+-->[ \t]+" + TIME)
SPEAKER = re.compile(r"^(?P<indent>[ \t]*(?:-[ \t]*)?)(?:M[oó]wca[ \t]+(?:\d+|nieustalony)|"
                     r"Speaker[ _-]*\d+|Unknown[ \t]+speaker)[ \t]*:[ \t]*", re.IGNORECASE)
VOICE = re.compile(r"<v(?:\.[^ >\r\n]+)*[ \t]+[^>\r\n]+>", re.IGNORECASE)
VOICE_END = re.compile(r"</v[ \t]*>", re.IGNORECASE)


@dataclass
class Transcript:
    path: Path
    text: str
    encoding: str
    bom: bytes

    def encode(self, text):
        return self.bom + text.encode(self.encoding)


@dataclass
class EditedTranscript:
    text: str
    removed: int
    cues: int


def load_transcript(path):
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() not in FORMATS:
        raise ValueError("Wybierz transkrypcję TXT albo napisy SRT/VTT.")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Plik tekstowy przekracza 64 MB. Podziel go na mniejsze pliki do obróbki.")
    raw = path.read_bytes()
    encoding, bom = "utf-8", b""
    for mark, codec in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                        (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(mark):
            encoding, bom, raw = codec, mark, raw[len(mark):]
            break
    try:
        text = raw.decode(encoding)
    except UnicodeError as exc:
        raise ValueError("Nie rozpoznano kodowania tekstu. Zapisz kopię jako UTF-8 lub UTF-16 i dodaj ją ponownie.") from exc
    return Transcript(path, text, encoding, bom)


def remove_speakers(text, extension):
    extension = extension.lower()
    if extension not in FORMATS:
        raise ValueError("Obsługiwane formaty to TXT, SRT i VTT.")
    lines, removed, cues = [], 0, 0
    in_cue = False
    first_payload = False
    for line in text.splitlines(keepends=True):
        prefix, payload = "", line
        if extension == ".txt":
            match = TXT_TIME.match(line)
            if not match:
                lines.append(line)
                continue
            cues += 1
            prefix, payload = line[:match.end()], line[match.end():]
        else:
            if not line.strip():
                in_cue = False
            if SUBTITLE_TIME.match(line):
                cues += 1
                in_cue = True
                first_payload = True
                lines.append(line)
                continue
            if not in_cue:
                lines.append(line)
                continue
        match = SPEAKER.match(payload) if extension == ".txt" or first_payload or payload.lstrip(" \t").startswith("-") else None
        first_payload = False
        if match:
            # Remove one leading annotation, never names mentioned in speech.
            payload = match["indent"] + payload[match.end():]
            removed += 1
        if extension == ".vtt":
            payload, count = VOICE.subn("", payload)
            if count:
                removed += count
            # A voice span can close on a later line within the same cue.
            payload = VOICE_END.sub("", payload)
        lines.append(prefix + payload)
    return EditedTranscript("".join(lines), removed, cues)


def save_without_speakers(source, output_dir=None):
    loaded = load_transcript(source)
    edited = remove_speakers(loaded.text, loaded.path.suffix)
    destination = Path(output_dir) if output_dir else loaded.path.parent
    stem = loaded.path.stem
    if stem.casefold() in {"transkrypcja", "transkrybcja", "transcript"}:
        stem = loaded.path.parent.name + "_" + stem
    stem = re.sub(r"_transkrypcja(?=(?:__wersja-\d+)?$)", "", stem)
    stem = slug(stem, limit=110).rstrip(" .") + "_bez_mowcow"
    version = 1
    while True:
        suffix = "" if version == 1 else f"__{version}"
        path = destination / (stem + suffix + loaded.path.suffix.lower())
        try:
            atomic_new_bytes(path, loaded.encode(edited.text))
            return path, edited
        except FileExistsError:
            version += 1
