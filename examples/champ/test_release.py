import json
import tempfile
import unittest
import subprocess
import sys
from types import ModuleType
from pathlib import Path
from unittest.mock import patch, Mock

from api_client import ResponsesClient
from core import ROOT, TASKS
from release_utils import sha256, summarize, validate_cases, validate_checkpoints


class ReleaseTests(unittest.TestCase):
    def test_prompt_index_matches_all_sixteen_bodies(self):
        index = json.loads((ROOT/'prompts/index.json').read_text())
        self.assertEqual(set(index), set(TASKS))
        for task, entry in index.items():
            self.assertEqual(entry['file'], task+'.md')
            self.assertEqual(sha256(ROOT/'prompts'/entry['file']), entry['sha256'])

    def test_full_identity_set_for_each_split(self):
        cases = [{'task':t, 'episode':e} for t in TASKS for e in range(50)]
        for dataset in ('val', 'test'):
            self.assertEqual(len(validate_cases(dict(dataset=dataset, cases=cases))), 800)

    def test_duplicate_and_wrong_split_rejected(self):
        case = dict(task='BinFill', episode=0)
        for document in (dict(dataset='val', cases=[case, case]),
                         dict(dataset='train', cases=[case]),
                         dict(cases=[case]),
                         dict(dataset='val', cases=[dict(task='BinFill', episode=50)])):
            with self.assertRaises(ValueError):
                validate_cases(document)

    def test_errors_and_missing_results_remain_in_denominator(self):
        cases = [dict(task='BinFill', episode=i) for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            for case, status in zip(cases, ['success', 'error']):
                out = Path(tmp)/case['task']/f"ep{case['episode']:03d}"
                out.mkdir(parents=True)
                (out/'result.json').write_text(json.dumps({**case, 'dataset':'val', 'status':status}))
            report = summarize(dict(dataset='val', cases=cases), tmp)
        self.assertEqual(report['success_rate'], 1/3)
        self.assertFalse(report['complete'])
        self.assertEqual(report['counts']['pending'], 1)

    def test_wrong_result_identity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'BinFill/ep000'
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps(dict(task='StopCube', episode=0, status='success')))
            with self.assertRaises(ValueError):
                summarize(dict(dataset='val', cases=[dict(task='BinFill', episode=0)]), tmp)

    def test_supplied_key_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'credential'
            path.write_text('sk-test')
            client = ResponsesClient.from_key_file(path)
            self.assertTrue(path.exists())
            self.assertNotIn(client.key, client.scrub('credential: '+client.key))

    def test_missing_checkpoint_fails_before_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'Missing checkpoint'):
                validate_checkpoints(tmp, tmp)

    def test_corrupt_adapter_fails_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('79999/params', '79999/assets', 'adapter'):
                (root/name).mkdir(parents=True)
            (root/'history_config.txt').write_text('symbolic-grounded-subgoal.yaml')
            for name in ('adapter_config.json', 'adapter_model.safetensors', 'additional_config.json'):
                (root/'adapter'/name).write_text('corrupt')
            with self.assertRaisesRegex(ValueError, 'differs from'):
                validate_checkpoints(root/'79999', root/'adapter')

    def test_default_generator_produces_800_disjoint_test_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'cases'
            subprocess.run([sys.executable, str(ROOT/'prepare_cases.py'), '--output', str(out),
                            '--shards', '8'], check=True, capture_output=True)
            full = json.loads((out/'all.json').read_text())
            self.assertEqual(full['dataset'], 'test')
            selected = []
            for path in out.glob('shard_*.json'):
                shard = json.loads(path.read_text())
                self.assertEqual(shard['dataset'], 'test')
                selected.extend((c['task'], c['episode']) for c in shard['cases'])
            self.assertEqual(len(selected), 800)
            self.assertEqual(len(set(selected)), 800)
            self.assertEqual(set(selected), {(c['task'],c['episode']) for c in full['cases']})

    def test_summarize_disjoint_test_shards_and_reject_overlap(self):
        cases = [dict(task='BinFill', episode=i) for i in range(2)]
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp)/str(i) for i in range(2)]
            for root, case in zip(roots, cases):
                out = root/case['task']/f"ep{case['episode']:03d}"
                out.mkdir(parents=True)
                (out/'result.json').write_text(json.dumps({**case, 'dataset':'test','status':'success'}))
            document = dict(dataset='test', cases=cases)
            report = summarize(document, roots)
            self.assertEqual(report['dataset'], 'test')
            self.assertTrue(report['complete'])
            self.assertEqual(report['counts']['success'], 2)
            with self.assertRaisesRegex(ValueError, 'Duplicate result'):
                summarize(document, roots + roots)
            with self.assertRaisesRegex(ValueError, 'split'):
                summarize(dict(dataset='val', cases=cases), roots)

    def test_missing_split_never_silently_becomes_test(self):
        case = dict(task='BinFill', episode=0)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'BinFill/ep000'
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps({**case, 'status':'success'}))
            with self.assertRaisesRegex(ValueError, 'split'):
                summarize(dict(dataset='test',cases=[case]), tmp)

    def test_runner_passes_selected_split_to_environment(self):
        import runner
        modules = {name: ModuleType(name) for name in ['robomme', 'robomme.robomme_env',
                   'robomme.env_record_wrapper', 'openpi_client', 'openpi_client.websocket_client_policy']}
        builder = Mock(metadata_index={('BinFill', i):{} for i in range(50)})
        factory = Mock(return_value=builder)
        modules['robomme.env_record_wrapper'].BenchmarkEnvBuilder = factory
        modules['openpi_client.websocket_client_policy'].MMEVLAWebsocketClientPolicy = Mock()
        for dataset in ('test', 'val'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cases = root/'cases.json'
                cases.write_text(json.dumps(dict(dataset=dataset, cases=[dict(task='BinFill',episode=0)])))
                argv = ['runner.py','--cases',str(cases),'--output',str(root/'results'),
                        '--spool',str(root/'spool'),'--vla-checkpoint','vla','--monitor-adapter','monitor']
                with patch.dict(sys.modules, modules), patch.object(sys,'argv',argv), \
                     patch('release_utils.validate_checkpoints'), patch('api_client.ResponsesClient'), \
                     patch.object(runner,'Monitor'), patch.object(runner,'Planner'), \
                     patch.object(runner,'episode',return_value={'status':'success'}) as episode:
                    runner.main()
                self.assertEqual(factory.call_args.kwargs['dataset'], dataset)
                self.assertEqual(episode.call_args.args[0].dataset, dataset)
                self.assertEqual(json.loads((root/'results/PILOT_FINISHED.json').read_text())['dataset'], dataset)


if __name__ == '__main__':
    unittest.main()
