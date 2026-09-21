# Installation and evaluation

Reproduce the Astra planner, visual completion monitor and grounded VLA on RoboMME.
Return to the [project overview](../README.md) for the method and recorded results.

## 1. Machine and access

Use a Linux NVIDIA GPU machine with a working CUDA driver and Vulkan rendering
stack. One worker uses **two GPUs**, one for the JAX VLA and one for the
simulator/Qwen monitor. The source deployment used RTX PRO 6000 Blackwell 96 GB
GPUs; minimum VRAM on smaller cards has not been measured. Keep sufficient disk
space for model downloads, extracted weights, videos and per-call image logs.

Install Git, Git LFS, [uv](https://docs.astral.sh/uv/getting-started/installation/),
a C++ build toolchain, and a CUDA toolkit compatible with PyTorch 2.9.1 for
FlashAttention compilation. Verify `nvidia-smi` and `nvcc --version`. For headless
rendering, follow the pinned simulator's
[system setup notes](https://github.com/RoboMME/robomme_benchmark/blob/856bc3a189d4172f3f47dbee4424d585f8d78db3/doc/docker_installation.md).

Your GitHub account needs access if this repository is private. Your OpenAI API
project must have access to `gpt-6-astra` through the Responses API. A ChatGPT
subscription alone does not provide the API credential used by this runner.

## 2. Clone and install the two environments

Run these commands in Bash. Keep using the same shell so exported paths persist.

```bash
git clone --recurse-submodules https://github.com/bingaochen/Astra-on-RoboMME.git
cd Astra-on-RoboMME
export REPO="$PWD"

# VLA server: JAX and the repository's locked dependencies.
GIT_LFS_SKIP_SMUDGE=1 uv sync --frozen --no-dev --python 3.11
export VLA_PYTHON="$REPO/.venv/bin/python"

# Simulator and completion monitor: a separate Python environment.
(cd third_party/robomme_benchmark && uv sync --frozen --no-dev --python 3.11)
export SIM_PYTHON="$REPO/third_party/robomme_benchmark/.venv/bin/python"
uv pip install --python "$SIM_PYTHON" -e packages/openpi-client \
  -r examples/champ/requirements-monitor.txt
uv pip install --python "$SIM_PYTHON" packaging ninja
uv pip install --python "$SIM_PYTHON" flash-attn==2.8.3 --no-build-isolation
```

The environments intentionally use different PyTorch/Transformers versions. Do
not merge them. Run the monitor dependency installation **after** simulator sync;
resyncing that environment can undo the monitor overrides. These also pin SAPIEN
3.0.3 to match the source deployment rather than the upstream lock’s 3.0.2. FlashAttention must
support your GPU architecture. The historical Blackwell deployment used a custom
sm120 build; a generic wheel may be insufficient. Keep FlashAttention 2 and the
included patch-embedding fix when reproducing the method.

The historical [package inventory](champ/simulator-source-freeze.txt) is
available for diagnosis. A clean Linux GPU installation of this release has not
yet been validated end to end; perform the smoke run below before a full test run.

## 3. Download the three model components

The monitor requires **both** the Qwen base and the trained LoRA adapter. The VLA
is a separate pretrained policy. The following downloads are revision-pinned;
[weights.json](../examples/champ/weights.json) records their provenance and checksums.

```bash
uv venv --python 3.11 .venv-download
uv pip install --python .venv-download/bin/python huggingface-hub==0.36.2

# A. Trained completion-monitor adapter.
.venv-download/bin/hf download bingaochen/Astra-on-RoboMME-Monitor \
  --revision 18ac28316a8b22c758945546c95f3910fc5dd27b \
  --local-dir checkpoints/Astra-on-RoboMME-Monitor
(cd checkpoints/Astra-on-RoboMME-Monitor && sha256sum -c SHA256SUMS)
export MONITOR_ADAPTER="$REPO/checkpoints/Astra-on-RoboMME-Monitor"

# B. Monitor base model.
.venv-download/bin/hf download Qwen/Qwen3-VL-4B-Instruct \
  --revision ebb281ec70b05090aa6165b016eac8ec08e71b17 \
  --local-dir checkpoints/Qwen3-VL-4B-Instruct
export MONITOR_BASE="$REPO/checkpoints/Qwen3-VL-4B-Instruct"

# C. Grounded-subgoal VLA and its policy-selection file.
.venv-download/bin/hf download Yinpei/mme_vla_suite \
  symbolic-grounded-subgoal/79999.zip symbolic-grounded-subgoal/history_config.txt \
  --revision 5db4d53ddb98c7f80cab08792dd53d985d712ab1 \
  --local-dir checkpoints/mme_vla_suite
"$VLA_PYTHON" scripts/unzip_ckpt.py checkpoints/mme_vla_suite --processes 1
export VLA_CHECKPOINT="$REPO/checkpoints/mme_vla_suite/symbolic-grounded-subgoal/79999"

# Cache the VLA tokenizer while internet access is available.
export OPENPI_DATA_HOME="$HOME/.cache/openpi"
"$VLA_PYTHON" -c 'from openpi.models.tokenizer import PaligemmaTokenizer; PaligemmaTokenizer()'
```

Keep `history_config.txt` next to `79999/`, and retain `79999/params` and
`79999/assets`. The launcher validates this layout and all three monitor file
hashes. The hosted adapter contains the original trained tensors. Public base/VLA
revisions were pinned during packaging; their tensors have not been independently
compared byte for byte with the historical GCP copies.

Official episode metadata is included in the pinned benchmark submodule. This
inference workflow does not require downloading the raw training H5 dataset or
retraining the monitor.

## 4. Supply your GPT API key and check installation

The current protocol uses `gpt-6-astra`, medium reasoning, high-detail images and
a 2048 output-token limit at `https://api.openai.com/v1/responses`. Use your own
account's key while keeping those settings. There is no automatic model fallback.
The inference commands below make paid API requests.

```bash
read -rsp 'OpenAI API key: ' OPENAI_API_KEY
export OPENAI_API_KEY
printf '\n'

# Dependency/GPU checks (no GPT requests).
CUDA_VISIBLE_DEVICES=0 "$VLA_PYTHON" -c 'import jax; print(jax.devices())'
CUDA_VISIBLE_DEVICES=1 "$SIM_PYTHON" -c \
  'import torch, flash_attn; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'
"$SIM_PYTHON" -m unittest discover -s examples/champ -p 'test_*.py'
```

For an end-to-end setup check, run one **validation** episode first. Inspect
`runner.log` and `summary.json` for setup/API/model errors before proceeding. A
completed rollout can legitimately fail the task; success on this one episode is
not an installation requirement. It is kept separate from the test evaluation.

```bash
"$SIM_PYTHON" examples/champ/prepare_cases.py --dataset val \
  --tasks ButtonUnmaskSwap --episodes 0 --output runs/setup-cases
VLA_GPU=0 MONITOR_GPU=1 PORT=18762 \
  bash examples/champ/run.sh runs/setup-cases/all.json runs/setup-001
cat runs/setup-001/summary.json
```

## 5. Run the complete official test set

Generate the explicit list of all 800 test episodes, then start one worker:

```bash
"$SIM_PYTHON" examples/champ/prepare_cases.py --dataset test \
  --output runs/test-cases
VLA_GPU=0 MONITOR_GPU=1 PORT=18762 \
  bash examples/champ/run.sh runs/test-cases/all.json runs/test-001
```

The cases file determines the **actual simulator split**, and `test` is recorded
in each episode's identity and result. Task/episode IDs are 0–49 within each task.
Both case generation and the runtime reject unsupported identities and duplicates.
The launcher starts the VLA, waits for readiness, runs the simulator/monitor and
planner, writes the summary, and stops its own VLA process on exit.

Use a **new output directory for every attempt**. Existing runs are never
overwritten, and there is no automatic resume or episode rerun. If interrupted,
inspect the recorded results and create an explicit list of unfinished cases for
a new run. Do not choose successful attempts retrospectively or drop errors from
the accounting. Keep the prompt/checkpoint/protocol fixed during test evaluation.

### Optional: parallel workers

For example, four workers need eight GPUs. Each gets a disjoint shard, its own
GPU pair, port, output directory and VLA server. From the same configured shell:

```bash
"$SIM_PYTHON" examples/champ/prepare_cases.py --dataset test --shards 4 \
  --output runs/test-parallel-cases
mkdir -p runs/test-parallel
for i in 0 1 2 3; do
  shard=$(printf '%02d' "$i")
  VLA_GPU=$((2*i)) MONITOR_GPU=$((2*i+1)) PORT=$((18762+i)) \
    bash examples/champ/run.sh "runs/test-parallel-cases/shard_${shard}.json" \
    "runs/test-parallel/shard_${shard}" \
    > "runs/test-parallel/launcher_${shard}.log" 2>&1 &
done
wait
"$SIM_PYTHON" examples/champ/summarize.py \
  --cases runs/test-parallel-cases/all.json \
  --results runs/test-parallel/shard_*/results \
  --output runs/test-parallel/combined-summary.json
```

API request spacing is 20 seconds **per worker**, not an account-wide rate limit.
Choose concurrency for your GPU resources and API quota. Never share a VLA server
between workers: its policy and random state belong to one worker.

## 6. Inspect and share the evaluation

For a single run, `runs/test-001/summary.json` is generated automatically. You can
also summarize a stopped run without loading models or calling GPT:

```bash
"$SIM_PYTHON" examples/champ/summarize.py --cases runs/test-cases/all.json \
  --results runs/test-001/results --output runs/test-001/checked-summary.json
```

The report includes `dataset`, `expected`, status counts, `complete`,
`success_rate` and per-episode records. The full test denominator is **800**;
missing episodes remain `pending`, and errors remain in the denominator.
`complete` is false if any requested episode is missing or errored. The summarizer
rejects overlapping attempts and mismatched splits. `PILOT_FINISHED.json` is a
legacy filename meaning the loop ended, not that all episodes succeeded.

Retain the entire run directory for reproducibility:

- `cases.json`, `summary.json`: requested identities and accounting.
- `repository-commit.txt`, `local-changes.patch`, `submodules.txt`, package lists:
  exact code and installed dependency provenance.
- `vla.log`, `runner.log`: startup and runtime diagnostics.
- `results/<task>/epNNN/`: identity, actions, videos, monitor inputs and decisions.
- `planner_calls/`: prompts, image attachments, API responses and recorded usage.

