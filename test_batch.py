from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from batch.asr import ASR, checkpoint, join_blocks, valid_checkpoint
from batch.common import (APP, WorkerLock, atomic_json, blocks, defaults, digest,
                          remove_work_file, signature, timestamp)
from batch.exporter import export, verify_outputs
from batch.result_paths import output_paths
from batch.importers import import_local, import_xlsx, video_id
from batch.media import classify_error, decode
from batch.speakers import assign_words, cues_from_words, merge_speakers
from batch.store import Store
from batch.worker import Worker, cleanup

def catalog_fixture(path):
    """Build a public-data-free workbook with reordered headers and a hyperlink."""
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Materiały"
    sheet.append(["Przykładowy katalog do testów"])
    sheet.append(["Widoczność", "Link", "Tytuł", "ID filmu", "Data", "Lp.", "Typ", "Status"])
    sheet.append(["Publiczny", "Otwórz film", "Pierwszy film", "", "2020-01-01", 1, "Film", "Opublikowany"])
    sheet["B3"].hyperlink = "https://youtu.be/abcdefghij0"
    sheet.append(["Prywatny", "", "Film prywatny", "abcdefghij1", "2020-01-02", 2, "Film", "Opublikowany"])
    sheet.append(["Niepubliczny", "https://www.youtube.com/watch?v=abcdefghij2", "Transmisja", "", "2020-01-03", 3, "Transmisja", "Opublikowany"])
    sheet.append(["Publiczny", "https://www.youtube.com/shorts/abcdefghij3", "Short", "", "2020-01-04", 4, "Short", "Opublikowany"])
    sheet.append(["Prywatny", "", "Szkic", "", "2020-01-05", 5, "Film", "Szkic"])
    workbook.save(path)
    workbook.close()
    return path


def rows():
    return [{"identity": f"yt:abcdefghij{i}", "title": f"Film {i}", "kind": "youtube",
             "source": f"https://www.youtube.com/watch?v=abcdefghij{i}", "date": f"2020-01-0{i + 1}"}
            for i in range(3)]


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_catalog_twice_preserves_source_and_status(self):
        catalog = catalog_fixture(self.root / "catalog.xlsx")
        before = digest(catalog)
        first = import_xlsx(self.store, catalog)
        job = self.store.jobs()[0]
        self.store.update(job['id'], status='done')
        second = import_xlsx(self.store, catalog)
        self.assertEqual((first['rows'], first['drafts'], second['added']), (5, 1, 0))
        self.assertEqual(len(self.store.jobs()), 5)
        self.assertEqual(self.store.get(job['id'])['status'], 'done')
        self.assertEqual(before, digest(catalog))
        self.assertEqual(sum(j['visibility'] == 'Prywatny' and j['status'] == 'pending' for j in self.store.jobs()), 1)
        self.assertEqual(self.store.get(job['id'])['source'], 'https://www.youtube.com/watch?v=abcdefghij0')

    def test_local_hash_dedup_and_changed_content(self):
        a, b = self.root / 'a.wav', self.root / 'b.wav'
        a.write_bytes(b'original')
        b.write_bytes(b'original')
        self.assertEqual(import_local(self.store, [a, b])['added'], 1)
        a.write_bytes(b'new content')
        self.assertEqual(import_local(self.store, [a])['added'], 1)
        self.assertEqual(b.read_bytes(), b'original')

    def test_stop_at_every_stage_finishes_whole_current(self):
        for requested_stage in ('download', 'uvr', 'asr', 'diarization'):
            root = self.root / requested_stage
            store = Store(root)
            store.add_many(rows())
            store.start()
            executed = []
            def process(job):
                for stage in ('download', 'uvr', 'asr', 'diarization', 'export'):
                    executed.append(stage)
                    if stage == requested_stage:
                        Store(root).control(stop=1)
                store.update(job['id'], status='done')
            Worker(root, processor=process).run(check=False)
            self.assertEqual(executed, ['download', 'uvr', 'asr', 'diarization', 'export'])
            self.assertEqual([j['status'] for j in store.jobs()], ['done', 'pending', 'pending'])
            store.start()
            Worker(root, processor=lambda job: store.update(job['id'], status='done')).run(check=False)
            self.assertEqual([j['status'] for j in store.jobs()], ['done'] * 3)

    def test_singleton_and_abandoned_running_job(self):
        self.store.add_many(rows())
        self.store.start()
        self.store.claim()
        lock = WorkerLock(self.root)
        self.assertTrue(lock.acquire())
        try:
            self.assertFalse(Worker(self.root).run(check=False))
        finally:
            lock.close()
        Worker(self.root, processor=lambda job: self.store.update(job['id'], status='done')).run(check=False)
        self.assertEqual([j['status'] for j in self.store.jobs()], ['done'] * 3)

    def test_blocked_download_pauses_and_resume_retries(self):
        from batch.media import MediaError
        self.store.add_many(rows())
        self.store.start()
        def process(job):
            raise MediaError('HTTP Error 429', 'blocked')
        Worker(self.root, processor=process).run(check=False)
        self.assertEqual([j['status'] for j in self.store.jobs()], ['blocked', 'pending', 'pending'])
        self.assertEqual(self.store.control()['stop'], 1)
        self.store.start()
        self.assertEqual(self.store.jobs()[0]['status'], 'pending')

    def test_unavailable_does_not_block_next(self):
        from batch.media import MediaError
        self.store.add_many(rows())
        self.store.start()
        def process(job):
            if job['identity'].endswith('0'):
                raise MediaError('Private video', 'unavailable')
            self.store.update(job['id'], status='done')
        Worker(self.root, processor=process).run(check=False)
        self.assertEqual([j['status'] for j in self.store.jobs()], ['unavailable', 'done', 'done'])

    def test_report_formula_injection_is_escaped(self):
        row = rows()[0]
        row['title'] = '=1+2'
        self.store.add_many([row])
        self.store.export_csv(self.root / 'report.csv')
        self.assertIn("'=1+2", (self.root / 'report.csv').read_text('utf-8-sig'))


