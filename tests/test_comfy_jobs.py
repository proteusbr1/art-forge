"""Prompt building of ComfyUI jobs: local mesh references and unique save names, without a GPU."""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forge.comfy_jobs import ComfyJobs  # noqa: E402
from forge.factory_util import sha_file  # noqa: E402


class NativeMeshOutputTests(unittest.TestCase):
    def test_local_mesh_reference_is_hashed_and_never_uploaded(self):
        with tempfile.TemporaryDirectory() as directory:
            mesh = Path(directory) / 'source.glb'
            mesh.write_bytes(b'mesh bytes')
            factory = ComfyJobs()
            factory.server = SimpleNamespace(url='http://127.0.0.1:8188', upload=Mock())
            factory.workflow = lambda _: ({'1': {'class_type': 'Hy3D21LoadMesh',
                                                 'inputs': {'glb_path': '$MESH$'}}}, 'hash')
            factory.resolve = lambda _: (str(mesh), sha_file(mesh))
            factory.input_record = lambda _: {'sha256': sha_file(mesh)}
            job = {'workflow': 'paint', 'id': 'things/statue/paint', 'family': 'things', 'asset': 'statue',
                   'values': {'$MESH$': {'ref': 'source', 'transport': 'local_path'}}}
            built, _ = factory.build(job)
            self.assertEqual(built['1']['inputs']['glb_path'], str(mesh.resolve()))
            factory.server.upload.assert_not_called()
            factory.server.url = 'https://remote.invalid'
            with self.assertRaisesRegex(ValueError, 'loopback'):
                factory.build(job)
            factory.server.url = 'http://127.0.0.1:8188'
            original_hash = sha_file(mesh)
            factory.resolve = lambda _: (str(mesh), original_hash)
            mesh.write_bytes(b'changed after manifest')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                factory.build(job)

    def test_native_mesh_saves_are_unique_across_jobs_and_nodes(self):
        workflow = {n: {'class_type': 'Save3DAdvanced', 'inputs': {'filename_prefix': '$PREFIX$'}}
                    for n in ('322', '403')}
        factory = ComfyJobs()
        factory.workflow = lambda _: (workflow, 'hash')
        prefixes = []
        for name in ('first', 'second'):
            built, _ = factory.build({'workflow': 'test', 'id': 'things/statue/' + name,
                                      'family': 'things', 'asset': 'statue'})
            prefixes.extend(n['inputs']['filename_prefix'] for n in built.values())
        self.assertEqual(len(set(prefixes)), 4)
        self.assertEqual(workflow['322']['inputs']['filename_prefix'], '$PREFIX$')


class DropTests(unittest.TestCase):
    def test_dropped_branch_is_removed_and_dangling_links_refused(self):
        workflow = {'1': {'class_type': 'LoadImage', 'inputs': {'image': 'a.png'}},
                    '2': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0], 'filename_prefix': 'x'}},
                    '3': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0], 'filename_prefix': 'y'}}}
        factory = ComfyJobs()
        factory.workflow = lambda _: (workflow, 'hash')
        job = {'workflow': 'test', 'id': 'things/statue/mesh', 'family': 'things', 'asset': 'statue', 'drop': ['3']}
        built, _ = factory.build(job)
        self.assertEqual(sorted(built), ['1', '2'])
        self.assertIn('3', workflow)
        with self.assertRaisesRegex(ValueError, 'dropped node 1'):
            factory.build(dict(job, drop=['1']))
        with self.assertRaisesRegex(ValueError, 'no node 9'):
            factory.build(dict(job, drop=['9']))


if __name__ == '__main__':
    unittest.main()
