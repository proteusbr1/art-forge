"""Workflow provenance hashes must identify exact bytes, including CRLF on Windows."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forge.factory_core import Factory  # noqa: E402


class WorkflowHashTest(unittest.TestCase):
    def test_only_exact_local_triposplat_classes_are_allowed(self):
        for name, allowed in [('TripoSplatConditioning', True), ('TripoSplatPreprocessImage', True),
                              ('VAEDecodeTripoSplat', True), ('TripoTextToModel', False),
                              ('TripoSplatApi', False), ('MeshyTextToModel', False)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                Path(directory, 'sample.json').write_text(json.dumps({'1': {'class_type': name, 'inputs': {}}}))
                factory = Factory.__new__(Factory)
                factory.workflows = {}
                with patch('forge.factory_core.WORKFLOWS', directory):
                    if allowed:
                        factory.workflow('sample.json')
                    else:
                        with self.assertRaises(ValueError):
                            factory.workflow('sample.json')

    def test_hash_preserves_line_endings(self):
        workflow = {"1": {"class_type": "SaveImage", "inputs": {}}}
        hashes = []
        with tempfile.TemporaryDirectory() as directory:
            with patch("forge.factory_core.WORKFLOWS", directory):
                for ending in ("\n", "\r\n"):
                    raw = json.dumps(workflow, indent=2).replace("\n", ending).encode()
                    Path(directory, "sample.json").write_bytes(raw)
                    factory = Factory.__new__(Factory)
                    factory.workflows = {}
                    parsed, digest = factory.workflow("sample.json")
                    self.assertEqual(parsed, workflow)
                    self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
                    hashes.append(digest)
        self.assertNotEqual(*hashes)
