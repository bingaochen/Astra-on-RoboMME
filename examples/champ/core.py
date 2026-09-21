"""Causal input construction shared by the online runner and tests."""
import json,re,time,uuid
from pathlib import Path
from PIL import Image,ImageDraw

TASKS=['BinFill','StopCube','PickXtimes','SwingXtimes','ButtonUnmask','VideoUnmask','VideoUnmaskSwap','ButtonUnmaskSwap','PickHighlight','VideoRepick','VideoPlaceButton','VideoPlaceOrder','MoveCube','InsertPeg','PatternLock','RouteStick']
DENSE_MEMORY_TASKS={'ButtonUnmask','ButtonUnmaskSwap','PickHighlight','StopCube'}
VIDEO_TASKS={'VideoUnmask','VideoUnmaskSwap','VideoPlaceButton','VideoPlaceOrder','VideoRepick','MoveCube','InsertPeg','PatternLock','RouteStick'}
ROOT=Path(__file__).resolve().parent

def atomic_json(path,data):
 path=Path(path);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2));tmp.replace(path)

def window_ids(t):return [max(0,t-3*(7-i)) for i in range(8)]

def memory_indices(task, frame_count, cutoff=None):
 if task not in DENSE_MEMORY_TASKS:return []
 end=frame_count if task=='StopCube' or cutoff is None else min(frame_count,cutoff+1)
 return list(range(end))

def freeze_memory_at_press(task,subgoal,signal,t,cutoff):
 if cutoff is not None or not signal:return cutoff
 prefix={'ButtonUnmask':'press the button at ', 'PickHighlight':'press the button at ', 'ButtonUnmaskSwap':'press the second button at '}.get(task)
 return t if prefix and subgoal.lower().startswith(prefix) else cutoff

def validate_subgoal(task,text):
 text=text.strip()
 if '\n' in text or len(text)>350:raise ValueError('Planner must return one grounded subgoal')
 templates=json.loads((ROOT/'prompts/index.json').read_text())[task]['templates']
 for template in templates:
  pattern=re.escape(template)
  pattern=re.sub(r'\\\{[^}]+\\\}',r'[a-zA-Z0-9 -]+',pattern)
  pattern=pattern.replace(re.escape('<y, x>'),r'<\d{1,3},\s*\d{1,3}>')
  if re.fullmatch(pattern,text,re.I):
   if any(int(n)>255 for pair in re.findall(r'<(\d+),\s*(\d+)>',text) for n in pair):raise ValueError('Coordinate out of bounds')
   return text
 raise ValueError('Unsupported planner output: '+text)

def parse_planner_output(task,text):
 # A completed GPT response outside the action template keeps the last command.
 try:
  return validate_subgoal(task,text)
 except ValueError:
  return None

def sheets(frames,indices,out,prefix):
 paths=[]
 for page,offset in enumerate(range(0,len(indices),16)):
  subset=indices[offset:offset+16];im=Image.new('RGB',(1024,4*280),(245,245,245));draw=ImageDraw.Draw(im)
  for k,idx in enumerate(subset):
   x,y=k%4*256,k//4*280;im.paste(Image.fromarray(frames[idx]).convert('RGB'),(x,y));draw.text((x+4,y+258),f'frame {idx}',fill='black')
  path=out/f'{prefix}_{page:03d}.jpg';im.save(path,quality=92);paths.append(path.name)
 return paths

class Trigger:
 def __init__(self):self.last=-10000
 def update(self,signal,t,task):
  if task!='StopCube':return signal
  # Eligibility spans at most 17 steps. A 32-step cooldown suppresses
  # duplicate eligibility, but persistent preparation completion can recheck.
  fire=signal and t-self.last>=32
  if fire:self.last=t
  return fire

