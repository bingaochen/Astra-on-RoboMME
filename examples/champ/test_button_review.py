import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np
from core import Planner

FIRST='press the first button at <65, 102>'
SECOND='press the second button at <65, 147>'
PICK='pick up the container at <100, 170> that hides the blue cube'

class ReviewTests(unittest.TestCase):
 def call_review(self,text='true',status='ok'):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  frames=[np.full((256,256,3),i,dtype=np.uint8) for i in range(49)]
  def responder(out):
   (out/'response.json').write_text(json.dumps({'status':status,'text':text}))
  planner=Planner(tmp.name,responder=responder)
  result=planner.review_second_button('press both',frames,frames[-1],SECOND,16,[FIRST],[{'t':0,'subgoal':FIRST},{'t':16,'subgoal':SECOND}],0,48)
  out=next(Path(tmp.name).iterdir())
  return result,json.loads((out/'request.json').read_text()),(out/'prompt.txt').read_text()
 def test_review_causal_inputs_and_rejection(self):
  result,req,prompt=self.call_review('false')
  self.assertFalse(result[0]);self.assertEqual(req['memory_frame_ids'],list(range(49)))
  self.assertEqual(req['command_start'],16)
  self.assertEqual(req['images'][:4],['current.png','execution_start.png','command_start.png','current_wrist.png'])
  self.assertIn(SECOND,prompt);self.assertIn(FIRST,prompt);self.assertIn('unverified claims',prompt)
 def test_approval(self):self.assertTrue(self.call_review(' true\n')[0][0])
 def test_invalid_output_is_error_not_approval_or_completion(self):
  with self.assertRaises(ValueError):self.call_review('yes, pick up the container')
 def test_api_error_is_not_a_negative_review(self):
  with self.assertRaises(RuntimeError):self.call_review('true','error')

 def loop(self,reviews,preds,task='ButtonUnmaskSwap'):
  video=SimpleNamespace(get_writer=lambda *a,**kw:Mock())
  with patch.dict('sys.modules',{'imageio':SimpleNamespace(v2=video),'imageio.v2':video}):runner=importlib.import_module('runner')
  class Env:
   t=0
   def obs(self):
    im=np.full((256,256,3),self.t,dtype=np.uint8)
    return dict(front_rgb_list=[im],wrist_rgb_list=[im],joint_state_list=[np.zeros(7)],gripper_state_list=[np.zeros(1)])
   def reset(self):return self.obs(),{'task_goal':'press both buttons then pick blue'}
   def step(self,a):
    self.t+=1
    return self.obs(),0,False,False,{'status':'ongoing'}
   def close(self):pass
  env=Env();builder=SimpleNamespace(make_env_for_episode=lambda ep:env,resolve_episode=lambda ep:(1,'easy'))
  client=SimpleNamespace(reset=lambda:None,infer=Mock(return_value={'actions':np.zeros((16,8))}))
  monitor=SimpleNamespace(predict=Mock(side_effect=[(p,[],0.0) for p in preds]))
  planner=SimpleNamespace(predict=Mock(side_effect=[(p,'plan',0.0) for p in [FIRST,SECOND,PICK]]),review_second_button=Mock(side_effect=[r if isinstance(r,Exception) else (r,'review',0.0) for r in reviews]))
  with tempfile.TemporaryDirectory() as tmp:
   args=SimpleNamespace(output=tmp,dataset='val',vla_checkpoint='fixed',max_steps=80,max_planner_calls=24)
   with patch.object(runner.imageio,'get_writer',return_value=Mock()):result=runner.episode(args,task,0,builder,monitor,planner,client)
   events=[json.loads(l) for l in (Path(tmp)/task/'ep000/decisions.jsonl').read_text().splitlines()]
  return result,events,client,monitor,planner
 def test_reject_then_approve_preserves_command_and_defers_history_freeze(self):
  result,events,client,monitor,planner=self.loop([False,True],[True,True,True,False])
  self.assertEqual(result['review_calls'],2)
  self.assertEqual([c.args[0]['grounded_subgoal'] for c in client.infer.call_args_list],[FIRST,SECOND,SECOND,PICK,PICK])
  self.assertEqual([e['t'] for e in events if e['type']=='memory_frozen'],[48])
  self.assertEqual([e['t'] for e in events if e['type']=='planner'],[0,16,48])
  self.assertEqual([c.args[4] for c in monitor.predict.call_args_list],[0,16,16,48])
  calls=planner.review_second_button.call_args_list
  self.assertEqual([c.args[4] for c in calls],[16,16])
  self.assertEqual([e['completed'] for e in events if e['type']=='planner'],[[],[FIRST],[FIRST,SECOND]])
  self.assertEqual([e['replan'] for e in events if e['type']=='monitor'],[True,False,True,False])
 def test_repeated_rejections_do_not_freeze_or_mark_complete(self):
  result,events,client,monitor,planner=self.loop([False,False,False],[True,True,True,True])
  self.assertEqual(result['status'],'timeout');self.assertEqual(result['review_calls'],3)
  self.assertFalse(any(e['type']=='memory_frozen' for e in events))
  self.assertEqual(planner.predict.call_count,2)
  self.assertTrue(all(c.args[0]['grounded_subgoal']==SECOND for c in client.infer.call_args_list[1:]))
 def test_monitor_false_does_not_call_gpt_review(self):
  result,events,client,monitor,planner=self.loop([],[True,False,False,False])
  planner.review_second_button.assert_not_called();self.assertEqual(result['review_calls'],0)
 def test_other_task_does_not_call_review(self):
  result,events,client,monitor,planner=self.loop([],[True,True,False,False],task='ButtonUnmask')
  planner.review_second_button.assert_not_called()
 def test_review_error_stops_without_executing_container(self):
  result,events,client,monitor,planner=self.loop([RuntimeError('review unavailable')],[True,True])
  self.assertEqual(result['status'],'error');self.assertEqual(result['steps'],32)
  self.assertEqual(planner.predict.call_count,2)
  self.assertFalse(any(e['type']=='memory_frozen' for e in events))

if __name__=='__main__':unittest.main()
