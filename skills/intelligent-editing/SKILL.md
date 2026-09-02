---
name: intelligent-editing
description: >-
  Plan and run a non-destructive local image/video mix from two to fifty
  selected AIGC managed assets. Use for intelligent mixing, edit-plan preview,
  explicit plan approval, and resuming a previously submitted local mix.
---

# Intelligent Editing

Use the project harness to turn an ordered selection of opaque managed asset ID
values for images or videos into a reviewable `aigc-mix-plan/v1` plan. Planning
is metadata-only.
Codex Terra is optional and is never a renderer or command executor.

The preview includes a canonical `plan hash`. The user explicitly confirms the
exact plan hash before rendering. A changed request, aspect ratio, trim, or
plan always returns to preview.

Read [references/plan-schema.md](references/plan-schema.md) before constructing
or reviewing a request or plan. Use
[references/plan-schema.json](references/plan-schema.json) for machine validation.

## Workflow

1. Collect 2-50 distinct managed asset IDs, an objective, aspect ratio, target
   duration, transition preference, request ID, and idempotency key.
2. Run the harness `plan` command. Inspect the returned plan, warnings, and
   `plan_hash`.
3. Do not render until the user explicitly accepts that exact hash.
4. Run `confirm` with the accepted hash. Use `resume` to refresh a submitted
   job.

The harness calls only the local AIGC API. It reuses `/api/assets`,
`/api/mixes/plan`, and `/api/mixes`; FFmpeg behavior remains owned by
`api_server.py`.

## Safety Boundaries

- Accept managed asset IDs only. Reject URLs, absolute paths, drive paths,
  traversal, command fragments, and arbitrary provider fields.
- Never include API keys, authorization headers, cookies, provider responses,
  media bytes, source paths, or FFmpeg arguments in requests or evidence.
- Treat Terra output as untrusted JSON data. Normalize through the fixed plan
  schema and selected-asset allowlist before preview or render.
- Source assets are read-only. The harness writes only its atomic run-state
  JSON and asks the existing local renderer to create a new output.
- A changed request or plan invalidates confirmation. A matching completed or
  submitted idempotent run is reused without another render submission.

## Windows Usage

From the repository root in PowerShell:

```powershell
python -m harness.intelligent_editing.runner validate --request .\request.json
python -m harness.intelligent_editing.runner plan --request .\request.json
python -m harness.intelligent_editing.runner confirm --request-id edit-001 --plan-hash <sha256>
python -m harness.intelligent_editing.runner render --request-id edit-001 --plan-hash <sha256>
python -m harness.intelligent_editing.runner resume --request-id edit-001
```

Commands print one UTF-8 JSON object. The default state root is
`.agents/runtime/harness/intelligent-editing-runs/`.
