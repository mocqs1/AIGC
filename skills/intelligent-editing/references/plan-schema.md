# Intelligent Editing Schemas

## Planning Request

```json
{
  "request_id": "edit-001",
  "idempotency_key": "edit-001-v1",
  "selected_assets": [
    {"asset_id": "images/a.png", "media_type": "image", "duration_ms": 3000},
    {"asset_id": "videos/b.mp4", "media_type": "video", "trim_bounds_ms": {"start": 0, "end": 5000}}
  ],
  "objective": "Tight product recap",
  "aspect_ratio": "portrait",
  "target_duration_ms": 8000,
  "transition_mode": "auto",
  "planner": "codex_terra",
  "image_duration_ms": 3000
}
```

`asset_id` is an AIGC managed identifier returned by `/api/assets`. Although
current IDs contain a managed-root prefix, they are not filesystem paths. URLs,
absolute paths, drive paths, traversal, and command syntax are invalid.

## Normalized Plan

```json
{
  "version": "aigc-mix-plan/v1",
  "objective": "Tight product recap",
  "target_duration_ms": 8000,
  "clips": [
    {"asset_id": "images/a.png", "start_ms": 0, "end_ms": null, "duration_ms": 3000, "transition": "hard_cut"},
    {"asset_id": "videos/b.mp4", "start_ms": 0, "end_ms": 5000, "duration_ms": 5000, "transition": "hard_cut"}
  ],
  "planner": "codex_terra",
  "transition_mode": "auto",
  "warnings": []
}
```

Only these fields are accepted. Every clip must reference a selected ID, use a
500-60,000 ms duration, and use `hard_cut`. Total duration is 1,000-300,000 ms
and must be within the deterministic target tolerance. The canonical plan hash
is SHA-256 over UTF-8 JSON with sorted keys and compact separators.

## Run State

```json
{
  "version": "aigc-intelligent-editing-run/v1",
  "request_id": "edit-001",
  "idempotency_key": "edit-001-v1",
  "input_fingerprint": "sha256",
  "status": "awaiting_confirmation",
  "stage": "previewed",
  "request": {},
  "plan": {},
  "plan_hash": "sha256",
  "accepted_plan_hash": null,
  "mix_id": null,
  "output": null,
  "error": null,
  "evidence": {},
  "created_at": "UTC ISO-8601",
  "updated_at": "UTC ISO-8601"
}
```

Valid lifecycle states are `awaiting_confirmation`, `submitted`, `running`,
`succeeded`, and `failed`. Evidence contains hashes, stable IDs, planner names,
safe warnings, and stage outcomes only. It excludes source paths, commands,
credentials, raw HTTP/provider bodies, and media bytes.

The machine-readable form is [plan-schema.json](plan-schema.json).

## CLI

- `validate --request <json> [--api-base <loopback-url>] [--state-root <dir>]`
- `plan --request <json> [...]` (`preview` is a compatibility alias)
- `confirm --request-id <id> --plan-hash <sha256> [...]`
- `render --request-id <id> --plan-hash <sha256> [...]`
- `resume --request-id <id> [...]` (`status` is a compatibility alias)

Exit code `0` means success. Validation, confirmation, state, and local API
errors return exit code `1` and a stable JSON error on stderr.
