# Shapewear e-commerce video research requirements

Status: proposed research and requirements artifact  
Date: 2026-08-28  
Owner: AIGC Studio product/research

This document scopes a project-local Skill and Herdr/harness workflow for
Chinese-market and global shapewear e-commerce video assets. It complements
`skills/shapewear-video-generator/SKILL.md` and
`docs/product/intelligent-editing-skill-harness-contract.md`. It is a
requirements and experiment brief, not a traffic or conversion guarantee.

## Evidence labels

**Observed in this repository** means directly supported by current local
artifacts:

- The existing Skill requires a hook -> demonstration -> detail -> CTA arc,
  explicit 9:16 framing for TikTok, one readable action per shot, and no more
  than four beats in a ten-second ad (`skills/shapewear-video-generator/SKILL.md`).
- Its quality gate requires product prominence, stable garment/body identity,
  readable construction/seams/compression zones, and manual review when a
  semantic check cannot be verified.
- The existing contracts require managed reusable assets, immutable sources,
  plan-before-render, explicit acceptance, deterministic local fallback, and
  no pixel/audio understanding in the MVP.

**Researched external guidance** is policy/specification material to verify at
implementation time and by market/account:

- TikTok for Business creative guidance and ad specifications:
  <https://ads.tiktok.com/business/creativecenter/inspiration/topads/pc/en>
  and <https://ads.tiktok.com/help/article/video-ads-specifications>
- Meta Reels ad guidance:
  <https://www.facebook.com/business/ads-guide/update/video>
- China advertising law and official government publication portal:
  <https://www.gov.cn/zhengce/content/2015-04/25/content_9605.htm>
- FTC advertising and endorsement guidance for substantiation and disclosures:
  <https://www.ftc.gov/business-guidance/advertising-marketing/endorsements-influencers-reviews>

Search retrieval was unavailable on 2026-08-28; links above are source pointers,
not claims that a current platform limit has been independently re-verified in
this session.

**Local expert assumptions** are marked with date and confidence. They are
starting points for experiments, not facts:

- 2026-08-28, medium confidence: mobile-first commerce viewers need the
  garment or a concrete fit/material proof visible immediately, while platform
  overlays make edge text unreliable.
- 2026-08-28, medium confidence: Chinese-market variants benefit from concise
  Mandarin captions and trust cues; global variants need localized language,
  sizing conventions, currency/returns wording, and claims review rather than
  literal translation.
- 2026-08-28, low confidence: 0.5-1.5 second opening changes will materially
  affect early retention; test rather than encode as a universal rule.

## Creative requirements

### First 1-2 second hook

Every short asset MUST have a readable first-frame or first-motion hook that
states a product problem or proof action without a body insult or medical claim.
Allowed structures include: close-up of seam/stretch recovery, controlled
before/after garment styling without implying body transformation, a fit detail
question, or a clear product promise limited to documented attributes.

Requirements:

- Show the garment or its relevant construction by 2.0 seconds.
- Keep opening copy to one idea; localize Mandarin/English and preserve
  sufficient contrast and duration for reading.
- Do not use shock exposure, sexualized posing, minors, or “fix your body”
  framing as an attention device.

### Product proof: fit, construction, and material

The timeline MUST include at least one proof beat and one inspectable detail beat
when source assets permit:

- fit: adult model fully covered in normal fashion-ad posture, with front/side/
  movement evidence where available;
- construction: seams, straps, gusset, compression zones, closures, or edge
  finishing shown at a stable close-up;
- material: texture, stretch, opacity, recovery, or care-relevant detail shown
  only when actually present in the source asset.

The Skill MUST distinguish “shown in source footage” from “inferred by the
planner.” It MUST NOT claim compression level, slimming, health, posture,
pain relief, or weight loss unless the operator has separately supplied and
approved substantiation and the destination permits the wording.

### Shot rhythm

The harness should expose shot duration and beat labels in the plan. Starting
hypothesis for 6-15 second edits:

- hook: 0-2 seconds;
- proof/demo: 2-7 seconds;
- material/detail: 7-11 seconds;
- CTA/trust: final 2-4 seconds, with overlap only if the visual remains legible.