class Planner:
 def __init__(self,spool,timeout=300,responder=None):self.spool=Path(spool);self.spool.mkdir(parents=True,exist_ok=True);self.timeout=timeout;self.responder=responder
 def review_second_button(self,goal,frames,wrist,subgoal,command_start,completed,issued,episode,t):
  if not subgoal.lower().startswith('press the second button at '):raise ValueError('Review requires second-button command')
  if not 0<=command_start<=t==len(frames)-1:raise ValueError('Invalid review frame boundary')
  rid=uuid.uuid4().hex;out=self.spool/rid;out.mkdir()
  for name,frame in [('current.png',frames[t]),('execution_start.png',frames[0]),('command_start.png',frames[command_start]),('current_wrist.png',wrist)]:Image.fromarray(frame).save(out/name)
  indices=list(range(t+1))
  images=['current.png','execution_start.png','command_start.png','current_wrist.png']+sheets(frames,indices,out,'memory')
  prompt=(ROOT/'prompts/second_button_review.md').read_text()
  prompt+='\nTask instruction: '+goal+'\nCURRENT second-button command: '+subgoal
  prompt+=f'\nCURRENT command started at frame {command_start}; current frame is {t}.'
  prompt+='\nEarlier monitor-confirmed history (unverified claims): '+json.dumps(completed)
  prompt+='\nIssued commands (not proof of completion): '+json.dumps(issued)
  prompt+='\nAttachment 1: current front. Attachment 2: fixed execution-start front. Attachment 3: CURRENT command-start front. Attachment 4: current wrist. Remaining attachments: chronological front-view memory sheets, every execution frame from 0 through current, with frame labels. No future frames or simulator hidden state are supplied.\n'
  (out/'prompt.txt').write_text(prompt)
  atomic_json(out/'request.json',dict(id=rid,task='ButtonUnmaskSwap',episode=episode,t=t,kind='second_button_review',images=images,memory_frame_ids=indices,command_start=command_start,subgoal=subgoal,planner_revision='v6_second_button_gpt_review_v1',model='gpt-6-astra',effort='medium',created=time.time()))
  start=time.monotonic()
  if self.responder is not None:self.responder(out)
  while not (out/'response.json').exists():
   if time.monotonic()-start>self.timeout:raise TimeoutError('Button review bridge timeout: '+rid)
   time.sleep(1)
  response=json.loads((out/'response.json').read_text())
  if response.get('status')!='ok':raise RuntimeError('Button review bridge failed: '+json.dumps(response))
  text=response.get('text','').strip().lower()
  if text not in ('true','false'):
   atomic_json(out/'parse_result.json',dict(approved=None,error='invalid_review_output'))
   raise ValueError('Button review must return exactly true or false')
  approved=text=='true'
  atomic_json(out/'parse_result.json',dict(approved=approved))
  return approved,rid,time.monotonic()-start
 def predict(self,task,goal,frames,demo,memory,completed,issued,episode,t):
  rid=uuid.uuid4().hex;out=self.spool/rid;out.mkdir()
  Image.fromarray(frames[-1]).save(out/'current.png')
  Image.fromarray(frames[0]).save(out/'execution_start.png')
  images=['current.png','execution_start.png'];images+=sheets(demo,list(range(len(demo))),out,'demo')
  indices=sorted(memory)
  images+=sheets(frames,indices,out,'memory')
  prompt=(ROOT/'prompts'/f'{task}.md').read_text()
  prompt+='\nCURRENT REQUEST\nTask name: '+task+'\nTask instruction: '+goal
  prompt+='\nCompleted subgoals (monitor-confirmed, chronological): '+json.dumps(completed)
  prompt+='\nCurrent/previous issued subgoals (NOT evidence of completion): '+json.dumps(issued)
  prompt+='\nAttachment 1 is the CURRENT execution image. Attachment 2 is the FIXED EXECUTION START image (execution frame 0, before any robot action), NOT the first demonstration frame. Use it as historical scene evidence, not as the current state. Remaining attachments are labeled below.\n'
  for i,name in enumerate(images[2:],3):prompt+=f'Attachment {i}: '+('demonstration sheet' if name.startswith('demo') else 'execution-memory sheet')+'.\n'
  if not demo:prompt+='No demonstration images supplied.\n'
  if task in DENSE_MEMORY_TASKS and task!='StopCube':prompt+='Execution-memory sheets contain history from execution start through the monitor-confirmed button-press completion (second button for ButtonUnmaskSwap); before that boundary they contain history through the current frame. Once frozen, later execution frames are excluded. Attachment 1 remains the current observation.\n'
  if not indices:prompt+='No selected execution-memory sheets supplied; the fixed execution-start image is still supplied.\n'
  if task=='StopCube':
   prompt+='\nONLINE STOPCUBE OVERRIDE: A monitor trigger is a replanning opportunity, NOT confirmation that any subgoal finished or any target crossing happened. On the initial call first prepare above the button. On later calls inspect current robot readiness and the complete dense visual history. Count actual target passages in either direction; never infer passages from the number of calls or issued remain-static commands. If unprepared, continue preparation. If prepared, choose remain static for an earlier approaching visit or press for the requested approaching visit. The trigger aims for 16-32 steps of lead; do not wait for the requested passage to finish. Historical frames include every execution frame, without subsampling. Simulator pauses during reasoning, so current image remains current.\n'
  prompt+='\nClosed-input inference: use attached images and this prompt only. Do not invoke tools, browse, read files or delegate. Output only the single grounded subgoal.\n'
  (out/'prompt.txt').write_text(prompt)
  atomic_json(out/'request.json',dict(id=rid,task=task,episode=episode,t=t,images=images,memory_frame_ids=indices,planner_revision='v6_boolean_press_boundary_non_template_continue_last_v4',model='gpt-6-astra',effort='medium',created=time.time()))
  start=time.monotonic()
  if self.responder is not None:self.responder(out)
  while not (out/'response.json').exists():
   if time.monotonic()-start>self.timeout:raise TimeoutError('Planner bridge timeout: '+rid)
   time.sleep(1)
  response=json.loads((out/'response.json').read_text())
  if response.get('status')!='ok':raise RuntimeError('Planner bridge failed: '+json.dumps(response))
  parsed=parse_planner_output(task,response['text'])
  atomic_json(out/'parse_result.json',dict(subgoal=parsed,continue_last=parsed is None,reason='non_template_output' if parsed is None else 'valid_template'))
  return parsed,rid,time.monotonic()-start


def is_planner_failure(result):
 """A completed model response failed the action contract; keep its outcome."""
 message=result.get("error", "")
 return result.get("status")=="error" and (message.startswith("Unsupported planner output:") or message in ("Planner must return one grounded subgoal", "Coordinate out of bounds"))

def has_recorded_outcome(result):
 return result["status"]!="error" or is_planner_failure(result)
