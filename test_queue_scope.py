"""Queue-kind isolation, durable migration and launch coordination, offline."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from batch.common import ResourceError, WorkerLock, defaults
from batch.store import Store
from batch.worker import Worker, launch, preflight, wait_for_launch


def mixed_rows(root):
    for index in range(3):
        yield {"identity": f"yt:abcdefghij{index}", "kind": "youtube", "title": f"YouTube {index}",
               "source": f"https://www.youtube.com/watch?v=abcdefghij{index}", "date": f"2020-01-0{index * 2 + 1}"}
        yield {"identity": f"local:{index:064x}", "kind": "local", "title": f"Lokalne {index}",
               "source": str(root / f"sample-{index}.wav"), "date": f"2020-01-0{index * 2 + 2}"}


class ScopedQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.store.add_many(mixed_rows(self.root))
        self.youtube = [row['id'] for row in self.store.jobs() if row['kind'] == 'youtube']
        self.local = [row['id'] for row in self.store.jobs() if row['kind'] == 'local']

    def tearDown(self):
        self.temp.cleanup()

    def finish_claims(self):
        processed = []
        while (job := self.store.claim()) is not None:
            processed.append(job['id'])
            self.store.update(job['id'], status='done')
        return processed

    def test_local_start_never_claims_youtube_and_youtube_never_claims_local(self):
        self.store.start(kind='local')
        self.assertEqual(self.finish_claims(), self.local)
        self.assertTrue(all(self.store.get(item)['status'] == 'pending' for item in self.youtube))
        self.store.start(kind='youtube')
        self.assertEqual(self.finish_claims(), self.youtube)

    def test_selected_ids_preserve_other_enabled_flags_and_chronological_order(self):
        self.store.configure([self.youtube[1], self.local[2]], enabled=0)
        before = {row['id']: row['enabled'] for row in self.store.jobs()}
        self.store.start(kind='local', job_ids=[self.local[2], self.local[0], self.local[0]])
        self.assertEqual(self.finish_claims(), [self.local[0]])
        self.assertEqual({row['id']: row['enabled'] for row in self.store.jobs()}, before)
        self.assertEqual(json.loads(self.store.control()['scope_ids']), [self.local[0], self.local[2]])
        self.assertEqual(self.store.get(self.local[1])['status'], 'pending')

    def test_empty_unknown_and_mixed_selections_do_not_mutate_queue(self):
        before_control, before_jobs = self.store.control(), self.store.jobs()
        for kind, ids in [('local', []), ('local', [999999]), ('local', [self.youtube[0]]),
                          ('youtube', [self.youtube[0], self.local[0]])]:
            with self.subTest(kind=kind, ids=ids), self.assertRaises(ValueError):
                self.store.start(kind=kind, job_ids=ids)
            self.assertEqual(self.store.control(), before_control)
            self.assertEqual(self.store.jobs(), before_jobs)

    def test_control_rejects_bad_scope_fields_and_canonicalizes_json_ids(self):
        before = self.store.control()
        for kind in ('video', None, 1):
            with self.assertRaises(ValueError):
                self.store.control(scope_kind=kind)
        for ids in ('invalid-json', '{}', '[true]', '[0]', '[-2]', '[1.5]', '["1"]', None):
            with self.assertRaises(ValueError):
                self.store.control(scope_ids=ids)
        self.assertEqual(self.store.control(), before)
        result = self.store.control(scope_kind='local', scope_ids=json.dumps([self.local[1], self.local[0], self.local[1]]))
        self.assertEqual(json.loads(result['scope_ids']), self.local[:2])

    def test_start_retries_only_enabled_jobs_in_the_requested_scope(self):
        for job in self.store.jobs():
            self.store.update(job['id'], status='blocked')
        self.store.update(self.local[1], status='error', stage='Wymaga działania', error='missing model')
        self.store.configure([self.local[2]], enabled=0)
        self.store.start(kind='local')
        self.assertEqual([self.store.get(item)['status'] for item in self.local], ['pending', 'pending', 'blocked'])
        self.assertTrue(all(self.store.get(item)['status'] == 'blocked' for item in self.youtube))

    def test_selected_start_does_not_retry_other_jobs_of_the_same_kind(self):
        for item in self.local:
            self.store.update(item, status='error', stage='Wymaga działania')
        self.store.start(kind='local', job_ids=[self.local[1]])
        self.assertEqual([self.store.get(item)['status'] for item in self.local], ['error', 'pending', 'error'])

    def test_stop_after_one_and_restart_preserve_kind_and_selection(self):
        wanted = [self.local[0], self.local[2]]
        self.store.start(kind='local', job_ids=wanted)
        processed = []
        def stop_after_current(job):
            processed.append(job['id'])
            self.store.control(stop=1)
            self.store.update(job['id'], status='done', progress=100)
        Worker(self.root, processor=stop_after_current).run(check=False)
        self.assertEqual(processed, [self.local[0]])
        reopened = Store(self.root)
        self.assertEqual(reopened.control()['scope_kind'], 'local')
        self.assertEqual(json.loads(reopened.control()['scope_ids']), wanted)
        reopened.control(stop=0)
        Worker(self.root, processor=lambda job: reopened.update(job['id'], status='done')).run(check=False)
        self.assertEqual([reopened.get(item)['status'] for item in self.local], ['done', 'pending', 'done'])
        self.assertTrue(all(reopened.get(item)['status'] == 'pending' for item in self.youtube))

    def test_crashed_selection_recovers_only_its_own_job(self):
        self.store.start(kind='local', job_ids=[self.local[1]])
        self.assertEqual(self.store.claim()['id'], self.local[1])
        self.store.update(self.youtube[0], status='running', stage='unrelated stale job', progress=47)
        Worker(self.root, processor=lambda job: self.store.update(job['id'], status='done')).run(check=False)
        self.assertEqual(self.store.get(self.local[1])['status'], 'done')
        self.assertEqual(self.store.get(self.youtube[0])['status'], 'running')
        self.assertEqual(self.store.get(self.youtube[0])['progress'], 47)

    def test_default_start_still_processes_the_entire_mixed_queue(self):
        self.store.start()
        self.assertEqual(self.store.control()['scope_kind'], 'all')
        self.assertEqual(self.store.control()['scope_ids'], '[]')
        self.assertEqual(self.finish_claims(), [row['id'] for row in self.store.jobs()])

    def test_csv_kind_filter_does_not_change_queue(self):
        before = self.store.control(), self.store.jobs()
        target = self.root / 'local-report.csv'
        self.store.export_csv(target, kind='local')
        with target.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream, delimiter=';'))
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row[0].startswith('local:') for row in rows[1:]))
        self.assertEqual(before, (self.store.control(), self.store.jobs()))


class MigrationTests(unittest.TestCase):
    def old_database(self, root):
        store = Store(root)
        store.add_many(mixed_rows(root))
        store.update(1, status='done', progress=100, attempts=7, error='saved note')
        store.configure([2], enabled=0, language='en', audio_track=1)
        store.control(stop=1, message='saved control', heartbeat='2020-01-01T00:00:00+00:00')
        before = store.jobs(), {key: value for key, value in store.control().items() if not key.startswith('scope_')}
        with closing(sqlite3.connect(store.path)) as db:
            db.execute('ALTER TABLE control DROP COLUMN scope_kind')
            db.execute('ALTER TABLE control DROP COLUMN scope_ids')
        return before

    def test_old_schema_migration_preserves_jobs_control_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = self.old_database(root)
            config = root / 'ustawienia.json'
            config.write_text('{"language":"en","custom":"preserve"}', encoding='utf-8')
            raw = config.read_bytes()
            migrated = Store(root)
            self.assertEqual(migrated.jobs(), before[0])
            control = migrated.control()
            self.assertEqual({key: value for key, value in control.items() if not key.startswith('scope_')}, before[1])
            self.assertEqual((control['scope_kind'], control['scope_ids']), ('all', '[]'))
            self.assertEqual(config.read_bytes(), raw)

    def test_two_concurrent_openers_migrate_old_schema_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = self.old_database(root)
            barrier = threading.Barrier(2)
            def open_store():
                barrier.wait(timeout=10)
                return Store(root).control()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(open_store) for _ in range(2)]
                controls = [future.result(timeout=15) for future in futures]
            self.assertEqual(controls[0], controls[1])
            self.assertEqual(controls[0]['scope_kind'], 'all')
            self.assertEqual(Store(root).jobs(), before[0])


class LaunchTests(unittest.TestCase):
    def test_live_worker_rejects_second_start_without_changing_stop_scope_or_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root)
            store.add_many(mixed_rows(root))
            local_id = next(row['id'] for row in store.jobs() if row['kind'] == 'local')
            started, release = threading.Event(), threading.Event()
            def process(job):
                started.set()
                if not release.wait(timeout=10):
                    raise RuntimeError('test timeout')
                store.update(job['id'], status='done')
            store.start(kind='local', job_ids=[local_id])
            thread = threading.Thread(target=lambda: Worker(root, processor=process).run(check=False))
            thread.start()
            try:
                self.assertTrue(started.wait(timeout=5))
                store.control(stop=1)
                before = store.control(), store.jobs()
                with patch('batch.worker.subprocess.Popen') as popen, self.assertRaises(ResourceError):
                    launch(root, kind='youtube')
                popen.assert_not_called()
                self.assertEqual(before, (store.control(), store.jobs()))
            finally:
                release.set()
                thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(store.get(local_id)['status'], 'done')
            self.assertTrue(all(row['status'] == 'pending' for row in store.jobs() if row['kind'] == 'youtube'))

    def test_second_start_during_handshake_preserves_first_scope_and_spawns_only_once(self):
        class PendingProcess:
            def poll(self):
                return None
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root)
            store.add_many(mixed_rows(root))
            wanted = [next(row['id'] for row in store.jobs() if row['kind'] == 'local')]
            child_lock = WorkerLock(root)
            with patch('batch.worker.subprocess.Popen', return_value=PendingProcess()) as popen:
                launch(root, kind='local', job_ids=wanted)
                try:
                    before = store.control()
                    with self.assertRaises(ResourceError):
                        launch(root, kind='youtube')
                    self.assertEqual(store.control(), before)
                    self.assertEqual(popen.call_count, 1)
                    self.assertTrue(child_lock.acquire())
                    wait_for_launch(root, timeout=5)
                    self.assertEqual(store.control()['scope_kind'], 'local')
                    self.assertEqual(json.loads(store.control()['scope_ids']), wanted)
                finally:
                    child_lock.close()

    def test_spawn_failure_pauses_the_requested_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            store.add_many(mixed_rows(Path(folder)))
            with patch('batch.worker.subprocess.Popen', side_effect=OSError('simulated spawn failure')):
                with self.assertRaises(OSError):
                    launch(folder, kind='local')
            self.assertEqual((store.control()['stop'], store.control()['scope_kind']), (1, 'local'))
            self.assertFalse(WorkerLock.busy(folder))
            self.assertFalse(WorkerLock.busy(Path(folder) / 'uruchamianie'))


class PreflightTests(unittest.TestCase):
    def test_local_resolves_tools_on_path_and_does_not_require_node(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = defaults()
            config.update(ffmpeg='test-ffmpeg', ffprobe='test-ffprobe', node='missing-node', diarization=False)
            tools = {}
            for name in ('test-ffmpeg', 'test-ffprobe'):
                tool = root / (name + '.exe')
                tool.write_bytes(b'not executed')
                tools[name] = str(tool)
            with patch('transcribe.validate_model'), patch('batch.worker.check_disk'), \
                    patch('batch.worker.shutil.which', side_effect=tools.get) as lookup:
                preflight(config, root, kind='local')
                self.assertEqual(config['ffmpeg'], tools['test-ffmpeg'])
                self.assertEqual(config['ffprobe'], tools['test-ffprobe'])
                self.assertNotIn(('missing-node',), [call.args for call in lookup.call_args_list])
                with self.assertRaisesRegex(ResourceError, 'node'):
                    preflight(config, root, kind='youtube')

    def test_worker_reads_preflight_kind_from_durable_control(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            store.add_many(mixed_rows(Path(folder)))
            store.start(kind='local')
            worker = Worker(folder, processor=lambda job: store.update(job['id'], status='done'))
            with patch('batch.worker.preflight') as check:
                worker.run()
            check.assert_called_once_with(worker.config, Path(folder), kind='local')


class CLITests(unittest.TestCase):
    def test_cli_start_defaults_to_all_and_waits_for_handshake(self):
        import kolejka
        with tempfile.TemporaryDirectory() as folder, patch('sys.argv', ['kolejka.py', '--root', folder, 'start']), \
                patch('kolejka.logging.basicConfig'), patch('batch.worker.launch') as spawn, \
                patch('batch.worker.wait_for_launch') as wait:
            kolejka.main()
            spawn.assert_called_once_with(Path(folder).resolve(), kind='all', job_ids=None)
            wait.assert_called_once_with(Path(folder).resolve())

    def test_cli_start_passes_kind_and_ids_without_rewriting_other_preferences(self):
        import kolejka
        with tempfile.TemporaryDirectory() as folder, patch('sys.argv', [
                'kolejka.py', '--root', folder, 'start', '--kind', 'local', '--ids', '2', '4']), \
                patch('kolejka.logging.basicConfig'), patch('batch.worker.launch') as spawn, \
                patch('batch.worker.wait_for_launch'):
            kolejka.main()
            spawn.assert_called_once_with(Path(folder).resolve(), kind='local', job_ids=[2, 4])

    def test_gui_local_folder_only_selects_and_prefills_without_import_or_start(self):
        import kolejka
        with tempfile.TemporaryDirectory() as folder, patch('sys.argv', [
                'kolejka.py', '--root', folder, 'gui', '--local-folder', folder]), \
                patch('kolejka.logging.basicConfig'), patch('batch.gui.Window') as window, \
                patch('batch.worker.launch') as spawn, patch('batch.importers.import_local') as add:
            app = window.return_value
            kolejka.main()
            app.local_tab.set_folder.assert_called_once_with(Path(folder))
            app.notebook.select.assert_called_once_with(app.local_page)
            app.run.assert_called_once_with()
            spawn.assert_not_called()
            add.assert_not_called()


if __name__ == '__main__':
    unittest.main()
