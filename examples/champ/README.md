# Pipeline reference

Start with the [installation and evaluation guide](../../docs/evaluation.md) for cloning,
environments, all three checkpoints, credentials and evaluation commands.
This directory contains the Responses API evaluation pipeline.
See the [project overview](../../README.md) for the three-tiered method and recorded results.

## Frozen method

| Component | Setting |
|---|---|
| Benchmark | Official `test` (default) or explicit `val`, 16 tasks × 50 fixed episodes; `joint_angle` |
| VLA | `symbolic-grounded-subgoal/79999`, server seed 42 |
| Execution | 16 actions per chunk; stop checked every step; strict 1300-step cap |
| Monitor | Qwen3-VL-4B + final V6 LoRA checkpoint2246; bf16; FlashAttention 2 |
| Monitor input | 8 causal front frames at stride 3 + command-start front + current wrist |
| Monitor decoding | Temperature 0; 8-token cap; exactly `true` or `false` |
| Planner | GPT-6 Astra, medium, Responses API, high-detail images, max 2048 tokens, store=false |
| Grounding | `<y, x>` coordinates on 256×256 front images |
| Planner budget | 24 calls per episode; button reviews counted separately |
| ButtonUnmaskSwap | Right button first; GPT reviews each positive second-button monitor trigger |
| StopCube | 32-step trigger cooldown; planner counts passages from dense causal history |
| Success | Environment `info.status` only; monitor/review approval never counts as success |

Planner inputs always include the current and execution-start front images.
Demonstration tasks receive the completed demonstration from reset. ButtonUnmask,
ButtonUnmaskSwap and PickHighlight keep every execution frame until the relevant
press is confirmed, then freeze that memory; StopCube retains every frame through
the current step. Other tasks receive no extra execution-memory sheets. Sheets
hold 16 frames on a 1024×1120 JPEG canvas, quality 92. Second-button review also
gets the current wrist, command-start reference and every execution frame.

A completed planner output outside the subgoal templates continues the existing
valid command until environment termination or the step cap, disabling further
monitor/planner calls. Initial invalid output with no previous command is an
error. API errors/incomplete responses do not activate this fallback. The inherited
runner also records invalid boolean output and planner-budget exhaustion as errors;
the summarizer retains them in the denominator and marks the run incomplete.
Explicit rate limits have bounded retries (at most nine sends); quota failures and
uncertain request outcomes are not retried automatically.

`common.md` is retained as a source reference; runtime planner calls load the
task-specific `.md` directly. Do not prepend `common.md` a second time. `index.json`
defines accepted templates and matches the packaged prompt hashes.

## Files and verification

- `prompts/`: all 16 task prompts, accepted templates and second-button review.
- `input_contract.py`: exact monitor text and causal image ordering.
- `core.py`: planner, monitor and task-specific control logic.
- `runner.py`, `run.sh`: selected benchmark split and two-environment execution.
- `protocol.json`, `weights.json`: evaluation settings and pinned model artifacts.
- `prepare_cases.py`: explicit test/val identities and disjoint shards.
- `summarize.py`: full-denominator accounting across one or more shard directories.

Run the CPU checks without API calls:

```bash
"$SIM_PYTHON" -m unittest discover -s examples/champ -p 'test_*.py'
```

Tests mock policies, simulator and API calls. They cover causal inputs, button
reviews, fallback/termination, retries, prompt hashes, test/val selection,
800-case generation and duplicate/split checks during result aggregation.
They do not validate GPU installation or end-to-end inference.

`train_config.json` and `train_entry.py` retain monitor training settings.
Retraining needs the separately prepared V6 label/image dataset; this guide
reproduces inference using the hosted checkpoint.
