"""GPT-6 + V6 boolean monitor + grounded VLA, official RoboMME evaluation."""
import argparse,json,os,time,traceback
from pathlib import Path
import numpy as np
from PIL import Image
import imageio.v2 as imageio
from core import VIDEO_TASKS,Planner,Trigger,atomic_json,memory_indices,freeze_memory_at_press,is_planner_failure,has_recorded_outcome

BASE='Qwen/Qwen3-VL-4B-Instruct'
ADAPTER='checkpoint-2246'
class Monitor:
 def __init__(self,base=BASE,adapter=ADAPTER):
  import torch
  from train_entry import patch_embed_forward,Qwen3VLVisionPatchEmbed
  Qwen3VLVisionPatchEmbed.forward=patch_embed_forward
  from swift.llm import PtEngine,RequestConfig
  self.engine=PtEngine(base,adapters=[adapter],torch_dtype=torch.bfloat16,model_type='qwen3_vl',attn_impl='flash_attention_2',device_map={'':0},max_batch_size=1)
  self.config=RequestConfig(max_tokens=8,temperature=0)
 def predict(self,task,goal,subgoal,frames,command_start,wrist,out):
  from swift.llm import InferRequest
  from input_contract import from_observations,parse_answer
  sample,ids=from_observations(task,goal,subgoal,frames,command_start,wrist)
  images=[]
  for i,frame in enumerate(sample['images']):
   path=out/f'{i}.png';Image.fromarray(frame).save(path);images.append(str(path))
  sample['images']=images
  atomic_json(out/'input.json',sample)
  started=time.monotonic()
  response=self.engine.infer([InferRequest(**sample)],request_config=self.config)[0].choices[0].message.content
  atomic_json(out/'response.json',{'text':response})
  return parse_answer(response),ids,time.monotonic()-started

def pack_state(obs):return np.concatenate([obs['joint_state_list'][-1],obs['gripper_state_list'][-1][:1]]).astype(np.float32)
def front(obs):
 im=np.asarray(obs['front_rgb_list'][-1],dtype=np.uint8)
 if im.shape!=(256,256,3):raise ValueError('Unexpected image geometry '+str(im.shape))
 return im.copy()

