"""Keep large text outside SQLite while retaining structured state in rows."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class PayloadStore:
    def __init__(self, directory: Path, threshold: int = 16 * 1024):
        self.directory = directory
        self.threshold = threshold

    def dumps(self, value: object) -> str:
        references = []

        def encode(item, path):
            if isinstance(item, str) and len(item.encode('utf-8')) > self.threshold:
                data = item.encode('utf-8')
                digest = hashlib.sha256(data).hexdigest()
                self.directory.mkdir(parents=True, exist_ok=True)
                target = self.directory / digest
                if not target.exists():
                    fd, temporary = tempfile.mkstemp(dir=self.directory)
                    try:
                        with os.fdopen(fd, 'wb') as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, target)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                references.append({'path': path, 'sha256': digest, 'bytes': len(data)})
                return None
            if isinstance(item, dict):
                return {key: encode(child, [*path, key]) for key, child in item.items()}
            if isinstance(item, list):
                return [encode(child, [*path, index]) for index, child in enumerate(item)]
            return item

        encoded = encode(value, [])
        if not references:
            return json.dumps(value, ensure_ascii=False)
        return json.dumps({'payload_format': 'picli-artifacts-v1', 'value': encoded,
                           'references': references}, ensure_ascii=False)

    def loads(self, text: str):
        envelope = json.loads(text)
        # Existing databases contain unwrapped JSON and remain readable.
        if not isinstance(envelope, dict) or envelope.get('payload_format') != 'picli-artifacts-v1':
            return envelope
        value = envelope['value']
        for reference in envelope['references']:
            digest = reference['sha256']
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('Invalid artifact reference')
            data = (self.directory / digest).read_bytes()
            if len(data) != reference['bytes'] or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Artifact integrity check failed')
            restored = data.decode('utf-8')
            path = reference['path']
            if not path:
                value = restored
            else:
                parent = value
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = restored
        return value
