# Lab: painting textures on generated meshes (2026-10-21)

The question: the factory makes meshes with TRELLIS.2 and paints them with Hunyuan3D Paint 2.1
(10 steps, 768 views, the piece's texture size). The painted models come out soft,
low-contrast and darker than the sharp concept they were made from. Is there a better way to
paint them with the local models and tools of this machine, and which way suits which kind of
object?

Everything was measured on one machine (one 24 GB card, ComfyUI Desktop with its own nodes,
Blender as a Python module), on copies of a real game's factory outputs. Those pictures and
meshes are not in this repository. The raw outputs, renders and sheets are under
`local/labs/texture-painting/` (ignored by git). Nothing was installed for the lab.

## Test set

Seven pieces of a real night, each with its concept cutout (the picture the mesh and paint
were made from), its TRELLIS.2 mesh (`game_mesh`, `raw_mesh`) and its production paint:

| Object | Recipe | Concept | Mesh |
|---|---|---|---|
| long weapon (assault rifle) | held_item | strict side view | 12000 triangles, clean |
| long projectile (rocket) | held_item | strict side view | 6000, clean |
| small device (handheld scanner) | held_item | strict side view | 6000, broken into shards |
| round item (hand grenade) | held_item | strict side view | 6000, clean |
| organic alien item (an ammunition clip) | held_item | strict side view | 6000, thin and broken |
| tracked vehicle (light tank) | articulated | 3/4 view from above | 30000, clean |
| armoured humanoid (a soldier) | humanoid | front A-pose (after the pose edit) | 25000, clean |

## Candidates

| Id | Candidate | Settings |
|---|---|---|
| H0 | Hunyuan3D Paint 2.1 as in production | 768 views, 10 steps, texture 1024 or 2048 (the piece's), cameras 0/90/180/270/top/bottom with weights 1, 0.5, 1, 0.5, 1, 1 |
| H1 | Hunyuan3D Paint at higher settings | 1024 views, texture 4096, 10 steps |
| B0, B0u | H1's views baked again through `Hy3D21UseMultiViews` (the bake path alone), as they are or enlarged to 2048 (Lanczos) | texture 4096 |
| C0 | H1's views brought to the concept's colours (mean and spread of L*, a*, b*), enlarged | |
| C1, C3, C4 | concept projection: the concept laid on the view that shows what it shows (silhouette fit, no mirror), the views enlarged to 2048 and baked by Hunyuan's own baker | C1: colour match, concept weight 2, everywhere; C3: colour match, only where the surface faces the concept's camera (fully within about 40 degrees, fading out by 55), side views weight 1; C4: as C3 without the colour match |
| L3, L4 | C3 and C4 from the production 768 views | |
| P4 | L4 as the factory option `hunyuan_concept` implements it, end to end on the server (`hunyuan21_paint_views.json`, `forge/paint_views.py`, `hunyuan21_bake_views.json`) | the option's defaults |
| S1, SC | detail pass: each H1 view through Qwen-Image 2.1 Turbo img2img at 2048 (sigmas 0.45, 0.2, 0), colour match; SC adds the concept as C1 | |
| E1, EC | multi-view repaint: each H1 view through Qwen-Image-Edit 2511 (Lightning, 4 steps) with the concept as the second picture, the "material replacement" prompt structure of ComfyUI's template; a view whose silhouette moved (IoU below 0.93) is dropped | EC adds the concept as C1 |
| A1 | new views of the concept from Qwen-Image-Edit 2511 with the Multiple Angles LoRA (back and both ends), fitted to the views by silhouette and projected, plus the concept as C1 | rifle, grenade, rocket, humanoid |
| F1 | as E1 with the template's own settings (the fp8mixed model, no Lightning, 20 steps, CFG 4) | rifle, grenade, humanoid |
| T0 | TRELLIS.2's own texture as the production workflow bakes it (texture sampler 12 steps, `BakeTextureFromVoxel` 2048 from the dense mesh, `UnwrapMesh` 2048), the variant's seed again | |
| T1 | T0 with 24 texture steps, bake and unwrap 4096 | |
| T2 | the settings of ComfyUI's "Pixal3D & TRELLIS.2: Image to Model" template: upsample to 1536, bake and unwrap 4096 (the triangle budget kept) | |
| TV | the template's `PaintMesh` path: TRELLIS.2's voxel colours as vertex colours of the reduced mesh | |
| TPt | TRELLIS.2's shape with the texture sampled by Pixal3D (`pixal3d_int8_convrot`, `Pixal3DConditioning`, the field of view from MoGe-2) | |
| R1, R1H | a cleaner mesh: the remesh as a signed field with QEF sharp features and a QEM reduction weighting feature edges; R1 with TRELLIS.2's texture, R1H painted by Hunyuan (H1 settings) | |
| M4 | P4 on T2's mesh (TRELLIS.2 at 1536) | |

TRELLIS.2 run again with the variant's seed does not give the production mesh bit for bit:
the shapes agree to about 1 percent of their size (GPU non-determinism), and T2's higher
resolution makes a new mesh. The T and R rows therefore also carry a slightly different shape.

## Measures

Every candidate was rendered in Blender by the same script: the same orthographic cameras
(the concept's view, its opposite, eight azimuths at 30 degrees), the same three lights, the
same "Standard" view transform, and every mesh shaded smooth by angle (35 degrees), so that the
paint is judged alone (the production GLBs have no normals: see "Normals" below). Each view was
rendered lit and unlit (the base colour texture as emission).

- **Detail**: the fine-detail energy of the unlit render (mean absolute difference between L*
  and L* blurred with sigma 1.5 px, inside the eroded silhouette), divided by the concept's in
  the concept's view, both cropped to the object and brought to 512 px wide ("close"); and the
  same at game size (the object 96 px long, the vehicle 256, the humanoid 128). 100 percent
  means as much fine detail as the concept.
- **Colour (dE)**: CIEDE2000 between the unlit render and the concept in the concept's view,
  both blurred (sigma 8 px at 512: regional colour, so that the small differences of shape
  between mesh and concept do not count), mean over the overlap. dL: mean lightness difference
  (negative: darker than the concept).
- **Seams**: in UV space, the CIEDE2000 between the two sides of each UV seam (sampled 2 texels
  inside each triangle at three points of the edge), weighted by edge length; a chart's inside
  edges give about 0.7 to 1.5 by the same measure.
- **Views dE**: the consistency of the six views a view-based candidate bakes: the mean
  CIEDE2000 between two views' colours of the same surface point (matched through Hunyuan's
  position maps), over pairs of neighbouring views.
- **GPU seconds**: ComfyUI's execution time of the jobs a candidate needs (the views, the
  passes, the bake), not counting the shape; local steps run on the CPU (the view step of P4
  takes about 25 s).
- **Failures**: runs that did not finish, and views or fits the candidate had to drop.

## Results

Per object, the main candidates (every candidate: `local/labs/texture-painting/metrics/table.json`):

| Object | Candidate | Detail close | Detail game | dE | dL | Seam dE | Views dE | GPU s |
|---|---|---|---|---|---|---|---|---|
| long weapon | H0 | 24% | 44% | 7.5 | -3.0 | 5.1 | 4.5 | 108 |
| long weapon | H1 | 23% | 43% | 5.9 | +0.9 | 6.5 | 7.1 | 309 |
| long weapon | T2 | 45% | 48% | 6.6 | +2.7 | 1.6 | - | 51 |
| long weapon | TPt | 60% | 108% | 11.0 | +8.6 | 3.4 | - | 69 |
| long weapon | S1 | 41% | 67% | 7.2 | +1.1 | 8.3 | 10.9 | 411 |
| long weapon | EC | 93% | 91% | 3.5 | +1.8 | 9.2 | 11.5 | 506 |
| long weapon | A1 | 93% | 91% | 3.5 | +1.7 | 9.5 | 11.8 | 389 |
| long weapon | FC | 109% | 106% | 4.1 | +3.1 | 14.0 | 19.0 | 1895 |
| long weapon | P4 | 83% | 81% | 3.3 | +0.6 | 5.1 | 5.3 | 106 |
| long weapon | M4 | 80% | 84% | 3.5 | +0.3 | 5.7 | - | 131 |
| long projectile | H0 | 33% | 36% | 16.0 | -13.9 | 5.2 | 4.8 | 88 |
| long projectile | H1 | 31% | 42% | 16.3 | -11.1 | 5.6 | 6.0 | 247 |
| long projectile | T2 | 30% | 22% | 14.8 | -11.7 | 1.3 | - | 39 |
| long projectile | TPt | 70% | 57% | 13.0 | -3.3 | 2.6 | - | 61 |
| long projectile | EC | 104% | 94% | 2.8 | +4.1 | 15.5 | 21.3 | 411 |
| long projectile | A1 | 95% | 88% | 3.0 | +1.8 | 10.5 | 13.1 | 330 |
| long projectile | P4 | 96% | 82% | 4.4 | -1.8 | 7.0 | 6.7 | 113 |
| round item | H0 | 16% | 33% | 11.1 | -6.1 | 5.6 | 2.6 | 93 |
| round item | H1 | 17% | 28% | 11.9 | -7.9 | 5.4 | 3.5 | 246 |
| round item | T2 | 25% | 36% | 10.9 | -5.6 | 2.6 | - | 178 |
| round item | TPt | 36% | 78% | 18.9 | +15.1 | 5.4 | - | 140 |
| round item | S1 | 34% | 53% | 11.5 | +0.0 | 9.0 | 6.0 | 341 |
| round item | EC | 69% | 100% | 4.8 | +3.4 | 10.8 | 15.7 | 426 |
| round item | A1 | 67% | 96% | 4.1 | +0.5 | 10.6 | 13.5 | 320 |
| round item | FC | 67% | 97% | 4.8 | +3.1 | 10.4 | 15.6 | 2114 |
| round item | P4 | 57% | 90% | 5.1 | -1.5 | 6.7 | 4.1 | 105 |
| armoured humanoid | H0 | 28% | 46% | 10.4 | -1.0 | 9.5 | 6.0 | 82 |
| armoured humanoid | H1 | 30% | 49% | 11.1 | -3.8 | 8.1 | 5.7 | 263 |
| armoured humanoid | T2 | 30% | 44% | 10.6 | +7.6 | 2.7 | - | 77 |
| armoured humanoid | TPt | 26% | 39% | 17.0 | -13.7 | 3.2 | - | 88 |
| armoured humanoid | EC | 67% | 89% | 3.5 | +0.7 | 9.8 | 11.6 | 422 |
| armoured humanoid | A1 | 67% | 89% | 3.5 | +0.6 | 10.6 | 15.0 | 351 |
| armoured humanoid | FC | 69% | 92% | 3.6 | +0.8 | 10.4 | 14.6 | 2305 |
| armoured humanoid | P4 | 59% | 80% | 4.6 | +0.5 | 8.7 | 7.0 | 112 |
| small device | H0 | 15% | 23% | 13.1 | +4.5 | 4.6 | 4.2 | 98 |
| small device | H1 | 11% | 19% | 14.5 | +8.3 | 2.8 | 3.3 | 274 |
| small device | T2 | 50% | 70% | 13.3 | -12.0 | 3.0 | - | 451 |
| small device | S1 | 23% | 33% | 14.2 | +4.9 | 4.1 | 5.0 | 373 |
| small device | EC | 67% | 92% | 20.1 | -9.1 | 11.5 | 17.5 | 490 |
| small device | M4 | 62% | 80% | 4.0 | +0.7 | 5.3 | - | 123 |
| organic alien item | H0 | 7% | 13% | 26.2 | -17.0 | 8.8 | 9.0 | 86 |
| organic alien item | H1 | 9% | 17% | 24.6 | -13.1 | 5.9 | 5.8 | 296 |
| organic alien item | T2 | 48% | 74% | 23.5 | -7.8 | 2.0 | - | 58 |
| organic alien item | S1 | 20% | 32% | 27.9 | +0.4 | 9.3 | 9.8 | 452 |
| organic alien item | M4 | 86% | 95% | 6.3 | -1.7 | 17.1 | - | 108 |
| tracked vehicle | H0 | 16% | 22% | 10.3 | -6.4 | 6.8 | 4.6 | 91 |
| tracked vehicle | H1 | 13% | 17% | 10.0 | -6.1 | 6.0 | 3.6 | 269 |
| tracked vehicle | T2 | 29% | 37% | 8.6 | -1.7 | 2.9 | - | 209 |
| tracked vehicle | S1 | 27% | 35% | 8.0 | -1.0 | 10.1 | 6.5 | 377 |
| tracked vehicle | M4 | 22% | 28% | 8.7 | -5.4 | 7.2 | - | 131 |

GPU s: ComfyUI execution seconds per variant, the shape not counted except in T2 and TPt (one
TRELLIS.2 job makes shape and texture); M4's are P4's on T2's mesh (T2's own time not added).
The long weapon's H1 is its views baked again (B0): its H1 run lost the bake to a mesh-name
error after making the views. The mesh-repair rows (R1, R1H), the colour-matched variants (C0
to C3, L3) and the vertex colours (TV) are in `table.json`.

