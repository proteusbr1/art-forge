# Starter motion library

`starter_humanoid.json` is a motion library in the factory's format (`forge-motion-library`,
the canonical joints of [docs/conventions.md](../../docs/conventions.md#motion-library-inputsclips)),
shipped so that a humanoid can walk, stand, aim, kneel, throw and fall without generating any
motion. A motions entry names it as its library file `@starter`
([docs/order.md](../../docs/order.md#the-humanoid-recipe)); it can also be copied and given as
an order's `inputs.clips`.

## Clips

45 clips at 20 or 24 frames per second. Hold kinds are generic item classes: `rifle`, `pistol`,
`heavy`, `launcher`, `device` (with `_aim` for the aiming poses) and `hand` (an item in the
right hand: a thrown object, a melee weapon).

| Clips | What | Source |
|---|---|---|
| `idle_rifle`, `idle_pistol`, `idle_heavy`, `idle_launcher` | weight shifting, at rest | MoMask sample 4, frames 99-139 |
| `idle_none`, `idle_thrown`, `idle_melee` | standing at rest, arms down | MoMask sample 0, frames 0-14 |
| `idle_device`, `aim_device` | held in front, looking at it; raised in front | MoMask sample 4 |
| `aim_rifle`, `aim_heavy` | shouldered, leaning into the aim | MoMask sample 1, frames 68-78 |
| `aim_pistol` | two hands, arms out | MoMask sample 2, frames 91-101 |
| `aim_launcher` | the tube on the shoulder | MoMask sample 5, frames 55-63 |
| `aim_thrown`, `throw` | an overhand throw (with and without the item) | MoMask sample 0, frames 90-120 |
| `aim_melee` | a downward blow | MoMask sample 1, frames 70-92 |
| `kneel_<class>`, `kneel_none` | on one knee | MoMask sample 0, frames 70-90 |
| `kneel_aim_<class>` | on one knee, aiming | MoMask sample 6, frames 58-68 |
| `collapse` | hit, falling, lying on the back (`feet` `lie`) | MoMask sample 2, frames 30-87 |
| `walk_<class>`, `walk_none` | one gait cycle in 24 frames, travel 0.763 body heights a cycle | CMU motion capture 07_01, cycle frames 55-186 |
| `walk_<class>_diag`, `walk_none_diag` | the same cycle with a longer stride, travel 1.079 body heights a cycle | the same |

Every clip's `source` says where it came from. The walks of every class have the same frames
(the exporter puts the item in the hands by IK).

## How the file was made

It is the motion library of an earlier private project of the same author, made from the two
sources below, with its frames unchanged. For this repository:

- the free-text description of each clip (a key the factory does not read) was dropped and
  folded into `source` in generic words (the original texts named the earlier project's game
  units); the `source` strings keep the dataset references;
- the walks got `kind` `walk`, `travel` (the forward distance per cycle, measured from the
  planted foot's backward step) and `stance` (the frames where each foot is planted: 13 of 24,
  measured from the same steps). No other clip has `stance` or `travel`.

Known property, measured when the file was made: in the walks the planted foot keeps its
forward place exactly but drifts sideways and sinks at the heel strike, up to 0.057 body
heights within a stance (about 10 cm on a 1.8 m figure). Listed through the motions list
(`source: library`), the stage's foot cleanup holds the planted feet (0.002 to 0.003 body
heights left, measured); given directly as `inputs.clips`, the exporter's motion report
measures that drift as foot slide.

## Sources and terms

- **CMU Graphics Lab Motion Capture Database** (mocap.cs.cmu.edu), trial 07_01, through the
  BVH conversion by B. Hahne ("cgspeed"), as distributed at github.com/Shriinivas/cmubvh, whose
  README states: "CMU places no restrictions on the use of the original dataset" and that the
  converter places "no additional restrictions on the use of this particular BVH conversion".
  The database was created with funding from NSF EIA-0196217. CMU's own terms page could not
  be read when this file was added (its certificate did not verify); check it before relying
  on the statement above.
- **MoMask** text-to-motion (github.com/EricGuo5513/momask-codes), run locally. Its code is
  under the MIT licence. Its models are trained on HumanML3D, which is built from other motion
  datasets (AMASS among them) with their own licences, some for non-commercial research only;
  MoMask's README says those datasets' licences must be followed. Whether such terms bind the
  motions a model generates is not settled, and this note does not decide it: check them
  before using these clips in a commercial product.

This note is not legal advice.
