---
name: aigc-production-orchestrator
description: >-
  Route AIGC image, video, product-asset, and local-mix requests through the
  project's registered skills and provider entrypoints. Use this skill whenever
  a request combines a brief with generation, references, batches, editing,
  prompt writing, asset continuity, quality checks, or resumable production,
  even when the user does not name a skill. It enforces stage gates, evidence-
  based manifests, safe asset roles, explicit confirmation for rendering, and
  bounded retries without replacing provider implementations.
---

# AIGC Production Orchestrator

## Mission

Turn an AIGC request into the smallest executable production path in this
repository. Route to an existing registered skill and its Python entrypoint;
do not reimplement provider calls, FFmpeg, or domain-specific workflows here.

Quality means four things: the requested subject stays identifiable, every
production step has an observable result, outputs are resumable and traceable,
and uncertainty is marked for review instead of presented as success.

## Route First

Classify the request before acting:

- `image_generation`: new image or image edit. Use the registered image skill
  and `main.generate_image` or its declared entrypoint.
- `video_generation`: text-to-video or image-to-video. Use the registered video
  skill and `main.generate_video` or its declared entrypoint.
- `product_fidelity`: clothing, shapewear, outfit replacement, or other
  reference-led product work. Lock the supplied reference as the source of
  truth and route to the matching registered product skill.
- `intelligent_editing`: ordered local assets, timeline, trim, transitions, or
  FFmpeg mix. Use `intelligent-editing`; planning must precede rendering.
- `prompt_only`: the user explicitly asks for a prompt, diagnosis, translation,
  or repair and does not ask for execution. Return a copy-ready prompt without
  calling a provider.
- `batch_or_resume`: multiple variants or continuation of existing work. Read
  the persisted project/run state first and resume the first incomplete item.

If several routes match, choose the narrowest domain skill first, then use this
skill for orchestration. Keep the project's registered entrypoint authoritative.

## Intake And Defaults

Collect only inputs that change execution: objective, asset type, selected
references, output format/aspect ratio, duration, target audience or channel,
provider if explicitly chosen, request ID, and idempotency key. Do not block on
optional details. State conservative defaults in the manifest.

For reference-led work, assign each input one role such as `product_master`,
`identity`, `pose`, `composition`, `style`, `background`, `start_frame`, or
`end_frame`. Do not let a generic prompt override a locked product or identity
reference. Use one source of truth for each semantic dimension.

## Stage Gates

Use this order for work that changes files or spends provider credits:

1. `diagnose`: identify route, missing required inputs, constraints, and risks.
2. `plan`: build the provider request or local edit plan; expose warnings and
   the plan hash when the registered skill supports one.
3. `confirm`: require explicit user approval for a render or local mix when the
   plan is reviewable and hashable.
4. `execute`: call only the declared project entrypoint and record its result.
5. `quality_check`: run deterministic checks, then mark semantic checks as
   `manual_review` when they cannot be proven from available media.
6. `deliver`: return the actual output paths and a compact manifest.

Prompt-only requests may stop after `diagnose` and prompt construction. Never
claim a file, URL, provider result, or semantic quality check that was not
actually produced or observed.

## Prompt And Production Rules

Build prompts from observable controls: subject/state -> action -> setting ->
camera/composition -> lighting/material -> sound/dialogue -> duration and
format -> exclusions. Prefer one readable action per shot and a causal action
chain with a visible starting state, progression, and result.

Preserve exact user-supplied dialogue and visible text. Do not invent claims,
measurements, brand facts, hidden product details, or additional packaging copy.
For video, keep a coherent camera path unless a cut is intentional; state the
reason for attention handoffs between subjects.

## Resumability And Idempotency

Persist progress in the owning harness or project state, not in conversation
memory. A completed or submitted idempotent request is reused. A changed input,
reference role, aspect ratio, duration, or plan hash invalidates confirmation
and returns to `plan`.

For batches, vary one creative axis at a time, keep the product and measurement
window stable, validate every item before submission, and preserve per-item
status. Do not retry a permanent validation or policy error. Retry a transient
provider error at most once with a materially changed hypothesis and record the
reason; otherwise stop and surface the blocker.

## Evidence And Quality

Keep manifests limited to safe data: skill/workflow, request ID, provider name,
plan hash when applicable, output paths or safe output URLs, reference roles,
quality status, warnings, and manual-review items. Never store API keys,
headers, cookies, provider response dumps, media bytes, source paths, or raw
commands in manifests or prompts.

Quality checks have two layers:

- deterministic: schema, file existence, supported type, aspect/duration
  metadata, output location, and stable identifiers;
- semantic: identity, product fidelity, continuity, readable action, text, and
  visual/audio intent. If the layer cannot be verified, use `manual_review`.

## Boundaries

- This is an orchestrator, not a new provider, renderer, prompt-only domain
  replacement, or frontend controller.
- Use only skill names, entrypoints, workflows, and output roots registered in
  `skills/registry.json`.
- Keep source assets read-only; write only approved output and run-state paths.
- Do not publish to social platforms, alter credentials, or modify external
  accounts unless a separate registered workflow explicitly owns that action.
- Do not use absolute paths, URLs, traversal, shell fragments, or arbitrary
  provider fields where the owning harness requires managed asset IDs.

## Delivery Contract

Return a concise result shaped like:

```json
{
  "skill": "aigc-production-orchestrator",
  "route": "image_generation",
  "delegated_skill": "registered-skill-name",
  "status": "planned | awaiting_confirmation | submitted | completed | manual_review | blocked",
  "request_id": "...",
  "plan_hash": "...",
  "outputs": [],
  "warnings": [],
  "manual_review": []
}
```

Omit fields that do not apply. Use actual values only. For a blocked request,
state the exact missing input or observed error and the next permitted stage.
