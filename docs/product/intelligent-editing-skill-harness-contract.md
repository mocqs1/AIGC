# Intelligent editing Skill and harness contract

Status: proposed MVP product contract  
Date: 2026-08-27  
Owner: AIGC Studio product

This contract defines the project-local Codex Skill and local execution harness
for **智能混剪**. It complements
`docs/product/intelligent-mix-api-config-contract.md`; that document remains
authoritative for the HTTP and Terra module contracts. Repository behavior
outranks this proposal where they differ.

## Purpose and boundaries

The Skill turns a user editing objective and two to fifty selected managed
image/video assets into a reviewable `aigc-mix-plan/v1` plan. The harness
validates the plan, requires explicit acceptance, and renders only the accepted
plan locally with FFmpeg.

- Imported and generated images/videos remain reusable library assets after any
  mix. The harness writes only a new output and evidence artifacts; it never
  edits, moves, or overwrites a source asset.
- Terra is an optional planner, never a renderer or command executor. Missing,
  disabled, timed-out, or invalid Terra planning selects a deterministic local
  fallback instead of preventing local rendering.
- The planner receives opaque selected asset IDs, the editing objective, and
  safe metadata only: media type, locally measured duration where available,
  dimensions, byte-size bucket, and allowed trim bounds. It receives no media
  bytes, thumbnails, absolute paths, filenames, EXIF, credentials, cookies, or
  unrelated user data.
- Current MVP planning is metadata and user-intent based. It makes no claim to
  understand pixels, scene semantics, speech, music, beats, audio quality, or
  copyright status.

## User intents and Skill activation

The project-local Skill activates when a request concerns arranging selected
managed images/videos into one locally rendered edit, including:

- “Use these product clips to make a fast 12-second portrait mix.”
- “Create a 20-second recap from the selected assets; emphasize variety.”
- “Plan a calm square sequence from these images and videos, then let me
  review it.”
- “Retry the interrupted mix without changing the approved edit.”

The Skill must request or reject incomplete work rather than inventing assets,
access, or rendering permission.

Near-miss cases outside this Skill:

- Generating a new image/video, retouching an image, or changing the pixels of
  a source asset: generation/editing workflow, not intelligent editing.
- “Find the best moments,” beat-sync, transcript-aware edits, or semantic scene
  selection: unavailable until approved media/audio analysis exists.
- Exporting a plan without rendering: plan-preview workflow; no confirmation is
  needed unless a render is submitted.
- Rendering arbitrary filesystem paths, URLs, or assets not selected from the
  managed library: reject; import/select them through the managed asset flow.
- Replacing, deleting, or trimming the source file itself: reject; only
  non-destructive output is supported.

## Required input and output contracts

### Input

The harness accepts one immutable planning request with:

```json
{
  "request_id": "opaque client request id",
  "selected_assets": [
    {
      "asset_id": "opaque managed asset id",
      "media_type": "image",
      "duration_ms": 3000
    },
    {
      "asset_id": "opaque managed asset id",
      "media_type": "video",
      "trim_bounds_ms": { "start": 0, "end": 12000 }
    }
  ],
  "objective": "tight product recap",
  "aspect_ratio": "portrait",
  "target_duration_ms": 12000,
  "transition_mode": "auto",
  "planner": "codex_terra"
}
```

Required invariants:

- `selected_assets` contains 2-50 distinct, managed asset IDs in the user’s
  initial order; all IDs resolve inside approved managed roots.
- `objective`, aspect ratio, target duration, image default duration, allowed
  video trims, and transition preference are explicit. A target duration is
  bounded to the renderer limit and cannot exceed five minutes.
- The server derives safe metadata after resolving IDs. Browser and remote
  planner requests never contain local paths or raw source content.
- An idempotency key scopes planning and rendering submissions. Repeating a
  completed request returns its existing plan/job/evidence rather than creating
  another output; a failed resumable stage restarts from its last durable
  checkpoint when inputs and accepted plan hash match.

### Plan preview

A proposed plan is always a complete `aigc-mix-plan/v1` object. It includes
planner identity (`codex_terra` or `local`), selected-asset clips only,
start times, source trims where applicable, per-clip duration, supported
transition, total duration, warnings, input fingerprint, and canonical plan
hash. The preview displays order, timing, transition, total duration, planner,
and every warning before it exposes **Render**.

The preview is not an authorization to render. Render requires an explicit
acceptance action referencing the displayed plan hash. Any input or plan change
invalidates prior acceptance and returns to preview.

### Render result and evidence

A terminal mix record returns an output asset ID and safe, durable evidence:

- request ID, accepted plan hash/version, planner selected and actually used;
- selected asset IDs plus safe input fingerprints; validation, fallback, and
  renderer warnings; timestamps and stage outcomes;
- FFmpeg version/capability check result, normalized output duration, output
  asset ID, and a failure code/message when unsuccessful.

Evidence must be sufficient to reproduce or diagnose the run from managed
assets and the accepted plan. It must not contain source paths, commands,
credentials, authorization headers, raw provider responses, or media bytes.

