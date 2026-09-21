#!/usr/bin/env bash
# One VLA GPU + one simulator/monitor GPU. Each invocation gets fresh output.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo 'Usage: bash examples/champ/run.sh CASES.json NEW_RUN_DIRECTORY' >&2
  exit 2
fi
: "${VLA_PYTHON:?Set VLA_PYTHON to the VLA environment Python}"
: "${SIM_PYTHON:?Set SIM_PYTHON to the simulator/monitor environment Python}"
: "${VLA_CHECKPOINT:?Set VLA_CHECKPOINT to symbolic-grounded-subgoal/79999}"
: "${MONITOR_BASE:?Set MONITOR_BASE to the downloaded Qwen3-VL-4B-Instruct directory}"
: "${MONITOR_ADAPTER:?Set MONITOR_ADAPTER to the released checkpoint-2246 directory}"
: "${OPENAI_API_KEY:?Supply your own API credential through OPENAI_API_KEY}"
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
CASES=$("$SIM_PYTHON" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$1")
RUN=$("$SIM_PYTHON" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$2")
VLA_GPU=${VLA_GPU:-0}
MONITOR_GPU=${MONITOR_GPU:-1}
PORT=${PORT:-18762}
[[ "$VLA_GPU" != "$MONITOR_GPU" ]] || { echo 'Use distinct GPUs for VLA and monitor' >&2; exit 2; }
[[ ! -e "$RUN" ]] || { echo 'Use a new run directory to preserve existing evidence' >&2; exit 2; }
export PYTHONPATH="$REPO/examples/champ:$REPO/src:$REPO/packages/openpi-client/src:$REPO/third_party/robomme_benchmark/src"
export OPENPI_DATA_HOME=${OPENPI_DATA_HOME:-"$HOME/.cache/openpi"}
export HF_HOME=${HF_HOME:-"$HOME/.cache/huggingface"}
export XLA_PYTHON_CLIENT_PREALLOCATE=false
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false USE_HF=1 IMAGE_MAX_TOKEN_NUM=128
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
"$SIM_PYTHON" - "$CASES" "$VLA_CHECKPOINT" "$MONITOR_ADAPTER" "$PORT" <<'PY'
import json, socket, sys
from pathlib import Path
from release_utils import validate_cases, validate_checkpoints
validate_cases(json.loads(Path(sys.argv[1]).read_text()))
validate_checkpoints(sys.argv[2], sys.argv[3])
with socket.socket() as sock:
    sock.bind(('0.0.0.0', int(sys.argv[4])))
PY
mkdir -p -- "$(dirname -- "$RUN")"
mkdir -- "$RUN"
cp -- "$CASES" "$RUN/cases.json"
cd "$REPO"
for component in vla simulator; do
  if [[ "$component" == vla ]]; then interpreter="$VLA_PYTHON"; else interpreter="$SIM_PYTHON"; fi
  "$interpreter" - > "$RUN/$component-packages.txt" <<'PYENV'
from importlib.metadata import distributions
print('\n'.join(sorted(f"{d.metadata['Name']}=={d.version}" for d in distributions())))
PYENV
done
git rev-parse HEAD > "$RUN/repository-commit.txt"
git diff HEAD > "$RUN/local-changes.patch"
git submodule status > "$RUN/submodules.txt"
# The VLA process does not need the planner API credential.
env -u OPENAI_API_KEY CUDA_VISIBLE_DEVICES="$VLA_GPU" "$VLA_PYTHON" scripts/serve_policy.py \
  --port="$PORT" --seed=42 policy:checkpoint --policy.config=mme_vla_suite \
  --policy.dir="$VLA_CHECKPOINT" > "$RUN/vla.log" 2>&1 &
vla_pid=$!
trap 'kill "$vla_pid" 2>/dev/null || true' EXIT
"$SIM_PYTHON" - "$vla_pid" "$PORT" <<'PY'
import os,socket,sys,time
for _ in range(180):
    os.kill(int(sys.argv[1]),0)
    try:
        with socket.create_connection(('127.0.0.1',int(sys.argv[2])),timeout=1):break
    except OSError:time.sleep(5)
else:raise TimeoutError('VLA startup exceeded 15 minutes')
PY
CUDA_VISIBLE_DEVICES="$MONITOR_GPU" "$SIM_PYTHON" -u examples/champ/runner.py \
  --cases "$RUN/cases.json" --output "$RUN/results" --spool "$RUN/planner_calls" \
  --port "$PORT" --vla-checkpoint "$VLA_CHECKPOINT" \
  --monitor-base "$MONITOR_BASE" --monitor-adapter "$MONITOR_ADAPTER" \
  > "$RUN/runner.log" 2>&1
"$SIM_PYTHON" examples/champ/summarize.py --cases "$RUN/cases.json" \
  --results "$RUN/results" --output "$RUN/summary.json"
