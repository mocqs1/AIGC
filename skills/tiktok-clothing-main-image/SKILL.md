---
name: tiktok-clothing-main-image
description: >-
  Generate TikTok Shop-ready clothing, underwear, and sleepwear product images
  from one required product master image plus optional same-product detail
  images. Preserve the source garment's color, pattern, fabric, construction,
  logo, and visible details while composing a clear 9:16 main image or cover.
  Use for product listings, photo carousels, video covers, and creator-style
  variants; do not use for model outfit replacement or text-only fashion art.
---

# TikTok Clothing Main Image

## Mission

Create a product-led TikTok image whose first reference is the exact garment
being sold. The master image is the identity source; optional references must be
additional views or macro details of that same product. The generated scene may
change, but the garment may not be recolored, redesigned, simplified,
embellished, re-labeled, or replaced with a generic item.

The default output is a vertical 9:16 composition with the complete product
centered in a readable safe area. Prioritize fabric, seams, edges, straps,
closures, lining, labels, and other construction evidence over decorative props.
Use product-only, flat-lay, hanger, or mannequin presentation by default. A
model is optional and must be an adult in a normal, fully covered product pose.

## Required input

- One product master image, supplied as the first item in `reference_images`.
- Zero to nine optional same-product detail or alternate-view images, ordered
  after the master image.
- A presentation brief or structured fields: `purpose`, `style`, `scene`,
  `aspect_ratio`, `market`, `locale`, and optional factual `claims`.
- `presentation_mode` is `product_only` by default; use
  `adult_model_fully_covered` only when an adult, fully covered product pose is
  explicitly useful.

Accepted purposes are `shop_listing`, `photo_carousel`, `video_cover`, and
`ugc_variant`. Accepted styles are `studio_detail` and `creator_ugc`.
The host validates managed local assets or public HTTPS image URLs; do not use
video references, a model image, or a second unrelated garment.

## Prompt contract

Build an English prompt for Hermes and put the source lock before creative
direction. State that the first image is the product master and later images
are evidence of the same product. Preserve exact color, tone, print, silhouette,
proportions, panels, seams, stitch spacing, hems, edges, straps, openings,
closures, hardware, logo/label placement, fiber character, weave or knit,
opacity, thickness, stretch, sheen, drape, and visible finish. Never guess an
occluded detail.

The brief controls only presentation: framing, background, lighting, camera,
and whether an adult fully covered model is useful. Keep any overlay limited to
facts supplied by the user (for example a fiber blend, size range, or wash-care
instruction). Never invent medical, slimming, performance, certification,
review, discount, urgency, QR, URL, or platform-brand claims.

Read [references/tiktok-guidance.md](references/tiktok-guidance.md) when
selecting a purpose, aspect ratio, style, or copy treatment. Platform rules
change by market and account, so the manifest is always `manual_review` rather
than a promise of TikTok approval.

## Workflow

1. Validate one master plus at most nine distinct image references and reject
   unsafe, sexualized, minor-coded, body-shaming, or garment-altering briefs.
2. Normalize purpose/style/aspect ratio and build the source-first prompt.
3. Call `main.generate_image` through the host with
   `provider="hermes"`, `image=<master>`, and
   `references=<same-product-details>`.
4. Save the artifact and `prompt.txt` under `outputs/tiktok_clothing/`.
5. Return a manifest with source IDs, purpose/style metadata, deterministic
   file checks, and explicit visual `manual_review` reasons.

## Output contract

```json
{
  "skill": "tiktok-clothing-main-image",
  "workflow": "tiktok_clothing_image",
  "type": "image",
  "provider": "hermes",
  "purpose": "shop_listing",
  "style": "studio_detail",
  "aspect_ratio": "9:16",
  "market": "US",
  "locale": "en-US",
  "presentation_mode": "product_only",
  "copy_version": "tiktok-clothing-main-image/v1.1",
  "source_count": 2,
  "source_hashes": ["..."],
  "output_hash": "...",
  "outputs": ["outputs/tiktok_clothing/image_task.png"],
  "quality": {
    "passed": true,
    "manual_review": ["..."],
    "policy_status": "unreviewed"
  }
}
```

## Boundaries

- This skill creates images only. It does not publish listings or certify
  compliance.
- Reject a request that asks to change the source garment or invent hidden
  details. Ask for a clearer master image when identity is ambiguous.
- For underwear and sleepwear, require adult, fully covered, non-sexualized
  presentation and retain `manual_review` for age, coverage, transparency,
  and semantic fidelity.
