# Intelligent Editing Harness

The importable runtime lives in `harness/intelligent_editing/` because Python
package names cannot contain hyphens. From the repository root in PowerShell:

```powershell
python -m harness.intelligent_editing.runner validate --request .\mix-request.json
python -m harness.intelligent_editing.runner plan --request .\mix-request.json
python -m harness.intelligent_editing.runner confirm --request-id <request_id> --plan-hash <plan_hash>
python -m harness.intelligent_editing.runner render --request-id <request_id> --plan-hash <plan_hash>
python -m harness.intelligent_editing.runner resume --request-id <request_id>
```

Place `--api-base <loopback-url>` and `--state-root <directory>` before the
subcommand when overriding defaults. Production HTTP is restricted to loopback
addresses; tests inject a transport and do not use the network.

`plan` (alias `preview`) always uses `GET /api/assets`; it calls
`POST /api/mixes/plan` only when `codex_terra` is explicitly selected.
`local` and `auto` use deterministic local planning. Rendering uses
`POST /api/mixes` only after confirmation of the exact plan hash, and `resume`
(alias `status`) uses `GET /api/mixes/{mix_id}`. FFmpeg and source resolution remain owned by
`api_server.py`; the Harness never edits source assets or constructs commands.

Run state is atomic JSON under
`.agents/runtime/harness/intelligent-editing-runs/` by default. It contains
only validated managed IDs, normalized plan data, hashes, lifecycle state, and
sanitized evidence. It excludes local source paths, commands, credentials,
provider responses, and media bytes.

Batch workflows use the importable
`harness.intelligent_editing.shapewear_batch.ShapewearBatchHarness` adapter;
it delegates preview, confirmation, rendering, and status to the same
plan-hash-gated Harness for every variant.
