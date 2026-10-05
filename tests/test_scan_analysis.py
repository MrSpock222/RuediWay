import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import server
from app.results import ResultStore
from app.scanning.analysis import ScanAnalysis
from app.scanning.service import ScanManager


class ScanAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scanner = ScanManager(self.root / 'scans')
        self.busy = threading.Lock()
        self.results = ResultStore()
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('Beantworte alle Aufgaben der einen Seite.', encoding='utf-8')
        self.backend = Mock(return_value='1. Antwort\n\n2. Weitere Antwort')
        self.analysis = ScanAnalysis(self.scanner, self.busy, lambda: False, self.backend,
                                     self.results.publish, self.prompt, self.root / 'captures')
        self.ids = ['a' * 32, 'b' * 32]
        for scan_id in self.ids:
            folder = self.scanner.root / scan_id
            folder.mkdir(parents=True)
            (folder / 'metadata.json').write_text(json.dumps(dict(id=scan_id, saved=True,
                state='complete', coverage=99.9, coverage_basis='text')), encoding='utf-8')
            (folder / 'page.png').write_bytes(b'color original')
            (folder / 'document.png').write_bytes(b'contrast original')

    def tearDown(self):
        if self.analysis.thread:
            self.analysis.thread.join(3)
        self.scanner.close()
        self.temp.cleanup()

    def finish(self):
        self.analysis.thread.join(3)
        self.assertFalse(self.analysis.thread.is_alive())
        self.assertFalse(self.busy.locked())

    def test_explicit_analysis_passes_two_views_persists_and_publishes(self):
        seen = {}
        def run(images, prompt, output):
            seen['images'] = [p.name for p in images]
            seen['data'] = [p.read_bytes() for p in images]
            seen['folder'] = images[0].parent
            seen['prompt'] = prompt
            self.assertTrue(self.busy.locked())
            return '1. Antwort\n\n2. Weitere Antwort'
        self.backend.side_effect = run
        self.assertEqual(self.analysis.snapshot(self.ids[0])['state'], 'idle')
        self.backend.assert_not_called()
        self.analysis.start(self.ids[0])
        self.finish()
        self.assertEqual(seen['images'], ['page.png', 'document.png'])
        self.assertEqual(seen['data'], [b'color original', b'contrast original'])
        self.assertFalse(seen['folder'].exists())
        self.assertIn('keine Lesbarkeitsgarantie', seen['prompt'])
        saved = self.analysis.snapshot(self.ids[0])
        self.assertEqual(saved['state'], 'complete')
        self.assertEqual(self.results.snapshot()['answer'], saved['answer'])
        self.assertEqual(self.results.snapshot()['version'], 1)
        self.analysis.current = None
        self.assertEqual(self.analysis.snapshot(self.ids[0])['answer'], saved['answer'])
        self.assertEqual((self.scanner.root / self.ids[0] / 'page.png').read_bytes(), b'color original')

    def test_duplicate_is_idempotent_and_other_work_is_locked_out(self):
        release = threading.Event()
        self.backend.side_effect = lambda *args: (release.wait(2), 'Answer')[1]
        try:
            self.analysis.start(self.ids[0])
            self.assertEqual(self.analysis.start(self.ids[0])['state'], 'running')
            with self.assertRaises(HTTPException) as error:
                self.analysis.start(self.ids[1])
            self.assertEqual(error.exception.status_code, 409)
            self.assertTrue(self.busy.locked())
        finally:
            release.set()
        self.finish()
        self.assertEqual(self.analysis.start(self.ids[0])['state'], 'complete')
        self.assertEqual(self.backend.call_count, 1)
        self.assertEqual(self.results.snapshot()['version'], 1)

    def test_failure_retains_previous_result_and_allows_explicit_retry(self):
        self.results.publish('Previous answer')
        self.backend.side_effect = RuntimeError('Codex hat das Zeitlimit überschritten.')
        with self.assertLogs('app.scanning.analysis', level='ERROR'):
            self.analysis.start(self.ids[0])
            self.finish()
        status = self.analysis.snapshot(self.ids[0])
        self.assertEqual(status['state'], 'error')
        self.assertIn('Zeitlimit', status['message'])
        self.assertEqual(self.results.snapshot()['answer'], 'Previous answer')
        self.backend.side_effect = None
        self.analysis.start(self.ids[0])
        self.finish()
        self.assertEqual(self.analysis.snapshot(self.ids[0])['state'], 'complete')

    def test_restart_marks_abandoned_request_as_retryable_not_running(self):
        folder = self.scanner.root / self.ids[0]
        self.analysis.persist(folder, {'scan_id':self.ids[0], 'state':'running'})
        self.assertEqual(self.analysis.snapshot(self.ids[0])['state'], 'error')
        self.backend.assert_not_called()
        self.analysis.start(self.ids[0])
        self.finish()
        self.backend.assert_called_once()

    def test_partial_scan_context_and_missing_input_releases_lock(self):
        folder = self.scanner.root / self.ids[0]
        metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        metadata['state'] = 'partial'
        (folder / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        self.analysis.start(self.ids[0])
        self.finish()
        self.assertIn('Teilscan', self.backend.call_args.args[1])
        (self.scanner.root / self.ids[1] / 'document.png').unlink()
        with self.assertRaises(HTTPException):
            self.analysis.start(self.ids[1])
        self.assertFalse(self.busy.locked())
        self.assertEqual(self.backend.call_count, 1)

    def test_active_scan_and_active_photo_analysis_prevent_start(self):
        with patch.object(self.scanner, 'is_active', return_value=True):
            with self.assertRaises(HTTPException):
                self.analysis.start(self.ids[0])
        self.busy.acquire()
        try:
            with self.assertRaises(HTTPException):
                self.analysis.start(self.ids[0])
            self.assertTrue(self.busy.locked())
        finally:
            self.busy.release()
        self.backend.assert_not_called()

    def test_host_routes_require_local_access_and_do_not_trigger_on_get(self):
        with patch.object(server, 'SCAN_ANALYSIS', self.analysis):
            local = TestClient(server.app, base_url='http://127.0.0.1', client=('127.0.0.1', 40000))
            remote = TestClient(server.app, base_url='http://127.0.0.1', client=('192.168.1.50', 40000))
            route = '/host/scans/' + self.ids[0] + '/analysis'
            self.assertEqual(local.get(route).json()['state'], 'idle')
            self.assertEqual(remote.get(route).status_code, 403)
            self.assertEqual(remote.post(route).status_code, 403)
            self.assertEqual(local.post(route, headers={'Origin':'http://evil.example'}).status_code, 403)
            self.assertEqual(local.post('/host/scans/not-a-scan/analysis').status_code, 404)
            self.backend.assert_not_called()
            self.assertEqual(local.post(route).status_code, 200)
            self.finish()
            self.assertEqual(local.get(route).json()['answer'], self.results.snapshot()['answer'])
