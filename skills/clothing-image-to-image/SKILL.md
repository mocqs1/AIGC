---
name: clothing-image-to-image
description: >-
  Generate a commercial image from one clothing reference image while treating
  that garment as the immutable product source. Use for clothing product
  renders, fabric or construction detail images, flat lays, studio catalog
  shots, and fashion material showcases when the user supplies a garment image.
  Do not use for model try-on or outfit replacement, which requires the
  model-outfit-swap skill.
---

# Clothing Image To Image

## Mission

Create a new product image whose garment is copied from the supplied clothing
image. The reference garment is the source of truth: preserve its exact color,
pattern, silhouette, cut, construction, panels, seams, edges, closures,
straps, openings, hardware, logo placement, fabric appearance, texture, and
visible wear or finish. Never recolor, redesign, simplify, embellish, or
invent garment details.

The image should make craftsmanship, fabric, construction, and function easy
to inspect. A model is optional. Prefer a clean product-only composition,
flat-lay, mannequin, hanger, or detail close-up when a model is not necessary.
Use a model only when requested and keep the garment fully visible at the
important construction points.

## Required input

- Exactly one garment reference image, passed as `image` or as the only item
  in `reference_images`.
- A short objective describing the desired presentation, such as fabric macro,
  seam construction, stretch recovery, pocket/closure detail, flat lay, or
  premium catalog view.
- Optional scene, camera, lighting, aspect ratio, and model preference.

The reference must be a managed local image asset or a public HTTPS image URL.
Do not accept local filesystem paths from browser input, video references, or a
second person/scene image. Source assets are read-only.

## Prompt contract

Build an English provider prompt and append the immutable contract below after
the user objective. The objective may change presentation, camera, scene, and
lighting, but never garment identity or product details.

Start every provider prompt with an explicit image-binding instruction. The
attached image is the garment source itself, not an inspiration image or a
generic style reference. It is the highest-priority conditioning input; do not
fall back to a text-only garment. If the garment cannot be recognized clearly,
fail the request for a clearer source image instead of inventing a replacement.

```text
IMAGE INPUT BINDING: The attached image is the exact garment to present. Use
that image as the single source of truth and preserve its identity in the
output. Do not generate a generic clothing item from the text prompt and do
not substitute another garment.
```

```text
IMMUTABLE GARMENT CONTRACT: Treat the supplied clothing reference as the
single source of truth for the garment. Reproduce the exact garment shown:
color, hue, tone, pattern, print, silhouette, proportions, cut, panels, seams,
stitching, hems, edges, straps, openings, closures, hardware, logos, labels,
material, weave, texture, sheen, thickness, and visible finish. Do not recolor,
redesign, add, remove, smooth away, or invent any garment detail. Do not turn
the garment into a different product. Keep construction physically plausible
and make fabric and workmanship clearly inspectable. Only change the requested
presentation context; the garment itself must remain unchanged.
```

Also instruct the provider to avoid beauty retouching that hides texture,
unreadable crops, occluding hands or props, and visual effects that obscure
construction. Do not make unsupported medical, body-shaping, performance, or
certification claims from the image.

This workflow uses a configured image provider. Prefer Hermes when that slot
has an API key; otherwise use Hermes Volcano. Do not invent a third routing
path or call an upstream provider from the browser.

## Workflow

1. Validate that there is exactly one image reference and bind it as the
   garment source.
2. Guard the user objective against minors, nudity, explicit sexual content,
   body-shaming, medical guarantees, and instructions to alter garment color
   or construction.
3. Build the prompt with the immutable garment contract and a presentation
   direction emphasizing material and construction detail.
4. Call `main.generate_image` with the selected `provider` (`hermes` or
   `hermes_volcano`), `image=<garment reference>`, and no additional
   reference images. Never call an upstream provider directly from the browser.

5. Save the generated artifact and final `prompt.txt` under
   `outputs/clothing_image/` when the host can write files.
6. Return a manifest and mark semantic garment fidelity for visual manual
   review. Text cannot prove that pixels, colors, or fine construction details
   were preserved.

## Output contract

```json
{
  "skill": "clothing-image-to-image",
  "workflow": "clothing_image_to_image",
  "type": "image",
  "provider": "hermes | hermes_volcano",

  "prompt": "...",
  "garment_reference": "images/garment.png",
  "outputs": ["outputs/clothing_image/image_task.png"],
  "quality": {
    "passed": true,
    "manual_review": [
      "garment color, pattern, silhouette, and all visible details match the source",
      "fabric, seams, construction, and functional details are readable",
      "the requested presentation does not hide or distort the garment"
    ]
  }
}
```

## Boundaries

- This skill creates images only. It does not publish, sell, certify, or make
  claims about a product's medical or performance outcomes.
- Reject requests to change the garment's color, pattern, logo, construction,
  or other source details. Ask for a new source image instead.
- Reject minors, nudity, explicit sexual content, fetish styling, body-shaming,
  and requests to alter a person's body.
- If the source image is too small, occluded, or ambiguous to inspect, keep the
  request for manual review rather than claiming exact preservation.
