# Recorded RoboMME test results

The completed evaluation contains all 800 official test identities: 16 tasks,
50 episodes each. The [per-task table](test-per-task.csv) records 633 success,
103 fail, 64 timeout, zero execution errors and zero pending cases. Overall
success is **633 / 800 = 79.125%** (79.13% rounded).

| Memory skill | Tasks | Success / episodes | Success rate |
|---|---|---:|---:|
| Counting | BinFill, StopCube, PickXtimes, SwingXtimes | 139 / 200 | 69.50% |
| Permanence | ButtonUnmask, VideoUnmask, VideoUnmaskSwap, ButtonUnmaskSwap | 188 / 200 | 94.00% |
| Reference | PickHighlight, VideoRepick, VideoPlaceButton, VideoPlaceOrder | 184 / 200 | 92.00% |
| Imitation | MoveCube, InsertPeg, PatternLock, RouteStick | 122 / 200 | 61.00% |

## Evaluation provenance

These counts are copied without modification from the completed
`Astra_RoboMME_test800` evaluation package's `official-per-task-summary.csv`.
The underlying campaign made 806 test attempts. Its first complete pass contained
629 success, 102 fail, 63 timeout and six bridge implementation errors. Only those
six errors were rerun after correcting the bridge's interpretation of recovered
connection events; the other 794 original outcomes were retained. Corrected
failures and timeouts were included. This is not best-of-multiple-attempt scoring.

The recorded run used **Codex CLI 0.153.4, GPT-6 Astra, medium reasoning**, with
independent ephemeral calls, a 20-second minimum call interval and a 300-second
interface timeout. VLA and monitor shared an L40S GPU with the simulator. The VLA
was `symbolic-grounded-subgoal/79999` with seed 42; the monitor was the fixed
Qwen3-VL-4B adapter. Task success comes from the simulator, not monitor approval.

The portable runner in this repository uses the **Responses API and two GPUs per
worker**. These results describe the recorded Codex campaign; they are not a
measurement of a fresh run of the API launcher. Transport, model-service input
handling and deployment differences must be considered when comparing runs.
The full trace/video package is not bundled in this code repository.