No more than four beats in ten seconds is an existing repository rule. Cuts MUST
not be so rapid that garment identity or proof cannot be inspected. A plan using
repeated near-identical shots MUST warn rather than claim variety.

### 9:16 safe zones

Deliver a 9:16 master for vertical placements. Keep critical garment features,
faces, captions, price, size, and CTA inside a configurable safe rectangle, with
platform-specific overlay margins applied at export. The exact margins MUST be
configuration, not hard-coded creative truth; the operator verifies current
TikTok, Reels, and local-placement guidance before release.

Acceptance smoke checks:

- no essential text or product proof is clipped at 9:16;
- no CTA collides with bottom navigation, caption, or commerce controls in the
  target placement preview;
- square/landscape adaptations never crop the primary proof without a revised
  composition or an explicit warning.

### CTA and trust signals

CTA language MUST be a clear next action: view details, choose size, check fit
notes, shop the collection, or read care information. Trust signals MAY include
real size chart, fabric composition, care instructions, shipping/returns policy,
review disclosure, and seller identity where supplied and approved.

Trust content MUST be factual, current, and localized. Reviews/testimonials
must not be invented or presented as independent when they are sponsored. Do
not use copied competitor/brand claims or imply affiliation with SKIMS or any
other brand.

## Batch variant matrix

A batch request SHOULD vary one controlled dimension at a time. Minimum MVP
matrix for one product and one market:

| Variant axis | A | B | Measurement purpose |
| --- | --- | --- | --- |
| Hook | seam/material proof | fit/movement question | compare early hold and 2-second retention |
| Proof order | fit then detail | detail then fit | test comprehension and completion |
| Rhythm | 3-4 deliberate beats | 2-3 longer beats | test completion without hiding product |
| Presenter | studio adult model | approved UGC-style adult model | test trust signal, holding product constant |
| CTA | shop/check size | read fit/care details | test action intent without changing offer |
| Language | Mandarin | localized English/other approved locale | test market comprehension and localization |

Each variant record MUST include product/asset IDs, market, language, duration,
format, hook ID, proof beats, CTA, claim-review status, and a deterministic
variant ID. Keep product, price, offer, audience definition, and attribution
window constant within a test cell unless that factor is the intended variable.

## Remix rules

Remixing is non-destructive and operates only on selected managed assets:

- preserve source files byte-for-byte; write a new output asset;
- preserve approved garment identity, color, construction, and claim text;
- reorder or trim only within source bounds and the accepted plan;
- do not repeat the same source shot consecutively unless explicitly justified;
- keep at least one proof beat and one detail beat when available;
- do not create a new claim by concatenating unrelated shots or captions;
- if a source lacks a needed proof, mark `missing_proof` for manual review;
- local fallback uses deterministic selected order and hard cuts; it must not
  pretend to have inspected pixels, semantics, or audio.

Any request to retouch, reshape, sexualize, replace, or mutate a source asset is
outside remix and routes to a separately governed generation/editing workflow.

## Platform adaptation

The workflow produces a canonical 9:16 master and platform variants through
explicit configuration:

- TikTok: validate current duration, resolution, text, music, commerce, and
  advertising policies for the account and market.
- Instagram Reels: preserve vertical composition, then validate current Reels
  placement specs and overlay behavior.
- Chinese placements: create separately reviewed Mandarin copy and validate the
  destination platform’s ad, health, beauty, sexual-content, and commerce rules;
  do not assume one rule set covers Douyin, Xiaohongshu, Taobao, or other media.
- Global placements: localize language, units, sizing, currency, returns, and
  required disclosures; do not simply translate regulated claims.

The harness MUST record target platform, market, locale, aspect ratio, duration,
caption/CTA version, and policy-review status with each output. Unsupported
adaptations fail with an actionable warning instead of silently cropping.

## Metrics and experiment design

Metrics are evaluation signals, not guarantees. At minimum collect or manually
record, where the destination provides them:

