---
name: model-outfit-swap
description: >-
  Generate a commercial image by preserving an adult model from one reference
  image and replacing only the clothing using one or more outfit references,
  with exact visual restoration of the selected garment's fabric, material,
  surface texture, color, pattern, construction, and manufacturing details.
  Use this skill whenever a user requests model try-on, virtual fitting,
  clothing replacement, fabric restoration, garment-detail matching, or a
  photo-accurate outfit swap, even if they do not name the skill.
---

# Model Outfit Swap

## Provider adapters

The workflow supports manual selection between the configured `hermes` GPT
Image 2 slot and the `hermes_volcano` Volcengine Seedream slot. `hermes` is the
default for backward compatibility. The ordered reference contract is shared,
but prompt policy and transport are provider-specific; never send the GPT
Image 2 edit prompt to Seedream unchanged.

### Hermes gpt-image-2 adapter

The configured `hermes` slot uses `gpt-image-2`, so this workflow uses a
source-first edit contract rather than the Ark Seedream creative ordering. The
first image is the fixed model canvas; every later image is evidence for the
same garment. The edit mask is limited to clothing pixels and the model, scene,
camera, and lighting remain unchanged. Presentation language never outranks
the selected garment source.

For `gpt-image-2`, references are sent as real multipart files to the
OpenAI-compatible `POST /images/edits` endpoint. The first file is the model
master and subsequent `image[]` files are garment evidence. Never encode these
references as `image_url` or `reference_image_urls` fields on
`/images/generations`; that text-to-image route may ignore them and still return
an unrelated successful image.

The adapter asks `gpt-image-2` to preserve the selected garment's color, fiber
character, weave/knit direction, texture scale, thickness, stretch, compression,
sheen, silhouette, panels, seams, stitch spacing, hems, bindings, openings,
straps, hardware, lining, padding, labels, logos, and visible manufacturing
evidence. Hidden or ambiguous details are not invented. This is a near-exact
visual target that still requires human review; no prompt can guarantee literal
pixel identity.

Use an image-to-image fashion workflow where the ordered references have a
fixed role contract:

- Reference 1 is the model master image. It is never treated as a garment.
- References 2 through 10 are garment or shapewear product images for the
  same outfit. They are additional product views, not people or scenes.
- At least two distinct image references are required. The host API accepts
  managed local assets and public HTTPS image URLs; the Harness accepts managed
  asset IDs only.

The edit mask is clothing-only. Preserve the adult model's identity, face,
hair, expression, skin, hands, feet, pose, anatomy, body proportions, complete
background and objects, lighting and shadows, camera/lens/viewpoint, framing,
crop, and aspect ratio. The selected garment is the source of truth: target an
exact visual match with zero tolerated deviation in fiber/material identity,
weave or knit direction, texture scale, thickness, weight, opacity, stretch,
compression, sheen, color, pattern, drape, panels, seams, stitch spacing,
edges, straps, openings, hardware, lining, padding, labels, logos, and finish.
Do not smooth, beautify, simplify, recolor, substitute, invent, remove, redraw,
or alter any garment, product, or scene detail.

The runtime uses an immutable provider-specific prompt and ignores product, scene,
style, and custom prompt fields for the garment replacement itself. Semantic
preservation cannot be proven from text alone, so every successful artifact
requires visual manual review.

### Hermes Volcano Seedream adapter

`hermes_volcano` uses the Ark JSON image-generation protocol and an independent
Seedream source-locked clothing prompt. Image 1 remains the model master and
later images remain garment evidence. Keep the garment's color, material,
texture, construction, and visible manufacturing details unchanged; presentation
briefs may affect display only. Semantic preservation still requires manual
visual review.

The provider contract is intentionally bounded to ten total images because
Hermes/Seedream accepts one model image plus up to nine garment references.
Multi-reference requests use the provider's default single composite output;
the Seedream 5.0 Pro model rejects the optional sequential-generation switch.
The runtime derives `portrait`, `landscape`, or `square` from the model master
image so the generated canvas does not silently crop or pad the source
composition.

## Garment Evidence Protocol

Treat every selected garment image as production evidence, not inspiration.
Use all garment references jointly and inspect them at both full-garment and
close-up scale before generation. Repeated evidence across references is
authoritative. If two references differ, preserve the feature that is clearly
supported by the matching view and do not merge incompatible designs. If a
feature is hidden or ambiguous, do not invent it or replace it with a generic
fashion equivalent.

The fidelity gate covers seven dimensions:

- material identity and finish, including fiber character and textile type;
- surface structure, weave/knit grain, texture relief, print registration, and
  transparency;
- physical behavior, including thickness, weight, stiffness, stretch,
  compression, sheen, drape, tension lines, folds, wrinkles, and edge roll;
- pattern and color, including hue, value, saturation, repeat, scale, and
  alignment under the preserved scene lighting;
- garment construction, including panels, cut lines, seams, stitch type and
  spacing, topstitch, overlock, binding, hems, darts, pleats, channels,
  reinforcements, and bartacks;
- components, including closures, elastic, straps, adjusters, cups, lining,
  padding, boning, pockets, labels, logos, and hardware;
- absence of invention: no missing, smoothed, simplified, recolored,
  substituted, or guessed garment evidence.

The exact-match target requires visual human review. File presence and format
checks cannot prove material or construction identity, so never report an
unreviewed result as verified.

## Workflow

1. Validate the ordered references and bind the first item to `model_image`.
2. Bind every later item to `outfit_images` without reordering or deduplicating.
3. Use the fixed clothing-only prompt and immutable edit contract. User-provided
   generation briefs are never forwarded to the image provider.
4. Call `main.generate_image` with `image=model_image` and
   `references=outfit_images`.
5. Save the artifact and `prompt.txt` under the configured output directory.
6. Return a manifest with the garment-fidelity contract and mark every
   material, construction, component, and model-preservation check for manual
   review.

For API-driven use, call `harness.model_outfit_swap.ModelOutfitSwapHarness`.
Its `preview` method returns the ordered role manifest and its `submit` method
delegates to `/api/generations` using only managed IDs. It never accepts source
paths, provider commands, credentials, or raw provider responses.

## Safety

Only adult, non-explicit commercial fashion imagery is supported. Reject minors,
nudity, explicit sexual content, body-shaming, or requests to alter body
structure. Do not invent product facts, logos, brand affiliation, or medical
claims.

## Output Contract

```json
{
  "skill": "model-outfit-swap",
  "workflow": "model_outfit_swap",
  "type": "image",
  "prompt": "...",
  "references": ["model", "outfit", "outfit-detail"],
  "outputs": ["outputs/images/model_outfit_swap/image_task.png"],
  "garment_fidelity": {
    "standard": "exact_visual_match",
    "verification_status": "pending_manual_review",
    "requires_visual_review": true,
    "automatic_guarantee": false
  },
  "quality": {
    "passed": true,
    "aspect_ratio": "portrait",
    "manual_review": [
      "model identity, anatomy, pose, background, and composition are unchanged",
      "only clothing changed; material, fabric behavior, color, construction, and all product details match the references"
    ]
  }
}
```
