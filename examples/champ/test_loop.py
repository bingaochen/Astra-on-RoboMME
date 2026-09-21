import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np


class LoopTests(unittest.TestCase):
    def run_loop(self, predictions, planner_outputs=None, terminal_step=48, expected_status="success", dataset="val"):
        video = SimpleNamespace(get_writer=lambda *a, **k: Mock())
        with patch.dict('sys.modules', {'imageio': SimpleNamespace(v2=video), 'imageio.v2': video}):
            runner = importlib.import_module('runner')
        class Env:
            t = 0
            def obs(self):
                image = np.full((256, 256, 3), self.t, dtype=np.uint8)
                return {'front_rgb_list': [image], 'wrist_rgb_list': [image],
                        'joint_state_list': [np.zeros(7)], 'gripper_state_list': [np.zeros(1)]}
            def reset(self):return self.obs(), {'task_goal': 'pick up a cube'}
            def step(self, action):
                self.t += 1
                return self.obs(), 0, self.t == terminal_step, False, {'status': 'success' if self.t == terminal_step else 'running'}
            def close(self):pass
        env = Env()
        builder = SimpleNamespace(make_env_for_episode=lambda ep: env, resolve_episode=lambda ep: (123, 'hard'))
        client = SimpleNamespace(reset=lambda: None, infer=Mock(return_value={'actions': np.zeros((16, 8))}))
        anchors = []
        predictions = iter(predictions)
        def predict(task, goal, subgoal, frames, anchor, wrist, out):
            anchors.append(anchor)
            return next(predictions), [], 0.0
        monitor = SimpleNamespace(predict=predict)
        # Reissued identical text must still begin a new command instance.
        planner = SimpleNamespace(predict=lambda *a: ('pick up the red cube at <100, 100>', 'fake', 0.0))
        if planner_outputs is not None:
            planner.predict = Mock(side_effect=[(text, 'fake', 0.0) for text in planner_outputs])
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(output=tmp,dataset=dataset, vla_checkpoint='fixed', max_steps=48, max_planner_calls=24)
            with patch.object(runner.imageio, 'get_writer', return_value=Mock()):
                result = runner.episode(args, 'BinFill', 5, builder, monitor, planner, client)
            self.assertEqual(result['dataset'], dataset)
            self.assertEqual(json.loads((Path(tmp)/'BinFill/ep005/identity.json').read_text())['dataset'], dataset)
            decisions = [json.loads(line) for line in (Path(tmp)/'BinFill/ep005/decisions.jsonl').read_text().splitlines()]
        self.assertEqual(result['status'], expected_status)
        if planner_outputs is not None and expected_status != 'error':
            self.assertEqual(result['steps'], 48)
            self.assertEqual(result['planner_calls'], 2)
            self.assertTrue(result['continued_last_subgoal'])
            self.assertEqual(client.infer.call_count, 3)
            self.assertEqual({call.args[0]['grounded_subgoal'] for call in client.infer.call_args_list}, {planner_outputs[0]})
        return anchors, decisions

    def test_reissued_identical_command_resets_reference(self):
        anchors, _ = self.run_loop([True, True])
        self.assertEqual(anchors, [0, 16])

    def test_false_keeps_command_reference_and_does_not_increment_completion(self):
        anchors, decisions = self.run_loop([False, True])
        self.assertEqual(anchors, [0, 0])
        calls = [d for d in decisions if d['type'] == 'planner']
        self.assertEqual([d['t'] for d in calls], [0, 32])
        self.assertEqual([len(d['completed']) for d in calls], [0, 1])

    def test_no_next_action_continues_last_until_environment_success(self):
        anchors, decisions = self.run_loop([True], ['pick up the red cube at <100, 100>', None])
        self.assertEqual(anchors, [0])
        event = next(d for d in decisions if d['type'] == 'continue_last_subgoal')
        self.assertEqual(event['command_start'], 0)
        self.assertEqual([d['t'] for d in decisions if d['type'] == 'vla'], [0, 16, 32])

    def test_no_next_action_does_not_imply_success_at_step_limit(self):
        self.run_loop([True], ['pick up the red cube at <100, 100>', None], terminal_step=100, expected_status='timeout')

    def test_unrecognized_completion_text_continues_last(self):
        from core import parse_planner_output
        output=parse_planner_output('PatternLock', 'No further segment is shown in the demonstration; all four demonstrated segments are already completed.')
        self.run_loop([True], ['pick up the red cube at <100, 100>', output])

    def test_initial_no_next_action_requires_previous_valid_command(self):
        _, decisions = self.run_loop([], [None], expected_status='error')
        self.assertIn('without a previous valid subgoal', decisions[-1]['error'])

    def test_test_split_is_recorded_in_episode_evidence(self):
        self.run_loop([False, True], dataset="test")
