"""Opt-in real integration checks; never starts the user's production queue."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time

from batch.common import APP, NO_WINDOW, WorkerLock, atomic_json, digest, read_json, settings
from batch.exporter import verify_outputs
from batch.result_paths import output_paths
from batch.importers import import_local
from batch.store import Store
from batch.worker import launch


def wait_for(predicate, timeout=120):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise TimeoutError("Warunek testu nie został spełniony.")


def wait_memory(process, store, timeout=180):
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    query = ctypes.WinDLL('psapi').GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    peak, end = 0, time.monotonic() + timeout
    while process.poll() is None:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        pid = store.control()['pid']
        handle = kernel.OpenProcess(0x410, False, pid) if pid else None
        if handle:
            try:
                if query(handle, ctypes.byref(counters), counters.cb):
                    peak = max(peak, counters.PeakWorkingSetSize)
            finally:
                kernel.CloseHandle(handle)
        if time.monotonic() > end:
            raise TimeoutError('Przekroczono czas próby')
        time.sleep(.2)
    return round(peak / 1024 ** 2, 1)


def window_reconnect(root, sample, model):
    import tkinter as tk
    from batch.gui import Window
    root = root / 'gui-reconnect'
    config = settings(root)
    config.update(diarization=False, asr_seconds=7, asr_context=1, model=str(model))
    atomic_json(root / 'ustawienia.json', config)
    store = Store(root)
    import_local(store, [sample])
    before = digest(sample)
    window = tk.Tk()
    window.withdraw()
    app = Window(root, window)
    process = launch(root)
    wait_for(lambda: store.control()['current_id'])
    app.stop()
    app.close()
    assert WorkerLock.busy(root), 'Zamknięcie okna zakończyło wykonawcę'
    second = tk.Tk()
    second.withdraw()
    reopened = Window(root, second)
    second.update()
    assert len(reopened.tree.get_children()) == 1
    reopened.close()
    process.wait(timeout=120)
    job = store.jobs()[0]
    assert job['status'] == 'done', job['error']
    assert digest(sample) == before
    assert verify_outputs(root / 'wyniki' / job['folder'], job['identity'])
    assert not (root / 'wyniki' / job['folder'] / 'robocze/audio.f32le').exists()
    return {'passed': True, 'output': str(root / 'wyniki' / job['folder']), 'original_unchanged': True}


def crash_resume(root, sample_source, model):
    root = root / 'crash-resume'
    config = settings(root)
    config.update(diarization=False, asr_seconds=8, asr_context=1, model=str(model))
    atomic_json(root / 'ustawienia.json', config)
    sample = root / 'repeated.wav'
    if not sample.exists():
        subprocess.run([config['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-y', '-stream_loop', '7',
            '-i', str(sample_source), '-c:a', 'pcm_s16le', str(sample)],
            check=True, creationflags=NO_WINDOW)
    store = Store(root)
    import_local(store, [sample])
    job = store.jobs()[0]
    work = root / 'wyniki' / job['folder'] / 'robocze'
    process = launch(root)
    first = work / 'asr/000000.json'
    wait_for(first.exists)
    saved = first.read_bytes()
    process.kill()
    process.wait(timeout=10)
    assert store.jobs()[0]['status'] == 'running'
    assert not WorkerLock.busy(root)
    resume = launch(root)
    resume.wait(timeout=180)
    job = store.jobs()[0]
    assert job['status'] == 'done', job['error']
    assert first.read_bytes() == saved
    checkpoints = list((work / 'asr').glob('*.json'))
    return {'passed': True, 'first_checkpoint_reused': True, 'blocks': len(checkpoints),
            'attempts': job['attempts'], 'output': str(root / 'wyniki' / job['folder'])}


def child_lifetime(root, _sample, _model):
    directory = root / 'child-lifetime'
    directory.mkdir(parents=True, exist_ok=True)
    pid_file = directory / 'child-pid.txt'
    code = ('import subprocess,sys,time; from pathlib import Path; from batch.processes import ChildGuard; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"]); '
            'g=ChildGuard(p); g.__enter__(); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(120)')
    parent = subprocess.Popen([sys.executable, '-c', code, str(pid_file)], cwd=APP, creationflags=NO_WINDOW)
    wait_for(pid_file.exists, timeout=20)
    child_pid = int(pid_file.read_text())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, child_pid)
    assert handle
    try:
        parent.kill()
        parent.wait(timeout=10)
        assert kernel.WaitForSingleObject(handle, 10000) == 0, 'Osierocony proces obliczeń nadal działa'
    finally:
        kernel.CloseHandle(handle)
    return {'passed': True, 'child_terminated_with_parent': True}


def long_silence(root, _sample, model):
    root = root / 'three-hour-silence'
    root.mkdir(parents=True, exist_ok=True)
    config = settings(root)
    config['diarization'] = False
    config['model'] = str(model)
    atomic_json(root / 'ustawienia.json', config)
    source = root / 'silence-3h.flac'
    subprocess.run([config['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
        '-i', 'anullsrc=r=16000:cl=mono', '-t', '10800', '-c:a', 'flac', str(source)],
        check=True, creationflags=NO_WINDOW)
    store = Store(root)
    import_local(store, [source])
    process = launch(root)
    peak_mb = wait_memory(process, store)
    job = store.jobs()[0]
    assert job['status'] == 'done', job['error']
    report = read_json(output_paths(root / 'wyniki' / job['folder'])['json'])
    assert len(report['asr']['blocks']) == 18
    assert not report['words']
    assert not (root / 'wyniki' / job['folder'] / 'robocze/audio.f32le').exists()
    return {'passed': True, 'duration_s': report['duration'], 'blocks': 18,
            'worker_peak_working_set_mib': peak_mb,
            'scope': '3h decode, silence fast path, checkpoints and cleanup; no speaker-quality test'}


def check_fresh_validation_root(root):
    root = Path(root)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError('Podaj nowy albo pusty katalog walidacji. Istniejące dane testów nie będą ponownie używane.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--sample', type=Path, required=True, help='Własne krótkie nagranie mowy do prób.')
    parser.add_argument('--model', type=Path, required=True, help='Lokalny kompletny model Whisper OpenVINO.')
    args = parser.parse_args()
    args.root, args.sample, args.model = args.root.resolve(), args.sample.resolve(), args.model.resolve()
    try:
        check_fresh_validation_root(args.root)
    except ValueError as exc:
        parser.error(str(exc))
    if sys.platform != 'win32':
        print('SKIP: próby procesów i pomiaru pamięci wymagają Windows.')
        return 2
    try:
        from transcribe import validate_model
        validate_model(args.model)
        if not args.sample.is_file():
            raise FileNotFoundError('Nie znaleziono próbki podanej przez --sample.')
    except (ValueError, FileNotFoundError) as exc:
        print('SKIP: brak danych integracyjnych: ' + str(exc))
        return 2
    args.root.mkdir(parents=True, exist_ok=True)
    results = {}
    for test in (window_reconnect, crash_resume, child_lifetime, long_silence):
        print(test.__name__, flush=True)
        try:
            results[test.__name__] = test(args.root, args.sample, args.model)
        except Exception as exc:
            results[test.__name__] = {'passed': False, 'error': str(exc)}
        atomic_json(args.root / 'runtime-tests.json', results)
        print(json.dumps(results[test.__name__], ensure_ascii=False), flush=True)
    return 0 if all(r['passed'] for r in results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
