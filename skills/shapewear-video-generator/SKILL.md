---
name: shapewear-video-generator
description: >-
  Generate polished, non-explicit shapewear marketing images, model try-on
  videos, and 9:16 TikTok ad assets. Use this skill whenever a request mentions
  塑身衣, shapewear, bodysuit, or waist trainer. Also use it for lingerie
  advertisement or fashion commercial requests when the brief is explicitly
  about shapewear or one of those garment terms, even if the user does not name
  this skill. Route production through the AIGC engine
  entrypoints and the bundled prompt/workflow templates.
---

# Shapewear Video Generator

## Mission

Act as a fashion-commerce art director and production operator. Turn a
shapewear brief into a clear product image, a coherent model try-on video, or a
short vertical ad asset. Keep the garment as the subject: construction,
compression zones, fit, texture, and silhouette must remain inspectable.

## Product image fidelity contract

The `shapewear_image` branch is a product-catalog workflow, not a freeform
fashion redesign. Hermes remains the default image provider, while the same
workflow can use the independently configured Hermes Volcano image slot. The
legacy `liblib` request name is accepted as an alias. If the selected provider
reports quota, rate-limit, credit, or capacity exhaustion, the host may retry
once with the other configured image provider. A supplied
product image is the highest-priority source of truth and must remain the same
garment. The text brief may choose presentation, camera, lighting, and scene,
but it cannot override the source or the fidelity contract.

For every image, preserve and inspect these dimensions:

- product identity: silhouette, proportions, rise, neckline, armholes, leg
  openings, cups, straps, side/back panels, compression zones, gusset/crotch,
  closures, lining, padding, boning, labels, logos, and hardware;
- textile identity: declared fiber or material, weave/knit direction, yarn or
  rib scale, mesh, denier, opacity, thickness, stretch, compression, recovery,
  sheen, nap, drape, and tension behavior;
- manufacturing evidence: seam placement, stitch type and spacing, flatlock,
  coverstitch, overlock, bonded seam, binding, folded or laser-cut hem,
  elastic, silicone gripper, bartack, reinforcement, and edge roll.

Only details visible in the source or explicitly supplied may be rendered.
Never guess hidden areas, recolor, simplify, smooth away, embellish, or turn
the product into ordinary lingerie, swimwear, a generic bodysuit, or decorative
fashion. The output target is an exact visual match; automated file checks do
not prove semantic fidelity, so every image remains `pending_manual_review`.

### Provider-specific image prompts

For the configured `hermes` slot (`gpt-image-2`), the selected product main
image is bound first and outranks the text brief, scene, style, lighting, and
model suggestion. Additional images are views of that same product. Keep the
complete garment readable at a useful scale; use product-only or mannequin
presentation unless a model is explicitly requested. Camera, lighting, and
scene are display controls only and cannot change the product.

The `hermes_volcano` slot remains Seedream-compatible and keeps the existing
prompt contract. Do not reuse the Seedream creative ordering for `gpt-image-2`.

## Success Bar

- The product is recognizable in the first frame and remains the visual anchor.
- Prompts specify garment construction, material, lighting, camera, motion, and
  duration instead of relying on generic words such as "beautiful" or "viral".
- Video beats have a simple advertising arc: hook, demonstration, detail, and
  CTA. The requested duration and 9:16 framing are explicit for TikTok assets.
- Outputs are saved under `outputs/shapewear/` with the final prompt recorded in
  `prompt.txt` when the host can write files.

## Trigger And Intake

Trigger on the terms in `skills/registry.json`. Collect the minimum available
brief: product/garment, color, material, scene, style, target market, format,
duration, and reference image. Do not block on optional fields; use conservative
commercial defaults and state them in the returned manifest.

## Workflow

1. Classify the request as `image`, `video`, or `tiktok_ad`. Treat `tiktok_ad` as
   a video with a 9:16 frame and the `tiktok_10s.yaml` timeline unless the user
   gives another duration.
2. Load only the relevant resource files:
   - Images: `prompts/image_prompts.yaml` and `workflows/shapewear_image.json`.
   - Videos: `prompts/video_prompts.yaml` and `workflows/shapewear_video.json`.
   - TikTok ads: add `prompts/marketing_hooks.yaml` and
     `templates/tiktok_10s.yaml`.
3. Build an English prompt with the chosen style block plus the user's product,
   color, material, scene, audience, camera, aspect ratio, and duration. For an
   image, always append the immutable product-fidelity contract. A user
   `prompt` is a presentation brief only and must never replace that contract.
4. Call `main.generate_image` for an image with the selected `hermes` or
   `hermes_volcano` provider; preserve the ordered reference image list (product source
   first when present). The host performs at most one quota/capacity fallback
   to the other configured image provider. For video, call
   `main.generate_video`; pass the generated image path as `image` when the
   workflow is image-to-video. Inject a mock client in tests and never spend
   real provider credits during validation.
5. Save or return the provider paths. Keep a small manifest containing the
   request type, prompt, workflow name, provider, and output path.
6. Run the quality gate below. If a semantic check cannot be verified from the
   available media, mark it `manual_review` instead of claiming it passed.

## Batch commerce variants and remix

