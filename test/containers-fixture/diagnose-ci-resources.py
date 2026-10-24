#!/usr/bin/env python3
"""Sample only the owned resource probe while the canonical CI owner runs."""
import argparse
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ci_owner', HERE / 'with-ci-macos-engine.py')
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
helper = owner.pipeline.helper
require = helper.require
LIMIT = 1 << 20


def request(engine, path):
    engine.unchanged()
    connection = http.client.HTTPConnection('localhost', timeout=1)
    connection.sock = socket.socket(socket.AF_UNIX)
    connection.sock.settimeout(1)
    try:
        connection.sock.connect(str(engine.path))
        connection.request('GET', path)
        response = connection.getresponse()
        data = response.read(LIMIT + 1)
        require(len(data) <= LIMIT, 'DIAGNOSTIC_RESPONSE_LIMIT')
        engine.unchanged()
        return response.status, data
    finally:
        connection.close()


def select_probe(rows, image):
    require(isinstance(rows, list), 'CONTAINER_LIST_REQUIRED')
    matches = [row for row in rows if row.get('ImageID') == image
               and any(re.fullmatch('/oc-qualification-[0-9a-f-]{36}', name)
                       for name in row.get('Names', []))]
    require(len(matches) <= 1, 'AMBIGUOUS_RESOURCE_PROBE')
    if matches:
        identity = matches[0]['Id']
        require(re.fullmatch('[0-9a-f]{64}', identity), 'RESOURCE_PROBE_ID')
        return identity
    return None


def processes(value):
    require(value.get('Titles') == ['PID', 'COMMAND'], 'COMM_ONLY_PROCESS_LIST_REQUIRED')
    rows = value['Processes'] or []
    require(len(rows) <= 16 and all(isinstance(row, list) and len(row) == 2
                                  and isinstance(row[0], str) and row[0].isdigit()
                                  and isinstance(row[1], str) for row in rows),
            'BOUNDED_PROCESS_LIST_REQUIRED')
    allowed = {'sh', 'dash', 'mv', 'dd', 'wc', 'cat', 'busybox'}
    return sorted(row[1] if row[1] in allowed else 'other' for row in rows)


def changed_files(rows):
    require(isinstance(rows, list), 'CHANGED_FILES_REQUIRED')
    paths = {row['Path'] for row in rows}
    return {'quotaFile': '/quota-probe' in paths,
            'rootMarker': '/open-compute-snapshot-probe' in paths,
            'movedHelper': any(p == '/.open-compute-exec-moved'
                              or p.startswith('/.open-compute-exec-moved/') for p in paths)}


def archive_category(status, data):
    if status in (200, 404):
        return 'readable' if status == 200 else 'absent'
    if status == 500:
        value = json.loads(data)
        message = value.get('message', '') if isinstance(value, dict) else ''
        if isinstance(message, str) and message.startswith('Error setting up pivot dir: mkdir '):
            for suffix, category in [(': no space left on device', 'pivot_enospc'),
                                     (': disk quota exceeded', 'pivot_edquot')]:
                if message.endswith(suffix):
                    return category
    return 'other_archive_error'


def observe(root, image, stop, rows):
    engine = None
    deadline = time.monotonic() + 1200
    identity = None
    while time.monotonic() < deadline and len(rows) < 120 and not stop.is_set():
        try:
            if engine is None:
                path = root / 'colima/fixture/docker.sock'
                # The canonical pipeline creates this directory after guest setup.
                if not (root / 'fixture').is_dir() or not path.is_socket():
                    stop.wait(1)
                    continue
                engine = helper.Engine(path.resolve())
            status, data = request(engine, '/containers/json?all=true')
            require(status == 200, 'CONTAINER_LIST_STATUS')
            selected = select_probe(json.loads(data), image)
            if selected is None:
                if identity is not None:
                    rows.append({'timeNano': time.time_ns(), 'probeAbsent': True})
                    return
                stop.wait(1)
                continue
            require(identity in (None, selected), 'RESOURCE_PROBE_CHANGED')
            identity = selected
            row = {'timeNano': time.time_ns()}
            rows.append(row)
            # Docker needs PID to select the container's rows; never retain it.
            status, data = request(engine, f'/containers/{identity}/top?ps_args=-o%20pid,comm')
            row['topStatus'] = status
            if status == 200:
                row['processes'] = processes(json.loads(data))
            status, data = request(engine, f'/containers/{identity}/changes')
            row['changesStatus'] = status
            if status == 200:
                row.update(changed_files(json.loads(data) or []))
            status, data = request(engine,
                f'/containers/{identity}/archive?path=%2Fopen-compute-snapshot-probe')
            row.update(archiveStatus=status, archive=archive_category(status, data))
        except Exception as error:
            rows.append({'timeNano': time.time_ns(), 'failureClass': type(error).__name__})
        stop.wait(1)


def run(images, binary, root, evidence):
    require(root.is_absolute() and root.parent.resolve() == root.parent
            and not os.path.lexists(root) and len(os.fsencode(root)) <= 48
            and not os.path.lexists(evidence), 'FRESH_SHORT_OWNED_ROOTS_REQUIRED')
    _, _, raw = owner.pipeline.consumer.oci.regular(images / 'images.json', 65536, True)
    image = 'sha256:' + json.loads(raw)['images']['qualification']['configSha256']
    require(re.fullmatch('sha256:[0-9a-f]{64}', image), 'QUALIFICATION_IMAGE_REQUIRED')
    stop, rows = threading.Event(), []
    thread = threading.Thread(target=observe, args=(root, image, stop, rows), daemon=True)
    thread.start()
    try:
        result = owner.run(images, binary, root, evidence,
            [sys.executable, '-I', '-B', '-c', 'print("Broker qualification only; no product Gate")'])
    finally:
        stop.set()
        thread.join(timeout=6)
        require(not thread.is_alive(), 'RESOURCE_OBSERVER_REMAINS')
        if evidence.is_dir():
            raw = ''.join(json.dumps(row) + '\n' for row in rows).encode()
            require(len(raw) <= LIMIT, 'DIAGNOSTIC_OUTPUT_LIMIT')
            fd = os.open(evidence / 'resource-observations.jsonl',
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(raw)
    return result


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('images', 'binary', 'root', 'evidence'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = run(args.images, args.binary, args.root, args.evidence)
    print(json.dumps(result))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