## Plan quality criteria

Validation enforces safety; the following rules define an objectively good MVP
plan and are evaluated before preview.

| Dimension | Criterion |
| --- | --- |
| Pacing | Each clip is 500-60,000 ms. Image clips use the configured default unless the plan states a bounded override. Consecutive clips may not have the same source asset ID. No clip starts before the prior clip ends. |
| Clip variety | With at least four unique selected assets, the plan uses at least three unique IDs before repeating one; with two or three assets, it cycles through all selected IDs before a repeat where duration permits. A plan may omit an asset only with a visible warning naming the omission. |
| Duration accuracy | `abs(total_duration_ms - target_duration_ms)` is at most 500 ms, or at most 5% of target duration when the target is under 10 seconds. If achievable only by violating clip or trim bounds, the plan uses the closest valid duration and warns with the measured delta. |
| Source and renderer safety | Every clip references exactly one selected asset, uses a trim within known bounds, and contains only renderer-supported fields. No URL, path, FFmpeg option, output name, or executable instruction is accepted from the plan. |
| Fallback | The local fallback preserves selected order, alternates available assets before repeats, uses configured image duration and valid video trims, hard cuts, and the closest valid target duration. Its preview identifies `local` and states why it was selected. |

These criteria measure timing and asset rotation, not creative or visual merit.

## Harness stages

1. **Validate request**: authenticate the local action as required, de-duplicate
   by idempotency key, resolve only selected managed IDs, collect safe metadata,
   check FFmpeg availability, and persist the immutable request fingerprint.
2. **Plan**: call Terra only when selected and configured; otherwise generate
   deterministic local rules. Parse the response as data, never as instructions.
3. **Validate and normalize**: enforce `aigc-mix-plan/v1`, asset containment,
   clip/trim/duration/transition limits, the quality criteria, and plan hash.
   Invalid Terra output is recorded as a warning and replaced by local rules.
4. **Preview and confirm**: persist and show the normalized plan. Render remains
   unavailable until the user explicitly accepts that exact hash.
5. **Render**: build a fixed FFmpeg argument array from validated fields only;
   write a new temporary output, verify it, then atomically publish a new
   managed output asset. Sources are read-only throughout.
6. **Record and resume**: persist stage state and evidence after each durable
   boundary. A duplicate request reuses the matching terminal record; an
   interrupted request either resumes safely from its checkpoint or returns a
   stable recovery error without altering sources.

## Acceptance criteria

- [ ] A user selects 2-50 managed local/generated images or videos and receives
  a previewed `aigc-mix-plan/v1` before any FFmpeg work begins.
- [ ] The preview lists planner, clip order, per-clip timing/trims,
  transitions, total duration, warnings, and plan hash; cancelling creates no
  output.
- [ ] Render cannot start without explicit acceptance of the exact displayed
  plan hash. Editing the request or plan requires a new preview and acceptance.
- [ ] Terra receives only opaque IDs and the documented safe metadata; source
  paths, filenames, media bytes, EXIF, secrets, and credentials are absent from
  its request and all evidence artifacts.
- [ ] A missing, disabled, timed-out, malformed, or unsafe Terra response
  produces a previewable deterministic `local` plan with a visible reason
  and allows rendering to continue.
- [ ] Every accepted plan passes the pacing, variety, duration-accuracy, and
  renderer-safety criteria, or records the specific closest-valid-duration or
  omitted-asset warning.
- [ ] A successful render creates a separately managed output asset and records
  the plan, planner/fallback, stage results, and output duration. Selected
  source files remain byte-for-byte unchanged.
- [ ] Repeating the same idempotency key and matching input/plan fingerprints
  neither invokes FFmpeg again nor creates another output. Interrupted work
  resumes from durable state or fails with a stable, actionable recovery error.
- [ ] Invalid assets, unsupported media, impossible trims, unavailable FFmpeg,
  and output verification failures fail before publication with secret-free,
  actionable messages.

## MVP non-goals

- Pixel, object, scene, face, text, speech, music, beat, transcript, or audio
  analysis; semantic clip ranking; or claims of audiovisual understanding.
- Audio mixing, beat matching, captions, color grading, visual effects,
  transition types beyond renderer-supported hard cuts, or automatic copyright
  clearance.
- Source-file mutation, arbitrary path/URL rendering, remote FFmpeg execution,
  or passing commands/options from Terra to FFmpeg.
- Provider streaming, multi-agent editorial review, multi-user approvals, or
  cloud secret management.

## Assumptions and unresolved risks

Assumptions: the managed library can resolve opaque asset IDs safely; FFmpeg can
probe and render the supported formats; durable job storage can retain request,
plan, and stage evidence; the existing Terra adapter remains optional.

Unresolved risks: media duration probing and codec availability can make the
closest-valid-duration calculation differ by platform; an interrupted FFmpeg
process needs a tested checkpoint/publication protocol before resumability is
claimed in production; timing-only quality rules cannot guarantee a visually or
musically coherent edit. These risks require implementation validation, not
expanded remote media disclosure.