For a campaign batch, use `skills.shapewear_video_generator.batch.plan_batch`
with 2-50 selected managed asset IDs and one platform preset (`tiktok`,
`reels`, or `shorts`). The planner creates stable variant IDs and changes one
creative axis at a time (hook, proof order, rhythm, CTA, or locale). Every
variant contains four explicit beats: `hook`, `demonstration`, `detail`, and
`cta`, plus a portrait safe-zone configuration and an embedded
`aigc-intelligent-editing` request.

Call `validate_batch` before submitting any variant. Use
`harness.intelligent_editing.shapewear_batch.ShapewearBatchHarness` to preview
the complete batch, collect every variant's plan hash, require explicit hashes
for every confirmation, then submit renders through the existing
`IntelligentEditingHarness`; it remains the only path to the local mix API and
FFmpeg.
`batch_manifest` records only safe output asset URLs and per-item manual-review
status. It never stores source paths, provider responses, commands, or keys.

Batch output is an experiment set, not a promise of reach or sales. Keep the
product, offer, audience, attribution window, and primary metric constant for a
test cell, and measure retention, proof-beat completion, qualified clicks,
add-to-cart/purchase where attribution is defined, plus complaint/refund and
policy-rejection guardrails. Failed or unverifiable semantic checks stay in
`manual_review`; they are not reported as platform-approved or conversion-ready.

## Decision Rules

- Use `luxury_fashion` for controlled studio stills (product-only or headless
  mannequin), `fashion_campaign` for an adult fully covered high-end fashion
  advertisement, `tiktok_ugc` for a standing mirror/phone-camera try-on still,
  and `product_detail` for fabric or seam close-ups. Combine one primary block
  with at most two supporting blocks.
- The selected advertising template must drive both the prompt block and the
  structured scene/style fields. Do not keep a product-only studio brief when
  `fashion_campaign` or `tiktok_ugc` is selected. Keep any on-camera adult fully
  covered.
- Video clip presets may send `clip_id`, `duration_seconds`, `aspect_ratio`, and
  `resolution`. Substitute those values into the video prompt block. Keep
  `product_detail` on the fabric-macro video block; do not collapse it to the
  studio-walk template. A landscape clip must keep 16:9 and `1920x1080`.
- Use image-to-video when a product image or generated keyframe exists and visual
  continuity matters. Use text-to-video only when no reference image is
  available.
- Prefer one readable action per shot. A ten-second ad should not contain more
  than four beats or rapid wardrobe changes.
- Preserve a user's market and language in on-screen copy, but write generation
  prompts in English for provider consistency.

## Output Contract

Return a concise manifest with:

```json
{
  "skill": "shapewear-video-generator",
  "workflow": "shapewear_image or shapewear_video",
  "type": "image or video",
  "provider": "hermes or hermes_volcano for shapewear_image",
  "provider_requested": "hermes",
  "provider_fallback": {"from": "hermes", "to": "hermes_volcano", "reason": "quota_or_capacity"},
  "prompt": "...",
  "outputs": ["outputs/shapewear/..."],
  "reference_roles": ["product_master", "same_product_detail_or_alternate_view"],
  "garment_fidelity": {"standard": "exact_visual_match", "verification_status": "pending_manual_review"},
  "quality": {"passed": true, "manual_review": []}
}
```

Use the actual paths returned by the AIGC engine. Do not invent URLs or claim a
provider completed a task when it failed or was not called.

## Quality Gate

Run `quality_check.py` for deterministic file checks, then inspect the media (or
ask an enabled vision evaluator) for the semantic checks:

Images:

- garment is fully present and not cropped at key construction points;
- silhouette, compression zones, panels, gusset, straps, openings, closures,
  and every visible component match the source or declared attributes;
- fiber/material character, weave or knit direction, thickness, opacity,
  stretch, recovery, sheen, and drape are readable and plausible;
- seam placement, stitch construction, hems, bindings, bonded/laser-cut edges,
  elastics, labels, logos, and hardware match without invention;
- anatomy, hands, and proportions are not visibly distorted and body shape is
  not altered;
- no detail is recolored, redesigned, simplified, or replaced; ambiguous areas
  remain manual review.

Videos:

- product is prominent and visible across the main beats;
- garment and body do not melt, warp, or change identity between frames;
- hook -> demonstration -> detail -> CTA reads in the requested duration;
- canvas is vertical 9:16 for TikTok and text stays inside safe margins.

If any check fails, revise the prompt or mark the output for regeneration. Do not
silently ship a failed semantic check.

## Boundaries And Anti-Patterns

- This skill creates assets only. It does not publish to TikTok, manage users,
  collect analytics, or make purchases.
- Do not use minors, explicit sexual content, body-shaming language, medical
  guarantees, or unsupported claims such as guaranteed weight loss or health
  outcomes.
- Do not claim affiliation with SKIMS or another brand. Translate brand
  references into high-level attributes such as minimal, premium, or editorial.
- Avoid generic stock-photo prompts, excessive beauty retouching, impossible
  anatomy, unreadable CTA text, rapid cuts that hide the garment, and invented
  result URLs.

## Resources

- `prompts/image_prompts.yaml`: image style and detail blocks.
- `prompts/video_prompts.yaml`: luxury and UGC video structures.
- `prompts/marketing_hooks.yaml`: hook and CTA options.
- `templates/*.yaml`: reusable campaign timelines.
- `workflows/*.json`: execution order and quality checks.
- `quality_check.py`: deterministic artifact validation.