- 2-second view/hold rate and 3-second view rate;
- average watch time, completion rate, and quartile retention;
- proof-beat view-through or timestamped drop-off;
- CTA click-through rate and qualified product-page visits;
- add-to-cart and purchase rate only when attribution, denominator, and window
  are defined; never report causality from raw counts alone;
- complaint, hide/report, refund, and policy-rejection rates as guardrails.

Experiment protocol:

1. Pre-register the primary metric, guardrails, hypothesis, market, audience,
   placement, attribution window, sample allocation, and stopping rule.
2. Randomize or otherwise balance variants within the same product/offer cell.
3. Change one principal creative axis per comparison; retain a control when
   feasible.
4. Do not declare a winner from an early spike or overlapping confidence
   intervals without the agreed analysis; report sample size, missing data, and
   execution differences.
5. Archive rendered asset ID, variant ID, plan hash, timestamps, and platform
   export settings so results map to the exact creative.

## Measurable creative hypotheses

These are hypotheses for controlled testing, not promises:

- **H1 hook**: showing garment construction or a concrete fit action within 2.0
  seconds increases 2-second hold versus a beauty-only opening, without raising
  complaint or policy-rejection rate.
- **H2 proof**: a fit demonstration followed by a material close-up improves
  completion and qualified product-page visits versus two lifestyle shots, with
  price/offer held constant.
- **H3 rhythm**: three or four readable beats outperform rapid cutting on
  completion and proof-beat retention for 10-second assets.
- **H4 trust**: an accurate size-chart/care/returns cue improves qualified CTA
  rate or reduces refund/complaint guardrails versus a generic “shop now” cue.
- **H5 localization**: market-native copy and sizing conventions improve hold or
  qualified visits versus literal translation, while policy rejection remains
  non-inferior.
- **H6 remix variety**: alternating distinct approved assets before repetition
  improves retention versus consecutive near-duplicate shots, without reducing
  product-detail exposure.

The local MVP can test timing, order, asset type, text presence, and recorded
outcomes. It cannot infer visual quality, semantic relevance, or audio/beat fit
from media pixels or sound without an approved analyzer.

## Compliance limits and release gate

The Skill and harness MUST reject or route to manual review for:

- medical, therapeutic, weight-loss, guaranteed slimming, body-fixing, or
  unsupported compression/posture claims;
- absolute or unverifiable superiority claims, copied brand language, fake
  reviews, undisclosed endorsements, or invented certifications;
- body shaming, discriminatory targeting, sexualized exposure, fetish framing,
  explicit sexual content, nudity, or unsafe depiction of minors;
- imagery that makes anatomy, garment construction, or fit materially
  misleading through generation artifacts or aggressive retouching;
- missing substantiation, missing seller/offer disclosures, or unreviewed local
  language claims.

All models must be adults, fully covered, and posed as ordinary fashion
advertising. Compliance status is evidence metadata, not a claim that a platform
approved the asset. Human review remains required for semantic safety, claim
substantiation, localization, and current platform-policy interpretation.

## Acceptance criteria

- [ ] The requirements artifact separates repository-observed facts, external
  source pointers, dated local assumptions, and hypotheses.
- [ ] Every generated short-video plan contains a first-1-2-second hook, a
  product proof/detail opportunity, readable shot timing, a 9:16 safe-zone
  configuration, and a factual CTA/trust-signal status.
- [ ] A batch can produce controlled variants across hook, proof order, rhythm,
  presenter, CTA, and locale with stable variant IDs and unchanged test-cell
  factors.
- [ ] Remix outputs are new managed assets; source files are byte-for-byte
  unchanged; missing or unverifiable proof is marked for manual review.
- [ ] Platform adaptations record platform, market, locale, export settings, and
  policy-review status, and never silently crop critical content.
- [ ] Experiments define a primary metric, guardrails, sample/stopping rules,
  attribution window, and exact creative/plan evidence before launch.
- [ ] No workflow promises traffic, conversion, or platform approval; claims are
  substantiated, non-medical, non-body-shaming, non-infringing, adult, fully
  covered, and non-explicit.
- [ ] Current MVP does not claim pixel, semantic, speech, music, or audio
  understanding; unverifiable checks remain `manual_review`.