Sheets (`local/labs/texture-painting/sheets/`, one per test object, not committed): the concept, the
concept's view, two 3/4 views, a close crop of the texture alone, and both at game size, one
row per candidate, the same camera and light in every row.

What the measures and the sheets show:

1. **Higher Paint settings do nothing.** H1 (1024 views, 4096 texture) keeps the same detail
   as H0 (23 against 24 percent on the long weapon, 30 against 28 on the humanoid) at three times the
   GPU time (250 to 300 s against 82 to 108). The Paint model's views are the limit, not their
   size or the texture's. 30 steps were not run after this.
2. **The concept carries the detail, and it can be put back.** The production views keep 7 to
   33 percent of the concept's fine detail and come out darker (dL down to -17). Laying the
   concept on the view that shows it (C, L, P rows) brings the rifle to 83 percent, the rocket to
   96, the grenade to 57 (90 at game size) and the humanoid to 60 (80 at game size), and halves
   the colour difference or better (rifle 7.5 to 3.3, rocket 16.0 to 4.4, grenade 11.1 to 5.1,
   humanoid 10.4 to 4.6). It needs a concept that is an orthographic picture of the mesh (a held
   item's side view, a figure's front): the 3/4 vehicle concept fits no view (IoU 0.66) and is
   left out, as the broken alien item's (0.79).
