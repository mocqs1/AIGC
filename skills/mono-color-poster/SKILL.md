---
name: mono-color-poster
description: Independent AIGC Studio workflow for original one-ink or controlled two-ink editorial posters.
---

# Mono-color Poster

This project adapter applies the installed `mono-color` skill to a local image
generation job. It compiles a deterministic five-paragraph prompt from an
approved palette, substrate, layout, carrier, typography role, and ratio.

The workflow supports zero or one optional managed reference image, keeps the
paper substrate visible, uses no more than two assigned inks, and saves output
and `prompt.txt` under `outputs/posters/`. Visual checks remain manual: verify
ink count, empty paper, focal event, typography, recognizable subject, and
originality before publishing.
