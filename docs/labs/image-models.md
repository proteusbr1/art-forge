# Image-model lab (2026-10-21)

Which local image model is best for each kind of 2D picture the factory makes, and which
edit model is best for fixes. Measured on one RTX 3090 (24 GB) with 62 GB of system RAM,
through a local ComfyUI server (v0.41) and its core nodes only. No custom nodes and no API
nodes were involved. The scripts and the sheets stay outside git in
`local/labs/image-models/` (`scripts/lab.py`, `scripts/measure.py`, `sheets/<case>.jpg`,
`metrics.json`, `results.jsonl`).

## Candidates

| Name in the tables | Model and file | Settings (from ComfyUI's own template) | Licence (model page, 2026-10-21) |
|---|---|---|---|
| `klein9b` | FLUX.2 Klein 9B distilled, `flux-2-klein-9b-fp8` | 4 steps, cfg 1 (`klein_t2i.json`) | FLUX Non-Commercial License |
| `klein4b` | FLUX.2 Klein 4B distilled, `flux-2-klein-4b-fp8` + `qwen_3_4b` | 4 steps, cfg 1 | Apache 2.0 |
| `zimage_turbo` | Z-Image-Turbo, `z_image_turbo_int8_convrot` + `qwen_3_4b_fp8_mixed` | 8 steps, cfg 1, res_multistep, shift 3 | Apache 2.0 |
| `krea2_turbo` | Krea 2 Turbo, `krea2_turbo_int8_convrot` + `qwen3vl_4b_fp8_scaled` | 8 steps, cfg 1, euler | Krea 2 Community License: commercial use only below US$1M yearly revenue, content filtering required, an enterprise licence above |
| `qwen21_turbo` | Qwen Image 2.1 Turbo, int8 | 8 sigmas, cfg 1 (`qwen_image_21_turbo_tile.json` graph) | Qwen Research License: non-commercial |
| `qwen21_base` | Qwen Image 2.1, int8 | 60 steps, cfg 1 | Qwen Research License: non-commercial |
| `flux2_dev` | FLUX.2 Dev, `flux2_dev_fp8mixed` + `mistral_3_small_flux2_fp8` | 20 steps, guidance 4 | FLUX [dev] Non-Commercial License (outputs may be used commercially, the model only for non-commercial purposes) |
| `qwen_edit_2511` | Qwen Image Edit 2511, int8, Lightning 4-step LoRA | 4 steps, cfg 1 (`qwen_edit_2511.json` graph) | Apache 2.0 (model and LoRA) |
| `klein9b_edit`, `klein4b_edit` | the Klein models above with the picture as a reference latent | 4 steps, cfg 1, the picture scaled to 1 MP | as above |
| `firered_11` | FireRed Image Edit 1.1 (bf16, 41 GB) with its Lightning 8-step LoRA | 8 steps, cfg 1, the picture scaled to 1 MP | Apache 2.0 |
| `flux2_dev_edit` | FLUX.2 Dev with the picture as a reference latent | 20 steps, guidance 4 | as `flux2_dev` |

FLUX 3 Image Klein (both variants) was not tested. Its ComfyUI repository is gated behind a
Hugging Face login and the FLUX Kommunity License, and the file names in ComfyUI's template
differ from the repository's.

## Cases

Generation: the prompts of a real game order (its brief, technology tier, family style,
global style and view texts), at the order's sizes, 4 seeds each (101, 202, 303, 404;
FLUX.2 Dev 2 seeds, see below):

| Case | What the prompt asks | Size |
|---|---|---|
| weapon | a modern assault rifle, strict side view, muzzle to the right, plain grey background, no hands, no text | 1536x1024 |
| alien | a small slender grey alien with three-fingered hands, front A-pose, whole figure, plain background | 1024x1536 |
| aircraft | a single-seat jet interceptor, three-quarter front view from slightly above, no text, no roundels | 1536x1024 |
| portrait | a soldier in composite body armour, head-and-shoulders portrait, dark background | 1024x1024 |
| illustration | an alien power source on a laboratory table, a finished atmospheric scene | 1536x1024 |
| material | worn grey flagstones with moss in the joints, straight down, seamless, flat light | 1024x1024 |

Edit (fixes), 4 seeds each, on pictures the factory made earlier (1536x1024):