class AlgorithmTests(unittest.TestCase):
    def test_video_ids_and_time(self):
        self.assertEqual(video_id('https://youtu.be/jhNt9yPlFyE?t=2'), 'jhNt9yPlFyE')
        self.assertEqual(video_id('https://studio.youtube.com/video/jhNt9yPlFyE/edit'), 'jhNt9yPlFyE')
        self.assertEqual(video_id('https://example.org/watch?v=jhNt9yPlFyE'), '')
        self.assertEqual(timestamp(12 * 3600 + 59.9996), '12:01:00.000')

    def test_blocks_cover_multi_hour_file_with_bounded_windows(self):
        parts = list(blocks(12 * 3600 + 37, 600, 5))
        self.assertEqual(len(parts), 73)
        self.assertEqual(parts[-1]['core_end'], 43237)
        self.assertLessEqual(max(p['end'] - p['start'] for p in parts), 610)
        self.assertTrue(all(a['core_end'] == b['core_start'] for a, b in zip(parts, parts[1:])))

    def test_word_boundary_dedup_preserves_repeated_words_elsewhere(self):
        def word(a, b, text):
            return {'start': a, 'end': b, 'text': text}
        parts = [
            {'block': {'core_start': 0, 'core_end': 10}, 'words': [word(1, 2, 'Tak'), word(9.5, 10.1, ' granica')]},
            {'block': {'core_start': 10, 'core_end': 20}, 'words': [word(9.8, 10.4, ' granica'), word(13, 14, ' Tak'), word(15, 16, ' Tak')]},
        ]
        self.assertEqual([w['text'].strip() for w in join_blocks(parts)], ['Tak', 'granica', 'Tak', 'Tak'])

    def test_speaker_linking_cannot_link_same_block_and_orders_first(self):
        def part(index, start, embeddings, turns):
            return {'block': {'index': index, 'core_start': start, 'core_end': start + 10},
                    'embeddings': embeddings, 'threshold': .6, 'turns': turns, 'exclusive': turns}
        a = {'start': 1, 'end': 2, 'speaker': 'B'}
        b = {'start': 3, 'end': 4, 'speaker': 'A'}
        c = {'start': 11, 'end': 12, 'speaker': 'X'}
        result = merge_speakers([part(0, 0, {'A': [1, 0], 'B': [.99, .01]}, [a, b]),
                                part(1, 10, {'X': [1, 0]}, [c])])
        self.assertEqual(len(result['speakers']), 2)
        self.assertEqual(result['turns'][0]['speaker'], 'Mówca 1')
        self.assertEqual(result['turns'][1]['speaker'], result['turns'][2]['speaker'])
        self.assertNotEqual(result['turns'][0]['speaker'], result['turns'][1]['speaker'])

    def test_boundary_repeated_word_and_context_recovery(self):
        parts = [
            {'block': {'core_start': 0, 'core_end': 10}, 'words': [
                {'start': 9.5, 'end': 9.8, 'text': 'tak'}, {'start': 10.6, 'end': 10.9, 'text': 'zachowaj'}]},
            {'block': {'core_start': 10, 'core_end': 20}, 'words': [
                {'start': 10.1, 'end': 10.4, 'text': 'tak'}]},
        ]
        result = join_blocks(parts)
        self.assertEqual([word['text'] for word in result], ['tak', 'tak', 'zachowaj'])
        self.assertTrue(result[-1]['recovered_from_context'])

    def test_overlap_and_uncertain_words_preserved(self):
        raw = [{'start': 0, 'end': 2, 'speaker': 'Mówca 1'}, {'start': 1, 'end': 3, 'speaker': 'Mówca 2'}]
        exclusive = [{'start': 0, 'end': 1.5, 'speaker': 'Mówca 1'}, {'start': 1.5, 'end': 3, 'speaker': 'Mówca 2'}]
        words = [{'start': 1.2, 'end': 1.8, 'text': 'razem'}, {'start': 4, 'end': 5, 'text': 'nieznany'}]
        assigned = assign_words(words, {'turns': raw, 'exclusive': exclusive})
        self.assertEqual(assigned[0]['speaker'], 'Mówca nieustalony')
        self.assertTrue(assigned[0]['overlap'])
        self.assertEqual(assigned[1]['speaker'], 'Mówca nieustalony')
        self.assertEqual(len(cues_from_words(assigned)), 2)

    def test_error_classes(self):
        self.assertEqual(classify_error("Sign in to confirm you're not a bot"), 'blocked')
        self.assertEqual(classify_error('HTTP Error 429: Too Many Requests'), 'blocked')
        self.assertEqual(classify_error('Private video'), 'unavailable')
        self.assertEqual(classify_error('Sign in to confirm your age'), 'login_required')
        self.assertEqual(classify_error('This live event will begin in 2 hours'), 'scheduled')
        self.assertEqual(classify_error('HTTP Error 503'), 'error')


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def report(self):
        return {'identity': 'test', 'title': 'Rozmowa', 'duration': 3, 'segments': [
            {'start': 1, 'end': 2, 'speaker': 'Mówca 1', 'text': 'Polski i English <test>'}]}

    def test_export_integrity_and_partial_failure(self):
        export(self.root, self.report(), 'version')
        self.assertTrue(verify_outputs(self.root, 'test', 'version'))
        output_paths(self.root)['txt'].write_text('broken')
        self.assertFalse(verify_outputs(self.root, 'test', 'version'))
        failed_folder, failed_report = self.root / 'failure', self.report()
        with patch('batch.exporter.atomic_new_bytes', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                export(failed_folder, failed_report, 'version')
        self.assertFalse(verify_outputs(self.root / 'failure', 'test', 'version'))

    def test_cleanup_never_deletes_local_source_or_outside(self):
        source = self.root / 'original.wav'
        source.write_bytes(b'original')
        work = self.root / 'work'
        work.mkdir()
        (work / 'audio.f32le').write_bytes(b'pcm')
        cleanup(work, {'kind': 'local', 'source': str(source)})
        self.assertTrue(source.exists())
        self.assertFalse((work / 'audio.f32le').exists())
        with self.assertRaises(ValueError):
            remove_work_file(source, work)

    def test_checkpoint_corruption_is_not_reused(self):
        path = self.root / 'block.json'
        checkpoint(path, {'signature': 'abc', 'words': []})
        self.assertIsNotNone(valid_checkpoint(path, 'abc'))
        self.assertIsNone(valid_checkpoint(path, 'different'))
        data = json.loads(path.read_text())
        data['words'] = ['tampered']
        atomic_json(path, data)
        self.assertIsNone(valid_checkpoint(path, 'abc'))

    def test_retained_audio_is_part_of_completion_manifest(self):
        audio = self.root / 'audio'
        audio.mkdir()
        (audio / 'wokal.flac').write_bytes(b'vocal data')
        report = self.report()
        report['retained_audio'] = ['audio/wokal.flac']
        export(self.root, report, 'version')
        self.assertTrue(verify_outputs(self.root, 'test', 'version'))
        cleanup(self.root / 'robocze', {'kind': 'youtube'})
        self.assertTrue((audio / 'wokal.flac').exists())
        (audio / 'wokal.flac').write_bytes(b'corrupt')
        self.assertFalse(verify_outputs(self.root, 'test', 'version'))

    def test_crash_resume_recomputes_only_incomplete_asr_block(self):
        from types import SimpleNamespace
        config = defaults()
        config.update(asr_seconds=10, asr_context=1)
        pcm = self.root / 'audio.f32le'
        np.ones(16000 * 25, dtype='<f4').tofile(pcm)
        engine = ASR(config, self.root)
        calls = []
        class FakePipeline:
            def get_generation_config(self):
                return SimpleNamespace(lang_to_id={})
            def generate(self, samples, **kwargs):
                calls.append(len(samples))
                if len(calls) == 2:
                    raise RuntimeError('simulated crash')
                return SimpleNamespace(texts=[' ok'], chunks=[], words=[SimpleNamespace(start_ts=1, end_ts=2, word=' ok')])
        engine.pipe = FakePipeline()
        job = {'language': 'auto'}
        with self.assertRaises(RuntimeError):
            engine.run(pcm, 25, 'pcm', job, self.root, lambda *args: None)
        first = (self.root / 'asr/000000.json').read_bytes()
        result = engine.run(pcm, 25, 'pcm', job, self.root, lambda *args: None)
        self.assertEqual(len(calls), 4)
        self.assertEqual((self.root / 'asr/000000.json').read_bytes(), first)
        self.assertEqual(len(result['blocks']), 3)

    def test_real_decode_selects_requested_track_and_reuses_pcm(self):
        config = defaults()
        if not Path(config['ffmpeg']).is_file() or not Path(config['ffprobe']).is_file():
            self.skipTest('Integration check requires FFmpeg and FFprobe on PATH.')
        source = self.root / 'stereo.mkv'
        subprocess.run([config['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-f', 'lavfi', '-i',
            'sine=frequency=880:duration=1', '-map', '0:a', '-map', '1:a', '-c:a', 'pcm_s16le', str(source)], check=True)
        work = self.root / 'work'
        work.mkdir()
        pcm, duration, key = decode(source, work, config, 1, lambda *args: None)
        audio = np.fromfile(pcm, dtype='<f4')
        frequency = np.argmax(abs(np.fft.rfft(audio))) / duration
        self.assertAlmostEqual(frequency, 880, delta=2)
        stamp = pcm.stat().st_mtime_ns
        decode(source, work, config, 1, lambda *args: None)
        self.assertEqual(stamp, pcm.stat().st_mtime_ns)
        self.assertTrue(source.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