def episode(args,task,ep,builder,monitor,planner,client):
 out=Path(args.output)/task/f'ep{ep:03d}';out.mkdir(parents=True,exist_ok=True)
 temp=out/'monitor_inputs';temp.mkdir(exist_ok=True)
 started=time.time();env=None;writer=None;events=(out/'decisions.jsonl').open('w');actions=[];t=0;monitor_calls=0;review_calls=0
 def log(**obj):events.write(json.dumps(obj)+'\n');events.flush()
 try:
  env=builder.make_env_for_episode(ep)
  obs,info=env.reset()
  goal=info['task_goal'];goal=goal[0] if isinstance(goal,list) else goal
  frames=[front(obs)];demo=[np.asarray(x,dtype=np.uint8).copy() for x in obs['front_rgb_list'][:-1]] if task in VIDEO_TASKS else []
  client.reset();memory=set(memory_indices(task,1));memory_cutoff=None;completed=[];issued=[];trigger=Trigger();subgoal=None;command_start=0;continue_last=False;planner_calls=0
  writer=imageio.get_writer(str(out/'rollout.mp4'),fps=30)
  # Raw fixed-height front/wrist video; text decisions live in timestamped JSONL.
  def video(obs):writer.append_data(np.concatenate([front(obs),np.asarray(obs['wrist_rgb_list'][-1],dtype=np.uint8)],axis=1))
  video(obs)
  atomic_json(out/'identity.json',dict(task=task,episode=ep,dataset=args.dataset,seed_and_difficulty=builder.resolve_episode(ep),goal=goal,monitor=getattr(args,'monitor_adapter',ADAPTER),vla=args.vla_checkpoint,model='gpt-6-astra',reasoning='medium',chunk=16,history_stride=3,stopcube_debounce_steps=32,simulation_paused_during_inference=True,monitor_contract='v6_bool_notime',memory_policy='dense_to_press_completion_for_three_stopcube_to_current',max_planner_calls=args.max_planner_calls,no_next_action_policy='all_non_template_completed_outputs_continue_last_until_environment_end'))
  outcome='timeout'
  atomic_json(out/'review_protocol.json',dict(revision='v6_second_button_gpt_review_v1',enabled=task=='ButtonUnmaskSwap',review_model='gpt-6-astra',reasoning='medium',on_false='continue_identical_command_and_reference',on_error='episode_error',review_calls_counted_separately=True,max_steps=args.max_steps))
  while t<args.max_steps:
   signal=False
   if subgoal is not None and not continue_last:
    call_out=temp/f't{t:04d}';call_out.mkdir(exist_ok=False)
    pred,ids,seconds=monitor.predict(task,goal,subgoal,frames,command_start,np.asarray(obs['wrist_rgb_list'][-1],dtype=np.uint8),call_out)
    monitor_calls+=1
    signal=trigger.update(pred,t,task)
    if signal and task=='ButtonUnmaskSwap' and subgoal.lower().startswith('press the second button at '):
     review_calls+=1
     approved,rid,review_seconds=planner.review_second_button(goal,frames,np.asarray(obs['wrist_rgb_list'][-1],dtype=np.uint8),subgoal,command_start,completed,issued,ep,t)
     log(type='second_button_review',t=t,subgoal=subgoal,command_start=command_start,approved=approved,request_id=rid,seconds=review_seconds)
     signal=approved
    cutoff=freeze_memory_at_press(task,subgoal,signal,t,memory_cutoff)
    if cutoff is not None and memory_cutoff is None:
     log(type='memory_frozen',t=t,subgoal=subgoal,cutoff=cutoff,reason='monitor_confirmed_press_completion')
    memory_cutoff=cutoff
    memory=set(memory_indices(task,len(frames),memory_cutoff))
    log(type='monitor',t=t,subgoal=subgoal,frame_ids=ids,prediction=pred,command_start=command_start,replan=signal,seconds=seconds,memory_ids=sorted(memory),memory_cutoff=memory_cutoff)
   if subgoal is None or signal:
    if planner_calls>=args.max_planner_calls:raise RuntimeError('Pilot planner-call limit reached')
    if subgoal is not None and task!='StopCube':completed.append(subgoal)
    next_subgoal,rid,seconds=planner.predict(task,goal,frames,demo,memory,completed,issued,ep,t)
    planner_calls+=1
    if next_subgoal is None:
     if subgoal is None:raise ValueError('Planner returned no next action without a previous valid subgoal')
     continue_last=True
     log(type='continue_last_subgoal',t=t,subgoal=subgoal,command_start=command_start,request_id=rid,seconds=seconds,reason='planner_non_template_output')
    else:
     subgoal=next_subgoal;command_start=t
     issued.append({'t':t,'subgoal':subgoal})
    log(type='planner',t=t,subgoal=next_subgoal,request_id=rid,seconds=seconds,completed=list(completed))
   element={'observation/image':front(obs),'observation/wrist_image':np.asarray(obs['wrist_rgb_list'][-1],dtype=np.uint8),'observation/state':pack_state(obs),'prompt':goal,'grounded_subgoal':subgoal,'simple_subgoal':subgoal}
   start=time.monotonic();chunk=np.asarray(client.infer(element)['actions'])
   if chunk.ndim!=2 or chunk.shape[1]!=8 or len(chunk)<16 or not np.isfinite(chunk).all():raise ValueError('Invalid VLA action chunk')
   log(type='vla',t=t,seconds=time.monotonic()-start)
   stopped=False
   for action in chunk[:min(16,args.max_steps-t)]:
    obs,_,terminated,truncated,info=env.step(action);t+=1;actions.append(action.copy())
    if info.get('status')=='error':raise RuntimeError(info.get('error_message','Simulator error'))
    frames.append(front(obs));video(obs)
    if terminated or truncated:
     status=info.get('status','unknown');outcome=status if status in ('success','fail','timeout') else 'error'
     stopped=True;break
   if stopped:break
  result=dict(task=task,episode=ep,status=outcome,steps=t,seconds=time.time()-started,planner_calls=planner_calls,monitor_calls=monitor_calls,continued_last_subgoal=continue_last)
 except Exception as e:
  log(type='exception',t=t,error=str(e),traceback=traceback.format_exc());result=dict(task=task,episode=ep,status='error',steps=t,seconds=time.time()-started,error=str(e));traceback.print_exc()
 finally:
  if writer is not None:writer.close()
  if env is not None:env.close()
  events.close()
  np.save(out/'actions.npy',np.asarray(actions))
 result['dataset']=args.dataset
 result['review_calls']=review_calls
 atomic_json(out/'result.json',result);print('EPISODE_RESULT',json.dumps(result),flush=True)
 return result

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('--cases',required=True)
 parser.add_argument('--max-planner-calls',type=int,default=24)
 parser.add_argument('--key-file',help='Optional credential file; otherwise OPENAI_API_KEY (file is not deleted)')
 parser.add_argument('--monitor-base',default=BASE)
 parser.add_argument('--monitor-adapter',required=True)
 parser.add_argument('--output',required=True)
 parser.add_argument('--spool',required=True)
 parser.add_argument('--port',type=int,default=18762)
 parser.add_argument('--max-steps',type=int,default=1300)
 parser.add_argument('--vla-checkpoint',required=True)
 args=parser.parse_args()
 from release_utils import validate_cases,validate_checkpoints
 document=json.loads(Path(args.cases).read_text())
 cases=validate_cases(document)
 args.dataset=document['dataset']
 validate_checkpoints(args.vla_checkpoint,args.monitor_adapter)
 from api_client import ResponsesClient
 responder=ResponsesClient.from_key_file(args.key_file) if args.key_file else ResponsesClient(os.environ.get('OPENAI_API_KEY',''))
 for directory in (args.output,args.spool):
  if Path(directory).exists():raise ValueError('Use new output and spool directories; existing evidence is preserved')
 Path(args.output).mkdir(parents=True,exist_ok=False)
 import robomme.robomme_env
 from robomme.env_record_wrapper import BenchmarkEnvBuilder
 from openpi_client.websocket_client_policy import MMEVLAWebsocketClientPolicy
 client=MMEVLAWebsocketClientPolicy('127.0.0.1',args.port);monitor=Monitor(args.monitor_base,args.monitor_adapter)
 planner=Planner(args.spool,responder=responder)
 tasks=list(dict.fromkeys(c['task'] for c in cases))
 errors=0
 for task in tasks:
  builder=BenchmarkEnvBuilder(task,dataset=args.dataset,action_space='joint_angle',gui_render=False,max_steps=args.max_steps)
  ids=sorted(ep for name,ep in builder.metadata_index if name==task)
  assert len(ids)==50,(task,len(ids))
  requested={c['episode'] for c in cases if c['task']==task};assert requested.issubset(ids),(task,requested)
  ids=[ep for ep in ids if ep in requested]
  for ep in ids:
   if (Path(args.output).parent.parent/'STOP.json').exists():raise RuntimeError('Host requested stop before next episode')
   rp=Path(args.output)/task/f'ep{ep:03d}'/'result.json'
   if rp.exists():continue
   if rp.parent.exists():raise RuntimeError('Incomplete prior episode; use a new output directory to preserve its evidence')
   r=episode(args,task,ep,builder,monitor,planner,client)
   errors=errors+1 if r['status']=='error' and not is_planner_failure(r) else 0
   if r['status']=='error' and r.get('error','').startswith(('Planner API','Pilot planner-call','Planner bridge failed')):raise RuntimeError('Stopping pilot after planner service or budget error')
   if errors>=3:raise RuntimeError('Three consecutive infrastructure/protocol errors; stopping shard')
 atomic_json(Path(args.output)/'PILOT_FINISHED.json',{'dataset':args.dataset,'tasks':tasks,'finished_at':time.time()})
if __name__=='__main__':main()