| Case | Picture | Instruction |
|---|---|---|
| letters | a craft concept with painted lettering on its tail fin | remove the letters, keep everything else |
| hand | an alien pistol held by a clawed hand | remove the hand, complete the grip, keep the pistol |
| colour | a hand grenade with small orange marks | make the orange marks royal blue, keep everything else |

## Measures

- Fidelity and cleanliness: judged by eye on the sheets, 0 to 2 each per case over all
  seeds. Fidelity covers the subject, the view and the framing. Cleanliness means no stray
  text, roundels, hands, weapons, extra objects or frames.
- Sharpness: the variance of the Laplacian at 1024 px on the long side (higher is crisper;
  very high on materials also means busy). It is comparable within a case only.
- Consistency: the mean pairwise correlation of 48x48 grey thumbnails over the seeds (1 is
  the same layout every seed; materials are naturally near 0).
- Material seam ratio: the mean difference across the opposite edges over the mean
  difference between neighbouring pixels (1 means it tiles as is; the factory makes every
  material seamless afterwards anyway).
- Edit preservation: the mean absolute difference from the input (0-255) and the share of
  pixels changed by more than 25. Colour: the share of strongly orange and blue pixels.
- Seconds per image: ComfyUI's own execution time, warm (the model already loaded), median
  per model. Cold first images took 10-30 s more (FLUX.2 Dev 266 s).
- VRAM: the card's peak use during the image (`nvidia-smi`). ComfyUI keeps models resident
  and fills the free memory, so every model reads 17-24 GB. That figure says each one runs
  on a 24 GB card, not what it needs.

## Results: generation

Fidelity + cleanliness (0-4 per case, 24 at most):

| Model | weapon | alien | aircraft | portrait | illustration | material | total | s/image |
|---|---|---|---|---|---|---|---|---|
| `klein9b` | 4 | 3.5 | 3.5 | 3.5 | 4 | 3 | **21.5** | 14.6 |
| `flux2_dev` | 4 | 3.5 | 2.5 | 3.5 | 4 | 4 | **21.5** | 153 |
| `krea2_turbo` | 3.5 | 3.5 | 1 | 3.5 | 4 | 3.5 | 19 | 19.0 |
| `qwen21_base` | 3.5 | 4 | 2.5 | 2.5 | 3.5 | 3 | 19 | 57.9 |
| `qwen21_turbo` | 3.5 | 4 | 2.5 | 2 | 3.5 | 3 | 18.5 | 16.2 |
| `klein4b` | 2.5 | 3.5 | 1 | 2 | 3.5 | 4 | 16.5 | 5.2 |
| `zimage_turbo` | 2.5 | 3 | 3.5 | 1.5 | 3 | 3 | 16.5 | 10.2 |

What decided the scores:

- weapon: `klein9b`, `flux2_dev` and both Qwen models drew the asked rifle in a true side
  view. `krea2_turbo` drew the right side view on all seeds but painted the magazine
  blue. `klein4b` turned it to a three-quarter view with a mangled barrel. `zimage_turbo`
  drew a different rifle type with orange furniture.
- alien: the Qwen models gave the cleanest slender A-pose. Every model drew five fingers
  or claws instead of three. `krea2_turbo` hunched the figure with claw hands, and
  `zimage_turbo` changed the head into a crest.
- aircraft: `klein4b` painted lettering and `krea2_turbo` roundels on every seed, and
  `flux2_dev` one roundel. The Qwen models drew a low side view with afterburner glow
  instead of three-quarters from above. `zimage_turbo` gave the asked view and no markings,
  with two of four seeds seen from behind. `klein9b` gave the asked view with tiny marks.
- portrait: no model gave a true head-and-shoulders crop; the brief's boots pulled every
  model towards a longer framing. `klein4b` and `zimage_turbo` drew full figures, and
  `zimage_turbo` added a holstered pistol. The Qwen models painted a letter-like glyph on a
  shoulder (2 of 4 seeds).
- illustration: all good; `zimage_turbo` added a nozzle with rivets that the alien style
  forbids.
- material: `klein4b` and `flux2_dev` gave uneven slabs with moss straight down on every
  seed, and `flux2_dev` tiled best (seam ratio 1.85). The Qwen models drew regular brick
  grids with little moss. One `klein9b` seed and one `zimage_turbo` seed came out in
  perspective. `krea2_turbo` drew dark, regular, stylised slabs.

