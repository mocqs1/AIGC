# Intelligent Editing Harness

This Harness turns a selected managed-asset request into a durable, resumable
workflow:

```powershell
python -m harness.intelligent_editing.runner validate --request .\mix-request.json
python -m harness.intelligent_editing.runner plan --request .\mix-request.json
python -m harness.intelligent_editing.runner confirm --request-id <request_id> --plan-hash <plan_hash>
python -m harness.intelligent_editing.runner render --request-id <request_id> --plan-hash <plan_hash>
python -m harness.intelligent_editing.runner resume --request-id <request_id>
```

The default API is `http://127.0.0.1:8001`. Use `--api-base` and
`--state-root` before the subcommand to override them. The API base must use a
loopback address. `plan` (alias `preview`) calls `GET /api/assets` and calls
`POST /api/mixes/plan` only when `codex_terra` is explicitly selected;
`local` and `auto` stay deterministic and local. Render calls `POST /api/mixes`
only after the exact preview hash has been confirmed. `resume` (alias `status`) polls
`GET /api/mixes/{mix_id}`.

The Harness persists atomic JSON state under
`.agents/runtime/harness/intelligent-editing-runs/`. State contains only
validated IDs, plan data, hashes, statuses, and safe evidence. It never stores
API keys, provider responses, local paths, FFmpeg commands, or media bytes.

## Shapewear batches

Use `ShapewearBatchHarness` for a validated batch from
`skills.shapewear_video_generator.batch.plan_batch`:

```python
from harness.intelligent_editing.shapewear_batch import ShapewearBatchHarness

batch_harness = ShapewearBatchHarness(editing_harness)
preview = batch_harness.preview(batch)
hashes = {item["variant_id"]: item["plan_hash"] for item in preview["variants"]}
batch_harness.confirm(batch, hashes)
batch_harness.render(batch, hashes)
batch_harness.status(batch)
```

The adapter validates the complete batch before every operation and delegates
each variant to `IntelligentEditingHarness`. Every variant therefore retains
the existing preview, exact plan-hash confirmation, idempotent render, and
safe-status rules. It accepts only opaque managed IDs and never receives
source paths, renderer commands, or provider credentials.
