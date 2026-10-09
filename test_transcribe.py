"""Regression checks for audio decoding and preservation of incomplete segments."""

import json
from pathlib import Path
import tempfile
import unittest
import wave

import numpy as np

from transcribe import (checked_device, decoded_audio, find_ffmpeg, save_outputs,
                        subtitle_cues, timestamp)


class TranscriptionTests(unittest.TestCase):
    def test_missing_timestamp_keeps_text_and_does_not_invent_subtitle(self):
        report = {"text": "Pełny tekst. Urwane słowo", "audio_duration_s": 4.0,
                  "segments": [{"start": 0.0, "end": 2.0, "text": "Pełny tekst."},
                               {"start": 2.0, "end": -1.0, "text": "Urwane słowo"}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            warnings = save_outputs(output, report)
            self.assertEqual(len(warnings), 1)
            self.assertIn("Urwane słowo", (output / "transcript.txt").read_text("utf-8"))
            saved = json.loads((output / "transcript.json").read_text("utf-8"))
            self.assertEqual(saved["segments"][1]["end"], -1.0)
            self.assertNotIn("Urwane słowo", (output / "transcript.srt").read_text("utf-8"))
            with self.assertRaises(FileExistsError):
                save_outputs(output, report)

    def test_subtitles_beyond_one_hour_and_fraction_rounding(self):
        cues, warnings = subtitle_cues([
            {"start": 3599.9996, "end": 3602.1236, "text": "Zażółć\n gęślą"},
            {"start": None, "end": 3604.0, "text": "Brak początku"},
            {"start": 3604.0, "end": None, "text": "Brak końca"},
            {"start": 3605.0, "end": 3608.0, "text": "Ostatni napis"},
        ], 3607.0)
        self.assertEqual(cues[0], (3600000, 3602124, "Zażółć gęślą"))
        self.assertEqual(cues[1][1], 3607000)
        self.assertEqual(timestamp(cues[0][0], ","), "01:00:00,000")
        self.assertEqual(len(warnings), 3)

    def test_actual_ffmpeg_resamples_stereo_and_releases_temporary_file(self):
        try:
            ffmpeg = find_ffmpeg()
        except FileNotFoundError:
            self.skipTest('Integration check requires FFmpeg on PATH.')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "próba stereo.wav"
            with wave.open(str(source), "wb") as wav:
                wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                # Constant unequal channels exercise the stereo-to-mono conversion.
                samples = np.tile(np.array([12000, 4000], dtype="<i2"), (48000, 1))
                wav.writeframes(samples.tobytes())
            with decoded_audio(source, ffmpeg, directory) as audio:
                self.assertEqual(len(audio), 16000)
                self.assertEqual(audio.dtype, np.dtype("<f4"))
                self.assertTrue(np.isfinite(audio).all())
                self.assertAlmostEqual(float(audio[8000]), 8000 / 32768, places=4)
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_failed_decode_is_not_accepted_as_partial_audio(self):
        try:
            ffmpeg = find_ffmpeg()
        except FileNotFoundError:
            self.skipTest('Integration check requires FFmpeg on PATH.')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "broken.wav"
            source.write_bytes(b"not an audio file")
            with self.assertRaisesRegex(RuntimeError, "FFmpeg"):
                with decoded_audio(source, ffmpeg, directory):
                    pass
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_unavailable_device_does_not_silently_fall_back(self):
        class Core:
            available_devices = ["CPU", "GPU.0"]
        core = Core()
        self.assertEqual(checked_device(core, "GPU"), "GPU.0")
        with self.assertRaisesRegex(ValueError, "NPU"):
            checked_device(core, "NPU")


if __name__ == "__main__":
    unittest.main()
