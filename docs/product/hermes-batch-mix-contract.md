# Hermes, batch generation, and local media: MVP product contract

Status: proposed MVP contract

This document describes the smallest useful Web UI contract for connecting
Hermes image generation, creating batches, reusing local media, and producing a
local mixed video. It is intentionally independent of a particular Hermes
transport implementation. Repository code and provider behavior take
precedence over this proposal.

## Current boundaries

- `web/src/App.jsx` submits one request at a time and polls one job.
- `web/src/api.js` currently exposes providers, jobs, assets, prompt preview,
  generation, R2 upload, and open-output-directory calls only.
- `api_server.py` accepts generation modes `image`, `video`,
  `shapewear_image`, `shapewear_video`, and `tiktok_10s`.
- `/api/assets` scans only the three output roots (`images`, `videos`, and
  `shapewear`). It does not enumerate an arbitrary user folder.
- Hermes `image_generate` accepts `prompt`, `aspect_ratio`, `image_url`, and
  `reference_image_urls`. The installed OpenAI image plugin reads the key from
  the Hermes secret store and routes through `OPENAI_BASE_URL`. It writes local
  results under the Hermes cache, so a successful result must be copied into
  an AIGC output root before the existing asset endpoint can serve it.

## User flows

### 1. Hermes settings

The top bar has a settings icon. It opens a modal with:

- Provider: `Hermes (OpenAI-compatible image)` (fixed for this MVP).
- API base URL: an `http` or `https` URL without embedded credentials.
- API key: password input, masked after entry; an empty value preserves the
  current key. A separate **Clear key** action is required to remove it.
- Image model: optional model id, defaulting to the active Hermes model.
- **Test connection**, **Save**, and **Cancel** actions.

The browser never calls the Hermes endpoint directly. Save and test requests
go to the local AIGC API, which writes the setting to the server-side Hermes
configuration/secret store. A key is write-only in the API: responses contain
`api_key_configured: true|false`, never the value.

Recommended API shape:

```text
GET  /api/settings/hermes
PUT  /api/settings/hermes
POST /api/settings/hermes/test
```

`GET` response:

```json
{
  "provider": "hermes",
  "api_url": "https://example.invalid/v1",
  "api_key_configured": true,
  "model": "gpt-image-2-high"
}
```

`PUT` request (the key is optional and write-only):

```json
{
  "api_url": "https://example.invalid/v1",
  "api_key": "new-key",
  "clear_api_key": false,
  "model": "gpt-image-2-high"
}
```

`POST /api/settings/hermes/test` returns `{ "ok": true, "provider":
"hermes", "message": "..." }` on success. Error messages must not echo a
key, authorization header, or complete upstream response body.

### 2. Batch image generation

The image composer exposes **Create batch** next to **Generate**. The user
chooses one Hermes model, aspect ratio, and a prompt template, then reviews a
table of rows before submission. A row can override the prompt and select one
primary source image plus additional reference images.

Recommended request:

```json
{
  "provider": "hermes",
  "items": [
    {
      "client_id": "row-1",
      "prompt": "...",
      "aspect_ratio": "portrait",
      "image_asset_id": "imported/abc/product.png",
      "reference_asset_ids": ["imported/abc/style.png"]
    }
  ],
  "options": { "max_concurrency": 2 }
}
```

`POST /api/generation-batches` returns `{ "batch_id": "...", "status":
"queued", "total": 1 }`. `GET /api/generation-batches/{batch_id}` returns
the aggregate status and item records. The item states are `queued`, `running`,
`succeeded`, `failed`, or `cancelled`; aggregate status is `queued`, `running`,
`succeeded`, `partial`, `failed`, or `cancelled`.

MVP limits: at most 100 items per batch, at most 16 reference images per item,
and a server-clamped concurrency of 1-4. The API should accept an idempotency
key so a double click does not create duplicate batches. A failed item can be
retried without resubmitting successful items.

### 3. Local media library

The composer has a **Local media** drawer with image/video filters, search, and
multi-select. For the browser MVP, import through a directory file input
(`webkitdirectory`) and `multipart/form-data`; do not accept an arbitrary raw
filesystem path from an untrusted request. The server copies files into a
managed library such as `outputs/imported/<batch-id>/` and returns opaque asset
ids.

Asset record:

```json
{
  "id": "imported/abc/product.png",
  "name": "product.png",
  "media_type": "image",
  "url": "/api/assets/imported/abc/product.png",
  "size": 123456,
  "source": "local",
  "modified_at": "2026-08-26T00:00:00Z"
}
```