Consistency over 4 seeds (layout correlation): the Klein models are the most repeatable on
objects (weapon 0.89 `klein9b`, alien 0.91 both Klein models, `krea2_turbo` weapon 0.92).
`zimage_turbo` and `flux2_dev` vary most. Sharpness: the Qwen models are crispest on
illustrations and materials (819-1184 against 336-417 for the others), and `zimage_turbo`
is the softest on characters.

## Results: edits

| Model | letters removed | hand removed | colour changed | letters diff / changed | hand diff / changed | colour diff / changed | colour: orange left, blue made (input 1.44% orange) | s/image |
|---|---|---|---|---|---|---|---|---|
| `klein9b_edit` | 4/4 (2 seeds recoloured the craft beige or white) | 4/4 | 2/4 right size, 2 overpainted bands | 8.2 / 7.8% | 14.3 / 7.6% | 7.4 / 3.9% | 0.84% / 1.96% | 13.9 |
| `klein4b_edit` | 4/4 (2 seeds recoloured) | 4/4 | changed on 4/4, overpainted bands | 9.2 / 8.9% | 11.8 / 7.4% | 7.2 / 5.2% | 0.23% / 3.53% | 7.8 |
| `qwen_edit_2511` | 4/4 (3 seeds redrew the craft's stripes and fins) | 4/4 | 4/4 the marks themselves | 13.2 / 11.2% | 16.0 / 17.5% | 10.5 / 11.6% | 0.19% / 1.27% | 19.3 |
| `flux2_dev_edit` (2 seeds) | 2/2 (1 seed bleached the stripes) | 2/2 | half changed | 7.7 / 5.2% | 15.4 / 9.5% | 2.8 / 0.8% | 0.64% / 0.64% | 278 |
| `firered_11` | redraws the whole picture | 4/4, but the pistol redrawn | redraws, adds frames | 33.6 / 77% | 28.1 / 21% | 34.7 / 65% | 0.37% / 0.77% | 39-155 |

- Removing a hand is solved by every model: the grip comes back whole on every seed.
  FireRed also redraws the pistol.
- The Klein edits keep the rest of the picture best, with the smallest difference from the
  input, but on a large picture they sometimes recolour everything (letters, 2 of 4 seeds)
  or paint more than was asked (colour).
- Qwen Image Edit 2511 changes exactly what was named in the colour case: 0.19% orange left,
  1.27% blue for 1.44% asked. On the craft it redraws more of the design than asked.
- FireRed 1.1 with its 8-step Lightning LoRA repaints the whole picture and adds borders and
  textured backgrounds, with or without scaling to 1 MP. Its 40-step mode was not tried: the
  bf16 transformer (41 GB) streams from system RAM and took 39-155 s per image already at
  8 steps.
- FLUX.2 Dev (35 GB, fp8) runs on 24 GB only by streaming weights from system RAM. One edit
  failed with `hostbuf_file_reader_read failed` when 2.7 GB of RAM were free, and passed when
  run again.

## Verdict per stage

| Stage | Winner | Change made |
|---|---|---|
| concept (objects, vehicles, weapons, illustrations) | `klein9b`, as before. `flux2_dev` ties on quality at ten times the time | none |
| reference (humanoid A-pose) | `qwen21_base` / `qwen21_turbo`, as before | none |
| material (raw textures) | `klein4b`: uneven slabs straight down on 4/4 seeds where the Qwen Turbo default drew regular brick grids, 3.5 s an image, Apache 2.0 | new option `klein4b` of the `material` recipe's new `texture` stage ([order.md](../order.md)); the default stays `qwen21_turbo` (one material case only) |
| edit (fixes) | no clear winner: Klein edits keep the picture best, Qwen Image Edit 2511 follows a named change most exactly | none |

For a game whose art must be usable commercially, the models whose licences allow it are
FLUX.2 Klein 4B, Z-Image-Turbo, Qwen Image Edit 2511 and FireRed (Apache 2.0), and Krea 2
under its revenue limit. The current defaults for concept (Klein 9B) and reference (Qwen
Image 2.1) are non-commercial. Among the commercial-friendly models, `krea2_turbo` scored
best on generation (19, but it paints roundels on aircraft), then `klein4b` and
`zimage_turbo` (16.5 each). Read each model's licence before using its outputs.
