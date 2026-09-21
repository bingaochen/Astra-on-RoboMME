import unittest,json,tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
from core import Planner,parse_planner_output,freeze_memory_at_press
from core import window_ids,memory_indices,validate_subgoal,Trigger,is_planner_failure,has_recorded_outcome
class Tests(unittest.TestCase):
 def test_fixed_start_and_current_are_distinct_and_demo_is_separate(self):
  with tempfile.TemporaryDirectory() as tmp:
   frames=[np.full((256,256,3),v,dtype=np.uint8) for v in [20,80,140]]
   def answer(_):
    out=next(Path(tmp).iterdir());(out/'response.json').write_text(json.dumps({'status':'ok','text':'press the button at <60, 120>'}))
   with patch('core.time.sleep',side_effect=answer):
    Planner(tmp).predict('ButtonUnmask','unmask the cube',frames,[frames[1]],{1},[],[],31,2)
   out=next(Path(tmp).iterdir());req=json.loads((out/'request.json').read_text());prompt=(out/'prompt.txt').read_text()
   self.assertEqual(req['images'],['current.png','execution_start.png','demo_000.jpg','memory_000.jpg'])
   self.assertEqual(Image.open(out/'current.png').getpixel((0,0)),(140,140,140))
   self.assertEqual(Image.open(out/'execution_start.png').getpixel((0,0)),(20,20,20))
   self.assertIn('Attachment 3: demonstration sheet',prompt)
   self.assertIn('Attachment 4: execution-memory sheet',prompt)
   self.assertIn('stationary entity',prompt)
 def test_first_frame_is_supplied_without_selected_memory(self):
  with tempfile.TemporaryDirectory() as tmp:
   def answer(_):
    out=next(Path(tmp).iterdir());(out/'response.json').write_text(json.dumps({'status':'ok','text':'press the button at <60, 120> to stop'}))
   with patch('core.time.sleep',side_effect=answer):
    Planner(tmp).predict('PickXtimes','pick twice',[np.zeros((256,256,3),dtype=np.uint8)],[],set(),[],[],27,0)
   out=next(Path(tmp).iterdir());req=json.loads((out/'request.json').read_text())
   self.assertEqual(req['images'],['current.png','execution_start.png'])
   self.assertIn('fixed execution-start image is still supplied',(out/'prompt.txt').read_text())
 def test_causal_padding(self):
  self.assertEqual(window_ids(0),[0]*8)
  self.assertEqual(window_ids(16),[0,0,1,4,7,10,13,16])
  self.assertEqual(window_ids(40),[19,22,25,28,31,34,37,40])
 def test_dense_memory_only_for_four_tasks(self):
  for task in ['ButtonUnmask','ButtonUnmaskSwap','PickHighlight','StopCube']:
   self.assertEqual(memory_indices(task,25),list(range(25)))
  for task in ['BinFill','PickXtimes','VideoUnmask']:
   self.assertEqual(memory_indices(task,25),[])
 def test_press_history_freezes_inclusively(self):
  for task in ['ButtonUnmask','PickHighlight']:
   self.assertIsNone(freeze_memory_at_press(task,'press the button at <60, 120>',False,16,None))
   cutoff=freeze_memory_at_press(task,'press the button at <60, 120>',True,32,None)
   self.assertEqual(cutoff,32)
   self.assertEqual(memory_indices(task,100,cutoff),list(range(33)))
   self.assertEqual(freeze_memory_at_press(task,'press the button at <60, 120>',True,64,cutoff),32)
  self.assertIsNone(freeze_memory_at_press('ButtonUnmaskSwap','press the first button at <60, 120>',True,32,None))
  cutoff=freeze_memory_at_press('ButtonUnmaskSwap','press the second button at <60, 120>',True,64,None)
  self.assertEqual(memory_indices('ButtonUnmaskSwap',100,cutoff),list(range(65)))
  self.assertEqual(memory_indices('StopCube',100,32),list(range(100)))
 def test_planner_receives_only_frozen_history_and_latest_image(self):
  with tempfile.TemporaryDirectory() as tmp:
   frames=[np.full((256,256,3),v,dtype=np.uint8) for v in range(40)]
   def answer(out):
    (out/'response.json').write_text(json.dumps({'status':'ok','text':'pick up the container at <60, 120> that hides the red cube'}))
   Planner(tmp,responder=answer).predict('ButtonUnmask','pick red',frames,[],set(range(17)),[],[],0,39)
   out=next(Path(tmp).iterdir());req=json.loads((out/'request.json').read_text())
   self.assertEqual(req['memory_frame_ids'],list(range(17)))
   self.assertEqual(len([n for n in req['images'] if n.startswith('memory')]),2)
   self.assertEqual(Image.open(out/'current.png').getpixel((0,0)),(39,39,39))
 def test_planner_contract(self):
  for task,text in [('StopCube','remain static'),('BinFill','pick up the first red cube at <100, 200>'),('InsertPeg','Pick up the peg by grasping the far end at <91, 82>'),('PatternLock','move forward-right')]:self.assertEqual(validate_subgoal(task,text),text)
  for text in ['DONE','remain static\nthen press','move to the top of the button at <300, 2> to prepare']:
   with self.assertRaises(ValueError):validate_subgoal('StopCube',text)
 def test_explicit_no_next_action(self):
  for text in ['No next action is justified: both button presses and the red-cube container pickup are already completed.', 'There are no further actions.', 'No next subgoal is justified: both buttons were pressed and the container hiding the red cube was already picked up.', '没有下一步']:
   self.assertIsNone(parse_planner_output('ButtonUnmaskSwap',text))
  self.assertEqual(parse_planner_output('StopCube','remain static'),'remain static')
  for text in ['target occluded', 'DONE', '', 'remain static\nthen press', 'press the button at <999, 999>', 'No further segment is shown in the demonstration; all four demonstrated segments are already completed.']:
   self.assertIsNone(parse_planner_output('ButtonUnmaskSwap',text))
 def test_stopcube_dedup_and_rearm(self):
  tr=Trigger();self.assertTrue(tr.update(True,16,'StopCube'));self.assertFalse(tr.update(True,32,'StopCube'));self.assertFalse(tr.update(False,48,'StopCube'));self.assertTrue(tr.update(True,64,'StopCube'))
 def test_persistent_preparation_does_not_latch_forever(self):
  tr=Trigger();self.assertTrue(tr.update(True,16,'StopCube'));self.assertFalse(tr.update(True,32,'StopCube'));self.assertTrue(tr.update(True,48,'StopCube'))
 def test_completion_repeated_actions(self):
  tr=Trigger();self.assertTrue(tr.update(True,16,'PickXtimes'));self.assertTrue(tr.update(True,32,'PickXtimes'))
 def test_resume_preserves_model_abstention(self):
  r={'status':'error','error':'Unsupported planner output: target occluded'}
  self.assertTrue(is_planner_failure(r));self.assertTrue(has_recorded_outcome(r))
  self.assertEqual(r['status'],'error')
 def test_infrastructure_error_still_retryable(self):
  r={'status':'error','error':'Planner bridge timeout: request'}
  self.assertFalse(is_planner_failure(r));self.assertFalse(has_recorded_outcome(r))
  for status in ['success','fail','timeout']:self.assertTrue(has_recorded_outcome({'status':status}))
if __name__=='__main__':unittest.main()