3. **Limit the concept to where it faces its camera, and do not match colours.** Laid
   everywhere (C1) the concept smears along surfaces seen at a grazing angle (round objects) and
   doubles markings where the other views paint them elsewhere; the colour match bleached
   saturated accents (the rocket's orange) and made the views disagree more (rifle 10.1 against
   8.0 views dE). The facing limit without colour match (C4, L4) keeps the seams at the
   production level on the rifle (5.1) and close elsewhere (6.7 to 8.7 against 5.2 to 9.5).
4. **768 views are enough.** L4 (the production views) measures as C4 (1024 views) within the
   noise; P4, the implementation, reproduces L4 to 1 percent. The option costs 105 to 115 GPU
   seconds per variant (views 70 to 74 s, bake 32 to 42 s) against 82 to 108 for production, plus
   about 25 s on the CPU.
5. **The Qwen passes do not help.** The Turbo detail pass (S1) adds texture-like noise without
   the concept's detail and needs the colour match; the two-picture edit (E1) mostly redraws the
   concept instead of repainting the view: 34 of 42 views moved too far and were dropped (the
   rifle's end views became a whole rifle seen from the side), and the views it kept invent
   detail that disagrees with the other views (views dE 15 to 20 against 4 to 7). The
   template's settings (F1) behave the same (below). The multiple-angles views (A1) register
   only in part and disagree with the rest (below).
6. **TRELLIS.2's own texture is cleaner, rarely sharper.** T0 and T2 have far fewer seams (1.3
   to 3.7: one continuous voxel field baked on `UnwrapMesh`'s charts) and no lightness drop on
   most objects, but no more detail than H0 on the rifle, rocket, grenade and humanoid, and they
   lose small markings and accent colours (chroma down to 0.3 to 0.6 of the concept's on the
   alien item, the scanner and the rifle). On the vehicle, whose 3/4 concept the projection
   cannot use, T2 is the best candidate (29 percent detail at close range against 16, colour 8.6
   against 10.3, seams 2.9 against 6.8). Pixal3D sampling the texture (TPt) is sharper still but
   its colours are wrong (silver and white patches, colour 11 to 19); vertex colours (TV) are
   the softest of all.
7. **The mesh decides more than the paint on thin and broken objects.** At TRELLIS.2's 1024
   upsample the scanner and the alien item come out as shards, and no paint repairs them. At
   1536 (T2's mesh) the alien item becomes a smooth closed shape whose silhouette fits its
   concept (IoU 0.96), and the concept projection on that mesh (M4) gives it 86 percent of the
   concept's detail and a colour difference of 6.3 against 26.2; its seams are high (17: the
   glowing core is painted by the concept on one side and by Hunyuan on the other). The
   scanner stays broken at 1536, but its silhouette fits (0.90) and M4 still brings its colour
   from 13.1 to 4.0. The sharper remesh (R1: signed field, QEF, feature-weighted reduction)
   gives clean edges but no sharper paint: R1H (Hunyuan on R1's mesh) keeps H1's detail or
   less on every object.

## The other candidates

- **A1, multiple-angles views.** The LoRA's back view fitted the matching Hunyuan view on all
  four objects tried (IoU 0.89 to 0.95), its end views only on the round item and the humanoid
  (0.81 to 0.94; on the long objects 0.34 to 0.67, dropped). With the concept laid as C1 they
  measure as C1 (detail 67 to 95 percent, colour 3.0 to 4.1), but the generated sides disagree
  with the rest (views dE 12 to 15 against 4 to 7 for P4, seams 9.5 to 10.6) and invent detail
  (the humanoid's back plate). They add about 45 s of GPU per object.
- **F1, the material-replacement template's own settings** (fp8mixed, 20 steps, CFG 4): more
  views keep their silhouette than with Lightning (8 of 18 against 5 of 18 on the same three
  objects), but each view takes 240 to 450 s, and the views invent materials (a violet back
  plate on the humanoid, red stripes on the grenade): with the concept laid (FC) seams 10 to 14
  and views dE 15 to 19, at 1900 to 2300 GPU seconds per object.
- **TPt, Pixal3D texture.** The sharpest of the TRELLIS.2 textures (the long weapon above the
  concept at game size), but with wrong colours on every object (silver and white patches, the
  humanoid's chroma at a third of the concept's).
- **TV, vertex colours.** The softest candidate (the long weapon 16 percent, the humanoid 13).
- **T1** (24 texture steps, 4096) adds little to T0 (the long weapon 29 against 25 percent).

## Winner per kind and recommended defaults

| Kind | Winner | Recommended |
|---|---|---|
| held items (side-view concept): long weapons, projectiles, round items | concept projection (P4) | paint `hunyuan_concept` |
| humanoids (front concept; creatures with a front concept were not in the test set) | concept projection (P4) | paint `hunyuan_concept` |
| thin or organic items whose mesh breaks at 1024 | a better mesh first: TRELLIS.2 at 1536, then the concept projection (M4) | mesh setting `resolution` 1536 and paint `hunyuan_concept` |
| objects and vehicles with a 3/4 concept (object_3d, prop, vehicle, articulated machines) | TRELLIS.2's own texture at the template's settings (T2), or the production paint | mesh `resolution` 1536, paint `trellis2_texture`, `params.texture` 4096 where the recipe offers it; `hunyuan_concept` falls back to the production views there |

`hunyuan_concept` is a new option of the `paint` stage of every recipe that has one (`humanoid`,
`articulated`, `creature`, `held_item`, and now `object_3d`, `prop` and `vehicle`, whose default
paint stays inside the mesh job); its jobs and settings are in [order.md](../order.md#the-paint-option-hunyuan_concept).
It is not the default: it wins only where the concept is an orthographic view of the mesh, and
falls back to the production views (enlarged) elsewhere. Its own defaults are the measured best
(768 views, the concept only where the surface faces its camera, no colour match, views
enlarged to 2048, `min_fit` 0.88).

## Normals

The painted GLBs of Hunyuan3D Paint carry no `NORMAL` attribute, so a viewer shades them flat,
and the held-item and parts exporters wrote 100 percent flat normals (every vertex normal equal
to its triangle's): the faceted look of small items is the normals, not the paint. The lab
rendered every candidate smooth by angle so that the paint is judged alone. The fix belongs to
the exporters, not to the paint stage.

## Cost

GPU seconds per variant, from ComfyUI's execution times on this machine:

| Candidate | GPU s per variant |
|---|---|
| production paint (H0) | 82 to 108 |
| H1 (1024 views, 4096) | 246 to 296 |
| `hunyuan_concept` (P4) | 105 to 115 (+ about 25 s CPU) |
| detail or edit passes on six views (S1, E1, Lightning) | H1 + 60 to 150 |
| the template's edit settings on six views (F1) | H1 + 1500 to 2000 |
| TRELLIS.2 mesh job with its texture (T0) | 49 to 170 (the shape included) |
| T2 (1536, 4096) | 39 to 451 (the shape included) |

## Licences of the models used

As stated on each model's page (read 2026-10-21); the text on the page is what counts.

| Model | Used by | Licence |
|---|---|---|
| Hunyuan3D 2.1 (Paint) | H, B, C, L, P, S, E, A, F, M, R1H | Tencent Hunyuan Community License (territory excludes the EU, the UK and South Korea) |
| TRELLIS.2 | every mesh; T, R | MIT |
| Pixal3D | TPt | MIT |
| MoGe-2 | TPt (field of view) | MIT |
| Qwen-Image-Edit-2511 (int8 and fp8mixed) | E, F, A | Apache 2.0 |
| Qwen-Image-Edit-2511 Lightning LoRA | E, A | Apache 2.0 |
| Qwen-Image-Edit-2511 Multiple Angles LoRA | A | Apache 2.0 |
| Qwen-Image 2.1 Turbo | S | Qwen Research License (non-commercial) |
| BiRefNet | the cutouts, the edit's mask | MIT |

The option the lab adds uses Hunyuan3D 2.1 only, as the production paint does.

## Not tested

- Painting in UV space (an inpaint pass on the atlas): the atlas of every candidate is cut into
  many small charts, and a pass over it would cross their borders; not run.
- 30 Paint steps (H2): H1 showed that the views' size and the texture's change nothing; steps
  were left out to keep the card for the other candidates.
- More cameras (eight azimuths): Hunyuan3D Paint's model generates its views together; a
  different camera set was not tried.
- An upscaling model on the views (the original Hunyuan3D 2.1 pipeline enlarges its views with
  RealESRGAN before the bake; no such model is installed on this machine, and none was
  installed).
- A second seed per candidate: the Paint stage's noise is fixed by the node (seed 0), and the
  TRELLIS.2 runs used the variant's seed only.
- The concept's lighting: the projected concept carries its shading and highlights into the
  albedo. In these renders it reads as detail; under a game's own lights it may double some
  shading. Not measured.
- Creatures and figures (no piece of those recipes in the test set), and a game engine render
  (the comparison is Blender only).