Supported images are `.jpg`, `.jpeg`, `.png`, `.webp`, and `.gif`; supported
videos are `.mp4`, `.mov`, and `.webm`. Reject unsupported files, empty files,
oversized files, path traversal, and symlink/junction escapes. Never expose an
absolute local path in JSON or HTML. Existing generated output assets remain
selectable alongside imported assets.

### 4. Reuse and local mixed video

There are two distinct operations:

1. **Image reuse/edit:** selecting a local or generated image maps to Hermes
   `image_url`; additional selected images map to
   `reference_image_urls`. The server resolves asset ids and passes local paths
   to Hermes. It then copies the result to `outputs/images` and registers it as
   a normal asset.
2. **Local mix:** selecting ordered image/video clips opens a simple timeline.
   Each clip has a duration (images default to 3 seconds), optional trim for
   videos, and a `cut` or `fade` transition. The user may select one optional
   audio track. This is a local FFmpeg job; it must not be sent to Hermes.

Recommended mix request:

```json
{
  "clips": [
    { "asset_id": "imported/abc/a.png", "duration_ms": 3000 },
    { "asset_id": "videos/video_001.mp4", "start_ms": 0, "end_ms": 5000 }
  ],
  "aspect_ratio": "portrait",
  "transition": "cut",
  "audio_asset_id": null
}
```

Use `POST /api/mixes` and return a normal job id (or a job with `kind: "mix"`)
so the existing history/polling surface can be reused. Output is an MP4 under
`outputs/videos`; input files must remain unchanged. If FFmpeg is unavailable,
fail before enqueueing with an actionable configuration error.

## Acceptance checklist

### Settings and Hermes

- [ ] Settings opens and closes without losing unsaved form values.
- [ ] Saving a valid URL/key/model updates `/api/providers` and the image
      composer without restarting the server.
- [ ] Test connection reports success/failure and never displays a secret.
- [ ] A fresh page load shows only `api_key_configured`, not the key itself.
- [ ] Invalid URL, blank required key, and upstream auth failure are shown as
      field-level errors.
- [ ] A Hermes image result is copied from Hermes cache into `outputs/images`,
      appears in `/api/assets`, and can be previewed/downloaded.
- [ ] Image edit with one primary and additional references works; a 17th
      reference is rejected with a clear limit message.

### Batch

- [ ] A batch of three rows returns one batch id and shows aggregate plus
      per-row progress.
- [ ] Successful rows remain available when another row fails (`partial`).
- [ ] Retry only resubmits failed rows; double submission with the same
      idempotency key does not duplicate work.
- [ ] Batch state survives a page refresh and completed assets appear in
      history/library.
- [ ] Server clamps concurrency and does not exceed the configured worker or
      provider rate limit.

### Local media and reuse

- [ ] Importing a folder with images and videos lists thumbnails, type filters,
      file sizes, and stable opaque ids.
- [ ] Unsupported, empty, oversized, traversal, and symlink/junction files are
      rejected without partial unsafe writes.
- [ ] Selected local images can be reused in Hermes image editing and in a
      later batch; no absolute path is sent to the browser.
- [ ] Existing generated outputs and imported assets are distinguishable but
      selectable from one library.

### Mix

- [ ] User can order at least three clips, change an image duration, trim a
      video, choose cut/fade, submit, and watch job progress.
- [ ] Successful mix previews and downloads as a valid MP4 and is listed in
      history; source files are byte-for-byte unchanged.
- [ ] Missing FFmpeg, invalid media, and incompatible audio fail with stable,
      actionable messages and no stack trace or secret.

## Risks and decisions to keep visible

- Hermes endpoint compatibility is not guaranteed by a URL alone. Test the
  configured OpenAI-compatible endpoint before enabling the provider; a valid
  `/v1/models` response can still hide image-task capacity failures.
- Hermes' image plugin currently reads `OPENAI_BASE_URL`, not the chat model's
  `model.base_url`; the save path must update the setting Hermes actually uses.
- Current AIGC job persistence stores only terminal single jobs. Batch and mix
  records need a durable store and restart recovery before claiming resilience.
- Browser folder selection and server-side arbitrary path scanning have very
  different security profiles. Prefer managed multipart import for the MVP.
- FFmpeg availability, codec support, and large video sizes can make local mix
  slow. Surface progress and enforce input/output size limits.
