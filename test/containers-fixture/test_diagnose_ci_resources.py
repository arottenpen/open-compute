"""Check that the temporary sampler reads only its probe and emits fixed fields."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('resource_diagnostic',
    Path(__file__).with_name('diagnose-ci-resources.py'))
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)
IMAGE = 'sha256:' + 'a' * 64
IDENTITY = 'b' * 64


def probe(**changes):
    return dict({'Id': IDENTITY, 'ImageID': IMAGE,
                 'Names': ['/oc-qualification-12345678-1234-1234-1234-123456789abc']}, **changes)


class ResourceDiagnosticTests(unittest.TestCase):
    def test_selects_only_the_exact_image_and_provider_probe_name(self):
        self.assertEqual(diagnostic.select_probe([probe()], IMAGE), IDENTITY)
        for row in [probe(ImageID='sha256:' + 'c' * 64),
                    probe(Names=['/tenant']), probe(Names=['/oc-qualification-arbitrary'])]:
            self.assertIsNone(diagnostic.select_probe([row], IMAGE))
        with self.assertRaises(ValueError):
            diagnostic.select_probe([probe(), probe()], IMAGE)
        with self.assertRaises(ValueError):
            diagnostic.select_probe([probe(Id='unsafe/path')], IMAGE)

    def test_process_output_never_retains_arguments_or_unknown_names(self):
        self.assertEqual(diagnostic.processes({'Titles': ['PID', 'COMMAND'],
            'Processes': [['1', 'sh'], ['2', 'dd'], ['3', 'private-canary']]}), ['dd', 'other', 'sh'])
        for value in [{'Titles': ['CMD'], 'Processes': [['sh -c private-canary']]},
                      {'Titles': ['COMMAND'], 'Processes': [['sh', 'private-canary']]},
                      {'Titles': ['PID', 'COMMAND'], 'Processes': [['1', 'sh']] * 17},
                      {'Titles': ['PID', 'COMMAND'], 'Processes': [['private-canary', 'sh']]}]:
            with self.assertRaises(ValueError):
                diagnostic.processes(value)

    def test_changes_emit_only_three_booleans(self):
        self.assertEqual(diagnostic.changed_files([{'Path': p} for p in (
            '/quota-probe', '/open-compute-snapshot-probe',
            '/.open-compute-exec-moved/helper', '/private-canary')]),
            {'quotaFile': True, 'rootMarker': True, 'movedHelper': True})

    def test_archive_errors_have_closed_categories_and_no_raw_message(self):
        for suffix, expected in [('no space left on device', 'pivot_enospc'),
                                 ('disk quota exceeded', 'pivot_edquot'),
                                 ('permission denied', 'other_archive_error')]:
            body = json.dumps({'message': 'Error setting up pivot dir: mkdir '
                               '/private-canary/.pivot_root123: ' + suffix}).encode()
            self.assertEqual(diagnostic.archive_category(500, body), expected)
        self.assertEqual(diagnostic.archive_category(200, b'private-canary'), 'readable')
        self.assertEqual(diagnostic.archive_category(404, b'private-canary'), 'absent')
        self.assertEqual(diagnostic.archive_category(403, b'private-canary'), 'other_archive_error')

    def test_live_observer_projects_each_response_and_terminates(self):
        rows, stop = [], threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'fixture').mkdir()
            path = root / 'colima/fixture/docker.sock'
            path.parent.mkdir(parents=True)
            import socket
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))

                def request(engine, route):
                    if route == '/containers/json?all=true':
                        return 200, json.dumps([probe()]).encode()
                    if '/top?' in route:
                        self.assertTrue(route.endswith('ps_args=-o%20pid,comm'))
                        return 200, b'{"Titles":["PID","COMMAND"],"Processes":[["1","sh"]]}'
                    if route.endswith('/changes'):
                        return 200, b'[{"Path":"/open-compute-snapshot-probe"}]'
                    stop.set()
                    return 200, b'private-canary-marker-body'

                with patch.object(diagnostic, 'request', request):
                    diagnostic.observe(root, IMAGE, stop, rows)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['archive'], 'readable')
        self.assertEqual(rows[0]['processes'], ['sh'])
        self.assertTrue(rows[0]['rootMarker'])
        raw = json.dumps(rows)
        for private in [IDENTITY, IMAGE, 'private-canary', '/open-compute']:
            self.assertNotIn(private, raw)

    def test_existing_roots_fail_before_an_observer_or_owner_starts(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)
            with patch.object(diagnostic.owner, 'run') as owner:
                with self.assertRaises(ValueError):
                    diagnostic.run(root, root, root, root / 'evidence')
                owner.assert_not_called()

    def test_sampler_does_not_replace_the_canonical_failure_or_leave_a_thread(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            base = Path(directory).resolve()
            images, root, evidence = base / 'images', base / 'vm', base / 'evidence'
            images.mkdir()
            (images / 'images.json').write_text(json.dumps(
                {'images': {'qualification': {'configSha256': 'a' * 64}}}))

            def failed_owner(*args):
                self.assertEqual(args[:4], (images, base / 'ocd', root, evidence))
                self.assertIn('no product Gate', args[4][-1])
                evidence.mkdir()
                return {'success': False, 'mainExecuted': False}

            before = set(threading.enumerate())
            with patch.object(diagnostic.owner, 'run', failed_owner):
                result = diagnostic.run(images, base / 'ocd', root, evidence)
            self.assertEqual(result, {'success': False, 'mainExecuted': False})
            self.assertEqual(set(threading.enumerate()), before)
            output = evidence / 'resource-observations.jsonl'
            self.assertEqual(output.read_bytes(), b'')
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
