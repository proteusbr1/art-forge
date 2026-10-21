# Conventions of the local tools

The formats the `humanoid` tools read and write: the motion library and the held-item
socket file a game supplies, and what the exporter (`tools/blender/humanoid_export.py`)
and the review sheet (`tools/review_godot/`) produce. The runners are in
`forge/local_gpu.py`; the synthetic examples are in `tests/fixtures/humanoid/`. The
formats of the `articulated`, `creature` and `held_item` tools follow in
[their own sections](#articulated-parts), and the `iso_tile` recipe's sprites in
[Iso tiles](#iso-tiles).

## Axes and units

- **Exported model:** metres, glTF axes (Y up). The figure faces glTF +Z, stands on the
  ground (y = 0) with its hips over the origin, and is `--height` metres tall at rest
  (`model.json` `height`, the height of the rest mesh after any reduction). A rig that faced
  another way is turned about the vertical first, so `model.json` `front` is always
  `[0, 0, 1]`; see [the facing](#the-way-an-exported-figure-faces).
- **Bone directions:** the retarget and the IK turn each mapped bone by the line from its
  joint to the joint of the next mapped bone of its chain, not by the bone's own axis: rig
  tools give joints orientations whose axes need not point to their children (such a rig was
  exported crouching before 2026-10-19). The last bone of a chain (head, hand, toes, a foot
  without toes) keeps its own axis, unless it is more than 45 degrees off its parent's
  segment continued.
- **Body coordinates** (motion library and hold placements): `[side, forward, up]` in
  body heights; side + is the figure's left, the ground is up = 0, the pelvis stands over
  side = forward = 0.
- **Item space** (held items): metres, +x is the item's front (a muzzle, a blade's tip),
  +z its top, +y = z x x (the item's left); the grip socket is the origin. An item GLB holds
  item space as Blender imports it: item x is glTF +x, item z is glTF +y and item y is
  glTF -z (the front along glTF +x, the top up).

## Motion library (`inputs.clips`)

One JSON file. The factory only retargets it; the game decides the timing and the poses.

| Key | Meaning |
|---|---|
| `format`, `version` | optional: `forge-motion-library`, `1` |
| `canon` | optional: the joint names (informative) |
| `anims` | `{clip name: clip}` (required) |
| `holds` | optional: `{hold kind or class: placement}` (below) |

Clip keys:

| Key | Meaning |
|---|---|
| `frames` | list of frames; each `{joint: [side, forward, up]}` with every canonical joint |
| `fps` | frames per second (default 24) |
| `loop` | the clip loops (its last key in the GLB is the first frame again) |
| `kind` | optional: `idle`, `walk`, `start`, `stop` or another word; without it the clip name's prefix is used. The sheet poses the first `idle` and strips `walk`, `start`, `stop` |
| `feet` | how the feet stand: `clip` (planted where the clip puts the ankles, not widened, never below the rest ankle; the lowest ankle is put back on the ground first), `walk` (ankles follow the clip relative to its lowest ankle, from the rest ankle height), `lie` (the lowest joint a little above the ground), `free` (bone directions only) |
| `pelvis_stand` | the clip's standing pelvis height (body heights; default: the first frame's); the hips go down by the clip's pelvis below it |
| `holds` | `null`, or a hold kind `<class>` or `<class>_<mode>` (for example `box`, `rifle_aim`): the class is matched against the held items' `class` |
| `hold` | optional placement for this clip only (wins over the library's `holds`) |
| `stance` | optional: `{"left": frames, "right": frames}` (a list of frame indices or `"all"`): when each foot is planted. The motion report measures foot slide only on these frames |
| `travel` | optional: `[side, forward]` body heights the figure moves over one pass of the clip (a walk in place); the foot slide is measured in the ground's frame |
| `source` | optional: where the motion came from (informative) |
| `pose_frame` | optional: the frame the review sheet's still figures show for this clip (default: the frame where both ankles are lowest); copied to `model.json` |

Canonical joints (24): `pelvis`, `l_hip`, `r_hip`, `spine`, `chest`, `neck`, `head`,
`head_top`, `l_collar`, `l_shoulder`, `l_elbow`, `l_wrist`, `l_hand`, `r_collar`,
`r_shoulder`, `r_elbow`, `r_wrist`, `r_hand`, `l_knee`, `l_ankle`, `l_toe`, `r_knee`,
`r_ankle`, `r_toe`. Each bone points along its segment: the hips take the frame of
`l_hip - r_hip` and `spine - pelvis`; the spine bones share `pelvis -> spine -> chest ->
neck`; neck `neck -> head`, head `head -> head_top`; shoulder `collar -> shoulder`, upper
arm `shoulder -> elbow`, lower arm `elbow -> wrist`, hand `wrist -> hand`; upper leg
`hip -> knee`, lower leg `knee -> ankle`, foot `ankle -> toe`.

**Hold placement** (`holds[kind]`, `holds[class]` or a clip's `hold`), looked up in that
order:

| Key | Meaning |
|---|---|
| `follow` | `chest` (placed on the rest body and carried by the top of the spine), `body` (fixed in body space) or `right_hand` (the item's socket at the right palm) |
| `socket` | the item socket put at `at` (default `grip`) |
| `at` | `[side, forward, up]` on the rest body (side widened with `--widen`) |
| `point` | `[side, forward, up]`: where the item's +x points |
| `up` | optional `[side, forward, up]`: the roll reference for the item's +z (default up) |
| `hands` | optional `{"right": socket, "left": socket}`: each wrist put on that socket by IK |
| `arms_from_rest` | optional: the arms start from the rest pose before the IK (carried items: every clip solves to the same arms) |

Without a placement the item follows the right palm and both hands stay as the clip has
them. A class without items in `--items` gets no sockets: its item bone follows the right
palm.

## Motions list

The humanoid's `motions` ([order.md](order.md#motions-of-a-humanoid)) end in clips of the motion
library above (`forge/motion_clips.py`, no bpy). From a source's positions (any units and up
axis) a clip is made in this order:

1. **Body coordinates.** Left from the hips (averaged over the clip), up the source's up axis,
   forward = left x up (turned round when the toes point the other way: a mirrored source);
   the floor is the lowest ankle or toe of the clip; positions are divided by the standing
   height (the tallest head over the floor; 0.97 times the bones' lengths when the clip never
   reaches 0.85 of that, a clip that never stands up); the first frame's pelvis is over the
   origin.
2. **Time.** A one-shot is resampled over its whole length to `frames`. A loop takes the
   window whose ends are most alike (pose relative to the pelvis, and speed), 0.8 to 1.25
   times `frames / fps` long, resampled to `frames`; the pelvis's drift over the window is
   removed (the figure walks in place) and becomes `travel`; the pose's residual at the window's
   end is spread linearly over the clip (`cycle_closure`). A travel under 0.02 body heights is
   dropped.
3. **Contacts**: the entry's `contacts`, else a library clip's `stance`, else detected: frames
   where an ankle is at most `contact_height` above its lowest and moves at most
   `contact_speed` in the ground frame (the body's travel added), one-frame gaps filled, runs
   shorter than `contact_min_frames` dropped. A foot planted on every frame is `"all"`.
4. **Hip height** (`hip_height`): when a planted ankle is off the clip's median planted ankle
   height by more than `ground`, each frame with a planted foot is lifted or lowered so its
   lowest planted ankle stands at that height (frames between interpolated, smoothed).
5. **Foot contacts** (`foot_contacts`): when a planted ankle moves more than `foot_slide_fix`
   within a run (ground frame), it is held at the run's median place, its toe moved with it,
   the frames next to the run moved half way, the knee placed by two-bone IK with the bones'
   lengths; the reach it lacked is reported.
6. **Below ground** (`below_ground`): the lowest joint may be at most `ground` below the floor
   (not checked for `feet` `lie`).

Thresholds (body heights; 0.01 is 1.8 cm on a 1.8 m figure), the constants `THRESHOLDS`:
`contact_height` 0.02, `contact_speed` 0.15 a second, `contact_min_frames` 3, `foot_slide`
0.004 (the check after the cleanup), `foot_slide_fix` 0.002, `ground` 0.004, `seam_factor` 2 and
`seam_min` 0.004 (a loop's seam jump may be the larger of `seam_factor` times its median frame
step and `seam_min`), `window_min` 0.8 and `window_max` 1.25.

A clip's report (`forge-motion-clip-report`): `name`, `source`, `source_frames`,
`source_fps`, `frames`, `fps`, `loop`, `scale` (`height_source_units`, `height_from`,
`mirrored`), `window` (loops: `start`, `frames`, `seconds`, `cost`), `contacts` (`how`:
`given` or `detected`, `left`, `right`), `corrections` (each: `name`, `applied`, `threshold`,
`before`, `after`, `passed`; `cycle_closure`'s `before` is the residual at the window's end,
its `after` the seam jump, with `median_step`; `foot_contacts` also `reach_shortfall`),
`thresholds`, `units`, `feet`, `travel`, `flags` (a failed check: `<name>: <after> over
<threshold>`) and `valid`.

Readers: **bvh** (`read_bvh`: the hierarchy's joints and end sites by forward kinematics, the
rotation order of each joint's channels, position channels on any joint; joint names matched
after dropping a `prefix:` and case: CMU's BVH conversion (`Hips`, `LeftUpLeg`, `Spine1`,
`Neck1`, `LeftFingerBase`, ...) and Mixamo's; the top of the head, a hand, a collar, the chest
and the neck are derived when missing). **HY-Motion** (`smpl_canon`: the SMPL body order of its
`keypoints3d` (52 joints, the middle fingers' first joints as the hands) plus `transl`, which
the keypoints leave out; y up).

**Made clips** (`<out>/<family>/_motions/<name>/`): `clip.json` (a motion library holding the
one clip, named as the entry), `report.json`; for `text` also `motion.npz` (HY-Motion's arrays
as it wrote them), `hymotion.json` (its record: model, text, seed, steps, guidance, duration,
fps, frames, checkpoint and config SHA-256, torch and CUDA versions, times, peak VRAM),
`prompt.txt`, `motion_text.provenance.json` (as the other local tools': the entry script and
`motion_clips.py` hashed, the checkout's git revision, the checkpoint, the seed, the effective
settings, the prompt, the command) and its logs.

**The piece's library** (`<out>/<family>/<piece>/motions/`): `library.json` (the entries in the
list's order, each named as the entry, its `kind` and `holds` as the entry gives them; the
hold placements of `inputs.clips` and of the library files used, the first one of a name
kept) and `report.json` (`forge-motions-report`: per clip the library clip's report, or the
made clip's job and flags; `flags`, `valid`).

**HY-Motion Lite** (`tools/motion/hymotion_entry.py`, run by `forge/motion_text.py`): the
configured `hymotion_python` with `hymotion_dir` (and `hymotion_pythonpath`) on its path,
`HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` set and `-B` (no bytecode written into the
checkout). The text encoders (`hymotion_text_encoder`, `hymotion_clip`) encode the prompt and
are freed, the motion model is built from `config.yml` next to `hymotion_model` (with the
checkout as the working folder, for its bundled body assets) and loads the checkpoint (every
learned parameter must match; only the body model's buffers may be missing), then generates
`duration` seconds at its own 30 fps with the seed, steps and guidance. No prompt rewriting,
no duration estimate. `-Check` imports the entry's modules in that Python
(`forge/motion_text.py` `import_check`), nothing more.

## Held-item socket file (`inputs.items`)

A folder with one `<name>.json` per item, next to its `<name>.glb` (item space). The
`held_item` recipe makes such pairs ([below](#held-item)) and gathers each family's picks in
`<out>/<family>/items/`; a file without both `class` and `sockets` is skipped, and other keys
are ignored.

| Key | Meaning |
|---|---|
| `name` | the item's name (default: the file name) |
| `class` | its hold class (matched by the library's `holds`) |
| `sockets` | `{socket: [x, y, z]}` metres in item space; `grip` (the firing or holding hand's wrist) is the origin; others as the game names them (`guard` for the support hand, `butt`, `shoulder`, ...) |
| `box` | optional `[[x, y, z], [x, y, z]]`: the item's bounding box in item space (the motion report's penetration measure); without it the box of `<name>.glb` is used |
| `length_m` | optional: its largest size in metres (informative) |

The exporter averages the sockets of the items of each class and keeps the union of their
boxes (`model.json` `classes`).

## Exporter outputs

`<out>/<name>.glb`:

- mesh `body`, skinned, at most 4 influences per vertex, weights normalised; its material:
  the painted base-colour texture at its original size, a tangent-space normal map
  (`--tex`), and the metallic-roughness map (glTF convention: roughness in green, metallic
  in blue), opacity (the base colour's alpha), emissive and occlusion maps when their
  source exists. `--metal-roughness` (the paint stage's map) replaces the rig's
  metallic-roughness map; a map the rig's material lacks (metallic-roughness, opacity,
  emissive, occlusion) is taken from `--material-source` (the painted mesh the rig was
  made from, same UVs); its base colour and normal map are not taken. Images: with
  `--image-format lossless` (default) every map is PNG; with `compact` the base colour is
  JPEG at quality 92 (PNG when it carries opacity) and every other map PNG. A normal or
  metallic-roughness map, an occlusion map or an opacity mask is never JPEG;
- the armature: mapped bones named as Godot's `SkeletonProfileHumanoid` (`Hips`, `Spine`,
  `Chest`, `UpperChest`, `Neck`, `Head`, `LeftShoulder`, `LeftUpperArm`, `LeftLowerArm`,
  `LeftHand`, `LeftUpperLeg`, `LeftLowerLeg`, `LeftFoot`, `LeftToes` and the `Right`
  ones); bones without a role (fingers, extras, an extra root) keep the rig's names;
- two bones that carry held items: `item` (under the hips; the right hand's item, its
  origin at the item's grip with the item's axes) and `item_l` (on the left palm); a tiny
  mesh `item_ref` on `item` is the identity of item space: an item GLB placed with
  `item_ref`'s transform sits where the clip holds it;
- one animation per library clip, named as the clip, keyed at the clip's frame times
  (linear) plus a closing key at `frames / fps` seconds (the first frame again for a loop,
  the last held otherwise): the animation lasts exactly `frames / fps`. Head, hand and
  foot scales (`--head`, `--hands`, `--feet`) are keyed in every animation. Without a
  library the GLB has the rest pose only.

`<out>/model.json` (`format` `forge-humanoid`, `version` 1, `status` `pending review`):
`name`, `height` (metres), `widen`, `head`, `hands`, `feet`, `ankle_height` (metres),
`front` (glTF direction the figure faces), `bones` (`{role: bone name}`),
`unmapped_bones`, `bone_map` (`source`, `file`), `item_bones` (`right`, `left`,
`reference`), `classes` (`{class: {items, sockets, box}}`), `items` (`{name: {class}}`),
`animations` (per clip: `name`, `kind`, `frames`, `fps`, `loop`, `seconds`, `holds`,
`class`, `follow`, `hands` (`{side: socket}` put on by IK), `feet`, `stance`, `travel`,
`source`, `pose_frame`), `mesh` (`triangles`, `reduced`, `normal_map`, `normal_baked`, `report`,
`image_format`),
`regions` (`mask`, mean luminance of `skin` and `hair` in the painted texture, pixel
counts `skin_pixels`, `hair_pixels`, `glow_pixels` (texels whose glow weight is over 0.5;
`null` when no glow colour was given), and with a glow colour `glow_share` (of the
texture), `glow_colours`, `glow_hues` (their hue ranges in degrees), `emissive_strength`
(`null` when nothing glows) and `glow_reference` (the reference picture's file name)),
`facing` (below), `seconds_export`.

### The way an exported figure faces

The exporter turns the rig about the vertical so that the front its body gives
(`humanoid_bones.body_facing`, no `bpy`) is glTF +Z. Not the bone map's `front`: that one is
snapped to the nearest horizontal axis (it only tells left from right), and the first real
night's figures, which stood turned between two axes in their generated meshes, were exported
turned 22 to 40 degrees by it (fixed 2026-10-19). The cues, from the rest joints of the mapped
bones:

- the line across the hips (left upper leg's joint minus the right's) and the line across the
  shoulders (the upper arms' joints), seen from above: the body's left; the front is
  perpendicular to it (left x up), on the side the map's left and right give (the toes chose
  them when the map was proposed). Both lines are summed, so each counts by its length; a line
  shorter than 2 % of the figure's height is not used;
- the feet (each foot's joint to its toes' end, both summed) only break a tie: when the two lines
  are more than 25 degrees apart, the one nearer the feet gives the front;
- the generated mesh's own facing is not used: the night's rigs did not all face one axis (19 of
  21 faced glTF +Z and 2 faced +X as the rig stage delivered them), and no cue of the face is read
  (no bone of the map points out of the face).

`model.json` and `mesh_report.json` `facing`: `squared` (false with `--body-frame`), `frame`
(`rig` or `body`), `source` (`hips and shoulders`, `hips`, `shoulders`, `hips (nearer the
feet)`, `shoulders (nearer the feet)` or `feet`), `turned_deg` (the turn applied),
`lines_apart_deg`, `feet_apart_deg` (the feet against the front used), `before` and `after`
(`turn_deg`, the front's degrees from facing glTF +Z, positive towards glTF +X, and `cues`:
`hips`, `shoulders`, `feet`, each cue's own front the same way), `limits` and `problems`. Mesh
flags (`facing: ...`; the variant is rejected, the stage held responsible `unknown`): the hips'
and shoulders' lines more than 25 degrees apart (a twisted figure: the way it faces is not
known), the feet more than 60 degrees from the front used, no line measurable (the feet then
give the front), and a squared figure whose front is still more than 0.5 degrees from glTF +Z
after the turn. On the night's 21 rigs the lines were at most 7.1 degrees apart and the feet at
most 10.3 degrees from the front used, and no rig was flagged.

`<out>/regions_mask.png`: the painted texture's size; red = skin (warm, moderately
saturated colour on the head), green = hair (darker warm colour on the top of the head),
blue = what glows: the glow weight 0-255 (0.5 and over is a light), found with
`--glow-colours` (the order's "What glows", [order.md](order.md)) or `--glow` (the
`--glow-hues` range) by `tools/blender/glow_regions.py` on the written GLB's base colour,
never on skin or hair. The same lights are the GLB material's emissive map (below). A game
can recolour these regions; the factory does not.

### What glows in the GLB

With a glow colour the exporter (and `python -m forge.glow`) rewrites the written GLB
without Blender: every material with a base-colour texture gets `emissiveTexture` (a PNG
named `emissive_glow_<material>`: each glowing texel's own colour brought to full value,
times its weight; black elsewhere; the base colour's sampler and UV set),
`emissiveFactor` `[1, 1, 1]` and, when the strength is not 1,
`KHR_materials_emissive_strength` (`emissiveStrength`, default 2; listed in
`extensionsUsed`; Godot 4 reads it as the material's emission energy). The binary chunk is
rebuilt view by view, every view keeping its index. A material whose emissive slot already
holds a map of its own (carried from the material source) keeps it, and only the mask
marks the lights. When nothing glows the GLB is left as written. `mesh_report.json`
`materials.slots.emissive.source_from` is then "the exporter's glow (colours)".

### Glow on an exported model

`python -m forge.glow` gives an exported model its glow again without exporting it (no
Blender, no GPU, nothing sent to a server): after the detection changed, or with other
colours, a strength, or the reference picture.

    python -m forge.glow SOURCE --out DIR [--colours cyan,emerald] [--text TEXT]
                         [--order ORDER.json --piece FAMILY/PIECE] [--reference PICTURE]
                         [--strength 2] [--set name=value ...] [--off]

`SOURCE` is an exporter's folder (`<name>.glb` with `model.json` and, for a humanoid,
`regions_mask.png`: the humanoid's or the creature's `final/` or `v<seed>_export/`) or a
GLB file (a figure's `mesh_painted_mesh.glb`). The colours come from `--colours`, else from
the colour words of `--text`, else from the piece's text in an order (`--order` with
`--piece`: the text the factory composes for its concepts), else from the model's
`model.json` (`regions.glow_colours`). `--reference` is the picture the paint stage
projected (the variant's `v<seed>_cutout_rgba.png`, a figure's `pick_chosen.png`).
`--set` changes a detection setting (`value`, `chroma`, `saturation`, `region_saturation`,
`chroma_contrast`, `contrast`, `weak_value`, `weak_chroma`, `core_value`, `max_region`,
`max_total`, `reference_weight`; defaults in `glow_regions.DEFAULTS`). `--off` takes the
glow out. An earlier glow (the exporter's or this command's) is replaced; an emissive map
of the material's own is kept. Exit code 2 on a problem (no colours, an unknown colour or
setting, no base-colour texture).

Writes into `DIR` (which may be the source folder: its files are replaced): the GLB (same
name), `glow_emissive.png` (the map), the mask (`regions_mask.png`, a copy of the source's
with red and green kept and blue replaced, or `glow_mask.png`, the weight in grey, for a GLB
without one), `model.json` (a copy with `regions` updated as the exporter writes it) and
`glow.json` (`forge-glow`: `colours`, `strength`, `reference`, `glow_pixels`, `glow_share`,
`emissive` per material (`written`, `none`, `kept ...`) and per material the detection's
report: `hues`, `settings`, `regions`, `regions_too_large`, `regions_pale`,
`largest_region_share`, `reference` (`lit_pixels`, `supported`), `capped`).

`<out>/bone_map.json` (`format` `forge-bone-map`):

| Key | Meaning |
|---|---|
| `source` | `proposed` (from the tree), `manual` (this file, written by a person) or `order` (`inputs.bone_map`) |
| `manual` | `true` makes this file win over the proposal (`roles` is then read); the file's hash enters the export job's specification, so the next factory run redoes that export, the review and the final copy. A file with `source` `order` is the exporter's own copy of the order's map and never counts as a correction; to correct it by hand, set `source` to `manual` |
| `valid`, `problems` | the validation |
| `roles` | `{role: rig bone name}` when valid, else `null` |
| `found` | what was found or given, valid or not |
| `godot_names`, `unmapped`, `front`, `left` | the profile names, the bones without a role, the rest directions (Blender axes, z up) |

Roles: `hips`, `spine`, `chest`, `upper_chest` (optional), `neck` (optional), `head`, and
per side `shoulder` (optional), `upper_arm`, `lower_arm`, `hand`, `upper_leg`,
`lower_leg`, `foot`, `toes` (optional). Validation: every role names an existing bone, no
bone twice; spine, chest and the upper chest along one chain below the hips; the neck and
the head below the top of the spine; the lower arm a child of the upper arm and the hand a
child of the lower arm (the leg likewise); the upper legs below the hips and not below the
spine; each hand and foot on its side of the body (front from the toes) and each side's
hand and foot on the same side; the upper and lower arm and leg of the two sides within
15 % of each other's length. An invalid map stops the export (exit code 3); a manual file
stays as written and the validation goes to `bone_map_report.json`.

How the proposal tells the neck from the arms at the top of the spine (where three or more
children branch): the arms are the two children that reach farthest across the body, one to
each side (across is up x the front the feet give); the neck is, of the others, the one
between the arms that ends in no fan of leaves (fingers) and goes up rather than out and
down. Not the child whose end point is highest: rigs of bulky armour have clavicles whose
tails point up above a short neck under a big helmet (fixed 2026-10-19; the synthetic trees of
`tests/fixtures/humanoid/bone_trees.json` reproduce them).

`<out>/mesh_report.json` (`forge-mesh-report`, `version` 2, `status` `pending review`):
`uv_layers`, `material_count`, `base_color_texture`, `texture_size`, `vertices`, `unweighted_vertices`,
`weight_sum_error`, `max_influences`, `influence_limit`, `triangles_in`, `triangles`,
`triangle_budget`, `reduced` (only when the mesh had more than `--tris` triangles),
`seam_vertices_merged`, `normal_map`, `normal_baked`, `bake_device`, `bake_source`
(`--bake-source` file name, `mesh before reduction`, or `null`), `bake_source_rejected`
(the bake source's name when it was left out), `bake_source_offset_m` (how far its bounds
are from the game mesh's, after any fit), `bake_source_fit`, `problems` and `flags` (the
same list), `valid`, and `materials` (below). No UV layer or no base-colour texture stops
the export (exit code 4).

`materials` is read from the written GLB itself (its JSON and the bytes of its images,
`tools/blender/glb_materials.py`), not from Blender's scene: `image_format`, `read_from`,
`replaced` (maps a better source replaced: the rig's normal map by the exporter's, the
rig's metallic-roughness map by the paint stage's, a rig metallic-roughness or emissive
map whose source is JPEG/WebP by the material source's lossless one), `not_carried`
(source maps that could not be carried, with why), `not_taken` (the material source's own
base colour and normal map, dropped by design), `notes` (a carried map whose source went
through a factor node the carry does not reproduce) and `slots`: for each of `base_color`, `normal`,
`metallic_roughness`, `occlusion`, `emissive` and `opacity`, `present`, `format` (`png`,
`jpeg`, `webp`), `size`, `lossless`, `image` (its name in the GLB), `source` (whether its
source map existed) and `source_from` (where it came from: the rig's material, the paint
stage's map, the material source, the exporter); `opacity` also has the material's
`alpha_mode` and is present when the mode is `BLEND` or `MASK` and the base colour has an
alpha channel. The GLB decides the formats: a mesh flag `material slot <slot>: ...` is
raised for every present slot that is lossy where the format forbids it, whether or not the
exporter saw a source for it (JPEG anywhere with `lossless`; with `compact` anywhere but
an opaque base colour), for a slot whose source existed but that is missing, and for a
source map that could not be carried; `material
source: ...` when the material source itself could not be read.

The bake source must line up with the game mesh: 90 % of the game mesh's vertices within
1 % of the height from its surface (`bake_source_fit`: `tolerance_m`,
`distance_before_m` with the median and 90th percentile, `bounds_offset_before_m`). When
it does not, its bounds are fitted to the game mesh's by one uniform scale and a
translation (`corrected`, `scale`, `translation_m`, `distance_after_m`,
`bounds_offset_after_m`); when it still does not line up it is not used (`used` false, a
flag): the normal map is baked from the game mesh before its reduction, or, when it was not
reduced, holds only the painted texture's relief.

Blender's glTF exporter logs `ERROR Draco mesh compression is not available ...` whenever
it starts in a `bpy` build without the Draco library; the GLB is written without Draco
compression (the exporter turns it off and logs a `NOTE` line right after).

`<out>/motion_report.json` (`forge-motion-report`, `status` `pending review`), with a
library only. Every clip is played on the exported GLB, loaded again in a fresh Blender:

| Measure | Meaning | Default threshold |
|---|---|---|
| `foot_slide_cm` | largest distance an ankle moves within a run of declared stance frames, with the clip's `travel` added (null without `stance`) | 2 cm (`--max-foot-slide`) |
| `hand_socket_cm` | largest distance from a wrist to the socket its placement puts it on | 2 cm (`--max-hand-socket`) |
| `reach_shortfall_cm` | largest distance an IK target (hand or foot) lies beyond the limb's length | 1 cm (`--max-reach`) |
| `stretch_p95` | 95th percentile over edges of `abs(length / rest length - 1)`, worst frame | 0.5 (`--max-stretch`) |
| `penetration_cm` | depth of the body's vertices (not hands, fingers or forearms) inside the held item's box | 2 cm (`--max-penetration`) |

`flags` lists `{clip, measure, value, threshold}` for every measure over its threshold
(and `missing` for a clip the GLB does not have). The thresholds are starting values: on
one real rig the stretch was about 0.40 in standing clips and 0.61 kneeling.

### A figure that wears an approved body

With `inputs.body` the export job (`body_export`, `forge/local_gpu.py run_body_export`) runs
two Blender steps in `v<seed>_export/`:

1. `tools/blender/humanoid_conform.py` (its geometry in `humanoid_fit.py`, without `bpy`) in
   `conform/`. The body: the GLB without its item bones, `item_ref` and animations, its joints
   by role from its `model.json` `bones`. The variant: its rig's bone map, chosen as the
   exporter chooses it (the order's `bone_map`, else `conform/bone_map.json` marked
   `"manual": true`, else the proposal; an invalid map stops with exit code 3). Then:
   - a prefit: the variant turned so that its front is the body's, then about the vertical
     and scaled uniformly by least squares over its joints seen from the hips, its hips on
     the body's;
   - one matrix per role: each limb segment (shoulder, upper and lower arm, upper and lower
     leg, foot to toes) turned onto the body's segment by the smallest rotation and
     stretched along its line, so both its joints land on the body's; the torso (hips to
     neck) one piece the same way; the neck and head one piece set on the body's neck joint
     with the torso's turn, not stretched; a hand, toes or a foot without toes turned with
     its parent segment and set on its own joint;
   - every vertex moved by the variant's own weights (linear blend skinning), the bake
     source's vertices by the weights of the variant's nearest vertex;
   - the mesh bound to the body's skeleton with the variant's weights renamed to the body's
     bones: a bone without a role (a finger) counted with its nearest mapped ancestor, a role
     the body lacks with its nearest role the body has. The body's finger bones carry no
     weight.
   It writes `conform/conformed.glb` (the body's skeleton, the conformed mesh, the variant's
   material), `conform/conformed_raw.glb` (the moved bake source, removed after the export),
   `conform/frame.json`, `conform/body_map.json` (the body's roles) and
   `conform_report.json`.
2. `tools/blender/humanoid_export.py --body-frame conform/frame.json`: the rig is not stood,
   turned, scaled or widened (it already stands in the body's frame), and the figure's floor
   and height are the body's (the clips are scaled by them, not by the outfit's mesh: a helmet
   or a sole would change them). The body's `widen`, `head`, `hands`, `feet` and `height` are
   passed on the command line, the body's roles as the bone map, the conformed raw mesh as the
   bake source. Its `facing` is measured but not applied (`squared` false). Until 2026-10-19 a
   wrapper ran the exporter with three of its statements replaced; the option took its place.

`conform/frame.json` (`forge-body-frame`): `height` (the body's `model.json` height), `floor`
(the body's left ankle height minus its `ankle_height`), `widen`, `head`, `hands`, `feet`,
`front`, `body` (its file name).

`conform_report.json` (`forge-conform-report`, `version` 1, `status` `pending review`):

| Key | Meaning |
|---|---|
| `valid` | false when the variant's bone map was invalid (then only `bone_map` and `flags`) |
| `body` | the body's file name, height, roles and bone count |
| `bone_map` | the variant's map's `source` and file |
| `weights`, `bones_folded` | how the weights were kept; the variant's bones without a role |
| `roles`, `missing_roles` | the roles both skeletons have; the body's roles the variant lacks (their bones carry no weight) |
| `prefit` | `turn_deg` (about the vertical, of which `refine_deg` after the front), `scale` |
| `joints` | per role: `before_cm` (after the prefit only: how the proportions differ), `after_cm`, `scale` (its segment's stretch), `kind` (`torso`, `head`, `segment`, `end`) |
| `joint_error_cm`, `joint_error_role` | the largest `after_cm` but the neck's and head's (the torso's spine joints) |
| `head_offset_cm` | how far the body's head joint is from the conformed variant's |
| `joint_outside_cm`, `joint_outside_max_cm` | per role, how far the body's joint lies outside the conformed outfit (0 inside): a point is outside when at least a quarter of 26 rays from it leave without crossing the surface, which tolerates the inner shells, open seams and mixed winding of generated meshes |
| `segment_scale` | the smallest and largest segment stretch |
| `stretch_p95`, `stretch_max`, `torn_share` | the edges' `abs(length / length after the prefit - 1)`; the share stretched over twice or under half |
| `floor_offset_cm` | per side, how far the outfit's sole (the lowest vertex of the foot and toes) is below (+) or above (-) the body's floor |
| `body_outside` | the body's surface (up to 20000 of its vertices) outside the conformed outfit: `p95_cm`, `p99_cm`, `max_cm`, `share_over_1cm`, `sampled` |
| `thresholds`, `flags` | the flags over the thresholds |

| Flag | Default threshold (setting) |
|---|---|
| `joints: the conformed <role> joint misses the body's by ...` | 4 cm (`conform_joint_cm`) |
| `scale: the <role> segment is stretched by ...` | outside 1/1.5 to 1.5 (`conform_scale`) |
| `tear: ... of the edges are stretched over twice or under half ...` | 0.2 % (`conform_torn_share`) |
| `outside: the body's <role> joint lies ... outside the outfit` | 1.5 cm (`conform_outside_cm`) |
| `floor: the <side> sole lies ... below/above the body's floor` | 4 cm (`conform_floor_cm`) |

The thresholds are starting values from one night's armours (below 1.5 for segment
stretch, joints inside, soles within 2 cm).

`model.json` of such a figure adds `body`: `file`, `sha256` (the body's GLB), `model_sha256`,
`conform_report` and `skeleton`, the comparison of the written GLB's skeleton with the body's,
read from both files (`forge/body.py`): `identical`, `joints`, `max_offset_m`, `max_turn_deg`,
`max_scale`, `max_inverse_bind`, `problems`. Joints' rest translations within 0.1 mm, rotations
within about 0.01 degree, scales within 1e-4 and the `item_ref` node's place are the same
skeleton (Blender's round trips leave a few 1e-7). A skeleton that differs is a mesh flag
`skeleton: ...`. Its `height`, `widen`, `head`, `hands` and `feet` are the body's.

## Review sheet

`run_review` writes `<name>.png` (transparent background, text on dark bands) and
`<name>.png.json`. Per variant: a header with its label and `pending review` (or
`FAILED: <reason>` with a red cross in place of the figures); the figure in every
direction of the review camera (orthographic, `pitch` degrees above the horizon, a
standing figure `figure_height` pixels tall) posed in its first idle (else the rest
pose) at the clip's `pose_frame` when it has one, else at the frame where both ankles are
lowest (the header names the clip and the frame; Godot's log prints it), and one large
front view; a strip of 8 frames of the walk, then of `start` and
`stop` when the model has them, from the strip direction; and one line per mesh flag and
per motion measure over its threshold. Nothing on the sheet marks a candidate approved.

The layout file: `size`, `status`, and per variant `label`, `failed`, `reason`, `pose`,
`pose_frame`, `pose_time`, `pose_how`, `flags` and `cells` (`kind`: `small`, `front`, `strip` or `failure`; `yaw`, `clip`,
`time`, `rect` = `[x, y, width, height]` in pixels), `verdict` (`acceptable`, `rejected`
or `null`) and `verdict_reasons`; and `banner` (the line over the sheet, or `null`).

The factory's review job passes each variant's acceptance verdict (forge/acceptance.py)
with its first two reasons (the worst first): a line under the header, green for
`acceptable` (`acceptable: motion not gated` when the order set no motion limit), red for
`rejected`; "pending review" stays. When no variant is acceptable a red banner over the
sheet says `NO VARIANT PASSED: MOST OFTEN THE <STAGE> (<n> OF <m>)`. A variant that wears an
approved body has one more reason at the end of that line, its conform measures:
`conform: joints <cm> cm, stretch p95 <x>, body out <cm> cm, <n> flags` (the Godot project is
unchanged; it joins the reasons into the line).

## Pick outcomes

The humanoid's decision (`pick_final.json`, and `pick_final_reserve.json` after reserve
variants, [order.md](order.md)) has an `outcome`: `picked` (the best acceptable variant,
copied to `final/`, pending review), `manual` (a person's choice in the file) or
`no_variant_passed` (no acceptable variant: `chosen` is `null`, `final/` is removed,
`responsible_stage` and `responsible_counts` say which stage was held responsible for the
rejected variants; a piece with `reserve` releases its reserve variants).

## Provenance of the local steps

Each runner writes `<out>/<step>.provenance.json` (`rig`, `export`, `review`) in the
record form of the factory's provenance (`kind` `local_gpu`, `workflow` and its SHA-256:
the script the step runs), plus `tool_files` (SHA-256 of the scripts it uses), `tools`
(configured paths, the bpy or Godot version, the rig checkout's git revision), `models`
(weight files with sizes and SHA-256, for the rig), `seeds`, `settings`, `inputs` (role,
file, SHA-256; a held-item folder lists each socket file), `command`, `exec_seconds` and
`outputs` (file and SHA-256 per role). Its stdout and stderr are next to it
(`<step>.stdout.log`, `<step>.stderr.log`).

## Held item

`tools/blender/held_item_export.py` (run by `run_item`, the held-item recipe's `item` stage;
the geometry in `tools/blender/held_item_geometry.py`, no `bpy`). It reads the painted mesh
(glTF axes) and writes the item in item space ([above](#axes-and-units)).

The frame is found (`orient`) when `--front` and `--top` are both `auto` (the default); given
axes (`--front`, `--top`, or a manual `frame.json`) skip the search. The mesh generators keep
the reference picture's up on glTF +y but lay its left-right on any of glTF +x, -x, +z and -z
(the first real night, 2026-10-20: all four among 88 variants).

- With `--reference` (the cutout the mesh was made from, read for its alpha; the recipe gives
  it): every candidate frame (the 24 with the front and the top on glTF axes, and, when the
  item is elongated and its longest principal axis is more than 30 degrees from every glTF
  axis, the 8 on its principal axes) is scored by its side silhouette (x to the right, z up)
  against the picture's: each on a 64 x 64 grid over its own box, their overlap (intersection
  over union) times the square root of the ratio of their aspects. A candidate ranks at its
  best of the turns 0, 8, 16 and 24 degrees either way about its side axis, less 0.004 per
  degree, plus 0.03 times the cosine between its top and glTF +y; the best one is used.
  Flags: `frame: the mesh matches its reference picture from no side` (a score below 0.5).
- The gun cues (templates `pistol`, `rifle`, `carbine`, `heavy`, `launcher`), over 24 stations
  along the length: `rear` (0.5 minus the centre of the profile's height along x), `ends` (the
  back end's height minus the front end's, over the tallest station) and `up` (how much more
  the profile's bottom edge varies than its top edge). The front cue is rear / 0.05 + ends /
  0.25. With a reference they check its frame: a front cue of -1 or less, or an up cue of
  -0.04 or less, is the flag `frame: the cues disagree: ...`.
- Without a reference: a gun or a `melee` item takes its longest principal axis as its length
  (the nearest glTF axis when within 35 degrees), its top as near glTF +y as square to it,
  then a gun's up cue and front cue turn it round when they say so; a front cue between -1
  and 1 is the flag `frame: the front cannot be told: ...`. A `device` or `thrown` item keeps
  the front glTF +x and the top +y.
- A template with `square` whose two largest spreads are within a ratio of 1.3 gets the flag
  `frame: a long item with no one long axis` (a mesh of two crossed bodies came out on the
  night), and one whose longest axis (at a ratio of at least 1.5) is more than 30 degrees from
  the front found, `frame: the longest axis is ...`. With a frame given, the squaring's flag
  `frame: the longest axis is ... from the front axis` (at a ratio of at least 1.5, more than
  30 degrees) is given to a template with `square` only (before 2026-10-20 to every template:
  a pistol's grip pulled its longest axis 31 degrees off on the night).

Then a long, straight item (the template's `square`, or `--snap 1`) is squared. With a
reference it is turned about its side axis by the turn (1 degree steps, up to 30) that
matches the picture best, when that is 3 degrees or more and raises the score by 0.01, and it
is not rolled. Without one it is turned so that its longest principal axis lies on +x, then
rolled so that its second axis lies on +z, each only when the turn is 8 to 30 degrees and the
item is elongated (spread ratios 1.3 and 1.2). Then it is scaled to `--length` (its extent
along +x) or `--height` (along +z), and moved so that its grip is the origin.

The sockets: the class template merged with the order's `item.sockets`
([order.md](order.md#the-held-item-recipe)), placed on surface samples (20 000 points spread
by area with a fixed seed, plus the vertices): `section` takes the points within 3 % of the
length of station `u` (6 %, when fewer than 6 points are there) whose height is inside
`band`, splits them where their heights leave a gap of 6 % of the item's height and keeps
the run nearest the socket's height `w`, and puts the socket in the middle of their extent
across and up, at the station (or at the nearest end of their extent along x when the slab
only grazes them). The centre must be on the mesh: within `--tolerance-cm` of a surface point,
or enclosed (surface points above, below and on both sides of it within 2 % of the item's
largest extent, or the tolerance when larger). When it is not, or the band is empty there,
the stations around it are tried, nearest first, in steps of 1 % of the length up to 20 %;
a socket found off its station is centred on the piece it found (up to 4 steps, the middle
of that piece's extent along x). `surface` takes the closest point; `none` the template
position. Flags: `socket <name> is missing: no geometry at its station` (a required socket
with no station on the mesh within reach), `socket <name> is off the mesh: <d> cm from the
surface and outside its cross-section` (a manual socket farther than `--tolerance-cm` from
every surface point and outside the extent, widened by the tolerance, of the points of its own
station).

Outputs in `<out>/`:

- `<name>.glb`: the item mesh `<name>`, in item space (front glTF +x, top glTF +y, the grip at
  the origin), with the painted material (the paint stage's metallic-roughness map linked, as
  the articulated recipe's), no animation.
- `<name>.json`: the socket file ([above](#held-item-socket-file-inputsitems)), with
  `format` `forge-held-item`, `version` 1, `status` `pending review`, `name`, `class`,
  `sockets` (those found), `box` and `length_m`.
- `model.json` (`format` `forge-held-item-model`): `name`, `hold_class`, `template`, `axes`,
  `size_m` (item space), `box_m`, `length_m`, `socket_file`, `socket_records` (per socket:
  `used` (the position written), `source` (`proposed` or `manual`), `template_at`, `snap`,
  `required`, `found`, `shift_m` (how far the snap moved it), `points`, `station` (a
  section's station used)), `required`, `frame` (`source` `found`, `order` or `manual`,
  `front_given`, `top_given`, `found` (the search's record or null: `method` (`reference`,
  `shape` or `default`), `shape`, `front` and `top` (glTF vectors), `front_axis` and
  `top_axis` (their axis names, or null), `score`, `overlap`, `aspect`, `reference_aspect`,
  `other_front` (the best candidate with another front) and `margin` (the ranks' difference),
  `cues` (guns), `spreads`, `ratio`, `length_deg`, `flags`), `square` (`turned_deg`,
  `length_deg`, `length_by` (`reference` or `principal axes`), `tilt_deg` and `tilt_gain`
  (with a reference), `roll_deg`, `ratio`, `squared`, `flag`), `scale`), `sockets_source`, `fit`,
  `mesh`, `seconds_export`. It has no top-level `class` and `sockets`, so the humanoid
  exporter never reads it as a socket file.
- `frame.json` (`forge-item-frame`, `version` 2): `front`, `top` (`auto` or the axes given),
  `snap`, `turned_deg`, `square`, `found` (as in `model.json`), `scale`, `size_m`, `manual`,
  `how_to_change`. With `"manual": true` it is read and kept as written: `front`, `top` (glTF
  axes, or `auto` for the search), `snap`, and `rotate_deg` `[about x, about y, about z]`
  (degrees, applied in that order in item axes) instead of the squaring (with `rotate_deg`,
  an `auto` front or top is the default `+x` or `+y`).
- `sockets.json` (`forge-item-sockets`): `manual`, `source`, `template`, `sockets` (the
  proposal, item space). With `"manual": true` it is read and kept as written: its `sockets`
  are the item's, in the item space of the frame in use; a grip other than `[0, 0, 0]` moves
  the origin there (the item and every socket shift with it).
- `mesh_report.json` (`forge-mesh-report`, `version` 2, `kind` `held_item`): as the
  articulated one for the material, plus `triangles`, `triangle_budget`,
  `seam_vertices_merged`, the socket and frame flags in `flags`, and `fit`:
  `socket_shift` (the largest `shift_m` of a snapped socket, as a share of the length),
  `turned_deg` (the squaring turn), `loose_share` (the share of the surface, by area, outside
  the largest connected piece), `excess` (the sum of each value over its `reference`: 0.15,
  15, 0.05) and `reference`. The final decision ranks acceptable variants by `excess`; it never
  rejects one.
- `item_spec.json` (the order's item object as given), `item.provenance.json`, the logs.

No UV layer or no base-colour texture stops it (exit code 4).

**The hold** (`run_hold`, the `hold` stage): `tools/blender/held_item_figure.py` writes
`figure_rig.glb` from the order's figure (one armature and its skinned meshes; the bones
`item` and `item_l`, the `item_ref` mesh, other meshes and every animation removed;
`figure.json` lists them), then the humanoid exporter runs on it (`--tris` 100000000, so the
mesh is not reduced and no normal map is baked; `--tex` 256) with `items/` (this item's GLB
and socket file) and `clips.json` (the order's library with only the shown clips). Its outputs
are the exporter's ([above](#exporter-outputs)) with the GLB named `figure.glb`, plus
`clips.json`, `figure.json` and `hold.provenance.json`.

**The item sheet** (`run_item_sheet`, the `item_sheet` option): `forge/item_sheet_layout.py`
lays it out and `tools/review_godot/item_sheet.gd` (scene `item_sheet.tscn`) renders it. Per
variant: the header (label, `pending review` or `FAILED: <reason>` with a red cross, class,
template, length), the verdict line; the item alone in every direction of the review camera
at the scale of a 1.8 m figure `figure_height` pixels tall; the side view (yaw 0, pitch 0:
front to the right, top up, `side_width` pixels wide) with a dot and a label on every socket
and a line of their positions; with the hold stage, the figure holding the item in each of
its clips (at the clip's `pose_frame`, else frame 0) from yaws 0, 90, 180 and 270, each
`hold_height` pixels tall for the figure; then one line per mesh flag, per flag of the hold's
motion report (`hold: <clip> <measure> <value> over <threshold>`, or `hold: FAILED: ...`) and
the fit line. The figure's cells show a second instance of the item GLB put under the
figure's `item_ref` node. The layout file `<name>.png.json` (`forge-item-sheet-layout`):
`size`, `camera`, `banner`, `variants` (label, failed, reason, glb, figure, verdict, flags,
cells, `sockets` (each mark's pixel position)), `cells` (as the layout sheet's, plus
`subject`: `item` or `figure`, and `kind`: `small`, `side` or `hold`), `marks` (`at`, `r`,
`color`, `label`), `texts`, `failures` and `load_failures`.

## Articulated parts

`tools/blender/articulated_export.py` (run by `run_parts`, the `parts` stage; the geometry
in `tools/blender/articulated_parts.py`, no `bpy`). Axes: glTF, metres, y up, the object
faces +Z, left is +X, standing on y = 0 with its footprint centred on the origin.
Normalised object coordinates (the order's regions): `[side, forward, up]`, side -0.5 to
0.5 (+ left), forward -0.5 to 0.5 (+ front), up 0 to 1, each divided by the object's extent
along it.

`<out>/<name>.glb`: one node per part, named by its id, nested as the parts' parents, each
node's origin at its pivot (the root's at the origin), no rotation at rest and no
animation. Each part's mesh has the painted material and, on its cut faces, the plain
material `interior`.

`<out>/model.json` (`format` `forge-articulated`, `version` 1, `status` `pending review`):
`name`, `front` ([0, 0, 1]), `axes`, `size_m` ([width, height, length]), `center_m`, `root`,
`parts` (per part: `parent`, `node`, `triangles`, `cut_triangles`, `open_edges` (open edges
a cut left), `source_open_edges` (the generated mesh's own open edges in this part: along an
open edge of the mesh before any cut), `bridged_edges` (straight edges on a cut plane that
joined the ends of a section where the cut crossed one of the mesh's holes; they border that
hole), `lone_edges` (single open cut edges touching no other: the edge of a sliver lying on
the plane, not flagged), `islands` (separate pieces), `share` of
the painted surface, `bounds_m` at rest, `origin_m` in model space, and `regions`: each box
resolved, in model metres before the glTF turn, with the `narrowest` edges in normalised
coordinates), `joints` (per moving or fixed part: `part`, `parent`, `kind`, `axis` (glTF,
in the parent's frame; `null` for fixed), `range`, `unit` (`degrees`, or `metres` for
slide), `rest` 0, `pivot_m`, `offset_m` (from the parent's origin)), `interior_material`,
`frame`, `mesh`, `seconds_export`.

A game moves a part by setting its node's transform to the rest transform times a turn
of `value` degrees about `axis` (turn, hinge, spin) or a move of `value` metres along it
(slide).

`<out>/frame.json` (`forge-frame`): `front` (the glTF axis the generated front pointed
along), `snap` (squaring allowed), `turned_deg`, `snap_deg`, `scale`, `size_m`, `manual`.
With `"manual": true` it is read and kept as written (`front`, and `snap_deg` when given).

`<out>/mesh_report.json` (`forge-mesh-report`, `version` 2, `kind` `articulated`): as the
humanoid's for the material (`materials`, read from the written GLB) plus `parts` (as in
model.json), `triangles_in`, `triangles`, `triangle_budget`, `seam_vertices_merged`. Flags:
`part <id> is empty: its region holds no geometry; ...`, `part <id> is tiny: <share> % of the
surface in <n> piece(s) (under min_share ...): its region caught almost nothing; the order's
box for it probably misses the part on this variant`, `part <id> has <n> open edges where its
cut was not closed` (only the cut's: a generated mesh is not watertight, and its own open
edges, the bridges along its holes and lone sliver edges are reported, not flagged), `frame:
the object is <w> m wide and <l> m long (more than max_width ...): it may face sideways;
correct its frame.json` (with the `max_width` setting), the material slot flags. No UV layer
or no base-colour texture stops the tool (exit code 4).

A `[low, high]` edge of a region resolves to the waist nearest the part's parent: walking
from the parent's end of the range (below a low edge, above a high edge), the first level
whose section (its bounding rectangle inside the box's other bounds) is no wider than both
its neighbours and no wider than the narrowest plus half the range's spread, half a step
inside when the waist goes on; levels at either end with nothing of the parent inside the
bounds are left out. A turret's ring is found above the deck even when the turret tapers
narrower at its top, and a barrel's edge `[low, high]` along forward cuts it where it leaves
the turret's face.

## Creature

`tools/blender/creature_export.py` (run by `run_creature_export`, the creature's `export`
stage), with `creature_bones.py` (the role map) and `creature_motion.py` (the clips), both
without `bpy`. Model axes inside the tools: metres, z up, front -Y, left +X; the exported
GLB faces glTF +Z, standing on y = 0 with the root over the origin.

**Body coordinates** (templates, role-space clips): `[side, forward, up]`, side + is the
creature's left; lengths in body heights.

`<out>/bone_map.json` (`format` `forge-creature-map`): `source` (`proposed`, `manual`,
`order`), `manual`, `valid`, `problems`, `roles` (`{role: [bones base to tip]}` when valid),
`found`, `root` (the root path: the skeleton's root down to the first branching bone),
`joints` (per role with joints: `[{bone, joint, bend}]`; a chain with fewer bones than the
plan's joints gives them the joints in order), `unmapped`, `split` (a chain taken from the
end of its parent), `twins` (`{role: [bones]}`: the mirror run of a single chain that hangs
unmapped beside it, as a rig tool predicts a serpent's tail or a propulsion unit as a pair of
legs; its bones turn as the role's bones at the same place along the chain), `facing` (the
exporter's `creature_bones.skeleton_facing`: `turn_deg` 0, 90 or -90, `across_deg`, `pairs`,
`feet_deg`, `problems`), `notes`, `front`, `left`. The precedence and the manual rule are the
humanoid's (a manual or given document may carry `twins`); an invalid map stops the export
(exit code 3), and so does a skeleton lying a quarter turn off whose feet do not tell its
front (`facing: ...`, with a proposed map).

The proposal also takes, for a required single chain no run points along, the nearest
remaining run of its parent by direction, when it is a limb's length (0.12 of the skeleton's
size) and runs along the middle or has a mirror twin; and a single chain must run along the
middle (its mean distance from the hub across under 0.08 of the size) unless it points
within about 45 degrees of its way and has a mirror twin, so a vestigial arm is not taken
for a neck.

The facing: a rig tool may predict the skeleton a quarter turn off (on the first real night
two of four rigs of one beast). The runs that hang from one bone and mirror each other (each
at least 0.2 of the size, lengths within the pair tolerance, one reflected onto the other
within 0.15 of their length) give the body's left-right line; when it lies more than 45
degrees from the side axis, the rig is turned a quarter turn so that the feet of the pairs
hanging from the root path point to the front (feet within 45 degrees of the side axis are
needed). Smaller turns are not made.

Roles: a single chain is its id; a paired chain `<id>_l<i>` and `<id>_r<i>`, index 0 the
highest (then the frontmost).

Motion kinds (`settings` of a `procedural` motion; angles in degrees, distances in body
heights; `chains` and `legs` take chain ids or roles):

| Kind | Loops | Settings (default) |
|---|---|---|
| `breathe` | yes | `chains` (the trunk), `amplitude` 3, `sway` 2, `cycles` 1 |
| `hover` | yes | `chains` (the trunk), `bob` 0.03, `tilt` 2, `lean` 0, `sway` 4, `cycles` 1 |
| `undulate` | yes | `chains` (the chains toward back), `waves` 1, `amplitude` 18, `base` 0.3, `axis` `lateral` or `pitch`, `upper` 4, `arms` 6, `travel` 0, `cycles` 1 |
| `gait` | yes | `legs` (the chains toward down), `stride` (half the leg length), `lift` (0.18 of it), `duty` (0.6; 0.65 with more than two legs), `phases`, `bob` 0.015, `sway` 3, `arms` 10, `cycles` 1 |
| `strike` | no | `chains` (the side chains, else the trunk), `windup` 25, `reach` 55, `peak` 0.45, `lunge` 0.06, `trunk` 10 |
| `aim` | yes | `chains` (the holding chains, else the side chains), `pitch` 75, `trunk` 0, `breathe` 1 |
| `flinch` | no | `chains` (the trunk), `amplitude` 14, `peak` 0.25, `push` 0.03, `arms` 10 |
| `collapse` | no | `fall` (`back`, `front`, `left`, `right`), `tilt` 80, `lie` 0.06, `limp` 25, `end` 0.75 |

The trunk: chains from the root toward up or front and their single children; side
chains: toward side; legs: toward down. A side chain (an arm) is swung as a whole about its
first bone. A gait solves hip and knee exactly; bones below them keep their rest
orientation in the world; its stance frames are the clip's contacts and its `travel` the
distance the body covers per pass (as the motion library's). Only a `collapse` may bring the
body lower than the rest pose's lowest bone.

`<out>/clips.json` (`format` `forge-creature-clips`, `version` 1): `axes`, `roles`, and
`anims` `{name: {kind, fps, loop, stance ({role: frames}), travel ([side, forward] body
heights per pass), source, frames}}`, each frame `{"root": [side, forward, up] (body
heights), "root_turn": [w, x, y, z], "roles": {role: [[u, [w, x, y, z]], ...]}}`: per role
the chain's accumulated turn at the end of each of its bones, at `u` (0 to 1 along the
chain by length), in body axes, relative to the rest pose. The same document is the
`library` source of the motions list: a skeleton with the same roles plays it whatever its
bone counts (each bone takes the turn interpolated at its own position).

`<out>/<name>.glb`: mesh `body`, skinned (at most 4 influences, weights normalised), the
armature with the rig's bone names, the item bones `item` (at the tip of the role
`holds.right`, not deforming) with `item_ref` (the identity of item space, as the
humanoid's) and `item_l` (`holds.left`), and one animation per motion, named as the motion,
keyed at its frame times plus a closing key (the first frame again for a loop).

`<out>/model.json` (`format` `forge-creature`, `version` 1, `status` `pending review`):
`name`, `height`, `front`, `axes`, `size_m`, `center_m`, `plan` (the body plan used, a
template expanded), `roles`, `root`, `joints`, `missing_roles` (optional roles the map went
without), `twins`, `facing`, `unmapped_bones`, `bone_map`, `item_bones`,
`ends` (each role's tip bone), `animations` (per motion: `name`, `kind`, `source`,
`frames`, `fps`, `loop`, `seconds`, `holds`, `stance`, `travel`, `params` (the effective
settings, with the automatic ones: a gait's stride, lift, duty, phases, crouch; a
collapse's drop)), `clips`, `regions` (`mask` and the glow keys of the humanoid's
`regions`), `mesh`, `seconds_export`.

`<out>/regions_mask.png`: the painted texture's size; blue = what glows (as the humanoid's,
with `--glow-colours`), red and green 0. The GLB's material gets the emissive map as the
humanoid's.

`<out>/mesh_report.json` (`kind` `creature`): as the humanoid's without the normal map and
the bake source (`normal_map` is `null`).

`<out>/motion_report.json` (`forge-motion-report`, `kind` `creature`), every clip played
on the exported GLB loaded again:

| Measure | Meaning | Default threshold |
|---|---|---|
| `foot_slide_cm` | largest distance a contact role's tip bone head moves within a run of contact frames, with the clip's travel added (null without contacts) | 2 cm (`--max-foot-slide`) |
| `reach_shortfall_cm` | largest distance a gait's foot target lies beyond the leg's reach | 1 cm (`--max-reach`) |
| `stretch_p95` | 95th percentile over edges of `abs(length / rest length - 1)`, worst frame | 0.5 (`--max-stretch`) |
| `below_ground_cm` | depth of the deepest vertex below the ground, worst frame | 3 cm (`--max-below-ground`) |

## Layout sheet

`run_sheet` (the `layout_sheet` option of both recipes): `forge/sheet_layout.py` lays the
sheet out from each variant's `model.json` and reports; `tools/review_godot/sheet.gd`
(scene `sheet.tscn`) renders the cells it is given. Per variant: the header (label,
`pending review` or `FAILED: <reason>` with a red cross), the verdict line, the subject in
every direction of the review camera (a creature in its first `breathe` or `hover` clip,
at frame 0; an articulated object at rest) and one large front view, then one strip per
joint sweeping its range (fixed joints have none) or per clip, then the flag lines. The
layout file `<name>.png.json` (`forge-sheet-layout`): `size`, `camera`, `banner`,
`variants` (label, failed, reason, glb, kind, pose, verdict, flags, cells), `cells` (each:
`variant`, `kind` small, front or strip, `rect`, `yaw`, `pitch`, `ppm`, `target`, `anim`,
`time`, `pose` (joint values: node, kind, axis, value)), `texts`, `failures` and
`load_failures` (a GLB Godot could not load).

## Iso tiles

The `iso_tile` recipe's files ([order.md](order.md#the-iso-tile-recipe)); the geometry is
`tools/blender/iso_grid.py` (no `bpy`), the steps `forge/tile_steps.py`, the mesh render
`tools/blender/tile_render.py`.

**The grid.** A sprite cell is `cell` pixels (x right, y down, origin at its top-left). World:
X along the grid's x axis (to the lower right on screen), Y along its y axis (to the lower left),
Z up; one tile edge is 1 and one level `wall_aspect` tile edges. The floor's diamond has its top
corner at `floor_top`; a world point (X, Y, Z levels) lies at `floor_top + X (tw / 2, th / 2) +
Y (-tw / 2, th / 2) + Z (0, -level)`. The camera is orthographic, 45 degrees about the vertical,
`elevation` degrees down (30 for a 2:1 diamond); one level is `level` pixels, so a level is
`level / (tw / sqrt(2) cos(elevation))` tile edges high (sqrt(1.5) for the classic 32 x 16
diamond with 24-pixel levels). The map's frame is left-handed (y runs south): a glTF mesh
imported by Blender at (x, y, z) lies at map (x, -y, z).

**Faces.** A floor's texture: u along +X, v along +Y, its texel (0, 0) at the diamond's top
corner. A wall's texture is its face as seen, v = 0 at its top, always one tile edge wide and
one level high per tile; a west wall stands on X = 0 and shows its +X face, a north wall on
Y = 0 and shows its +Y face. A wall `thickness` thick has its face at that offset and also shows
its top and its open end (the west wall's at Y = 1, the north wall's at X = 1), textured with
the face's own top and end bands as deep as the wall is thick. A wall `height` levels high
shows the lower part of its texture. A texture over a variant grid covers the grid's tiles
along u (and v for a floor); a west wall's frames run from its +Y tile on the left of the
texture to its Y = 0 tile.

**Frames** (`v<seed>_sprite/`, copied to `final/`): per frame of the variant grid,
`<name>.png` (shaded: the albedo lit from its normals by the order's light, the key from the
upper left in front, the fill from the right, relative to a flat floor, times the relief's
hollows and a wall's darker foot, then brought to the original's mean brightness; a seam group's
face to its group's), `<name>_albedo.png` (unlit; its saturation brought part of the way to the
original's and sharpened at the frame's scale), `<name>_normal.png` (the sprite's view: x right, y up (`normal_y` `up`, green up, as
OpenGL and Godot; `down` flips it), z toward the viewer, as (n + 1) / 2) and, when anything
glows, `<name>_emissive.png` (the glowing texels' colour at full value, alpha their weight).
With a variant grid larger than one tile the names are `<name>_v<NN>`, NN = `(y % gy) * gx + (x %
gx)`. Every frame is the canvas (`rect`, default the cell) at `scale` art pixels per grid pixel;
its alpha is binary (floors and walls: each pixel belongs to the face whose quad holds its
centre, then the outline is grown `overlap` grid pixels, 0.75 by default, about round, the new
pixels taking the colour and normal of the nearest opaque ones, so neighbouring frames overlap
by about one and a half grid pixels and a frame drawn smaller with filtering leaves no dark line
between tiles; a face cut by its picture, a fence, is not grown) or smooth (objects, colours bled
from the opaque pixels into the soft edge). A face's normals come from its albedo's relief, the fine detail plus the
medium forms (planks, bricks, corrugation) at twice the weight, times `tile.relief`; in a seam
group's band the relief is the material's own. An object's come from its silhouette's dome and
its albedo's fine and medium detail. The frames of a block (an object over a variant grid larger
than one tile) each hold their tile's whole column, the floor diamond swept up the original's own
height plus 0.1 level, so neighbouring frames overlap where a nearer tile's column covers a
farther one (the same picture's pixels), and a frame drawn alone has no hole.

`tile.json` (`forge-tile`): `name`, `kind`, `variant_grid`, `scale`, `anchor` (the canvas's
top-left in grid pixels of the cell: [0, 0] for the cell), `size` (art pixels), `frames` (`index`,
`at` [i, j], `file`, `albedo`, `normal`, `emissive` or null, `glow`), `grid` (the full grid),
`overlap` (art pixels the outline was grown; 0 when it was not: a game that grows outlines itself
should skip a piece with `overlap` above 0), `measures`, `settings`, `form`. A face also writes `texture.png` (its final face texture).

`mesh_report.json` (`forge-tile-report`): `flags` (texts; [order.md](order.md#the-iso-tile-recipe)),
`measures` (`overflow`: the largest share of a frame outside the original's silhouette, widened
(an object made from its picture: of the whole picture against the original's block); `cover`:
the least share of the original's silhouette a frame covers (likewise); `seam`: how many times
more a face texture's opposite edges differ than neighbouring texels, the worse axis (a wall:
end to end only); `border_difference`: the outermost texel column's mean difference from the
group's material; `shading`: the albedo's brightness slope across a face or object that the
original's own lacks (`shading_original`), as a share of its mean, after `delight` for an
object (`shading_painted` before it; a meshed object: against its paint, `shading_paint`);
`exposure_gain`; `saturation_gain`; `fit` (an object's picture fitted to the original's
silhouette: `scale`, `move` in grid pixels, `iou_before`, `iou`); `ground_cut` (the share of an
object's cut taken out as its own ground, with `tile.ground` `cut`); for a meshed object
`render_fit`, `picture_on_mesh` (`iou`, `scale`, `move` of the picture laid on the render) and
`delight` (the gain of each side of the box it looks like); `busy_picture` and `busy` (a
repainted face of a seam group, or an object with `tile.match`: its fine detail's contrast over
its floor material's, before and after it was calmed); `repeat` (a repainted face over more than
one tile: the share of its forms its tiles have in common, 0 for unrelated tiles, 1 for
identical ones); `layout` (a repainted face: the mean colour distance of its broad forms from
its guide's, each without its mean colour); `overlap_px`) and `fit.excess` (each measure over
its limit, summed: the ranking of acceptable variants; `repeat` counts for a scattered floor
only).

**A meshed object's render** (`v<seed>_render/`): `albedo.png` (the base colour, unlit, on
the canvas at the scale), `normal.png` (the view normal, y up) and `fit.json`
(`forge-tile-render`: `fit` with `heading_deg` of eight, `stretch` (its height over its
footprint: 0.8, 0.9, 1, 1.12 or 1.25), `size`, `move` [X, Y] on the floor, `iou`, `cover` and
`overflow` of its silhouette against the original's at four samples per grid pixel; `rect`,
`scale`, `samples`). The mesh is turned by each heading and stretched by each factor, scaled so
its silhouette's box fits in the original's, its bottom centre placed on the original's; the
best overlap wins. Cycles renders on the CPU, emission only (no light), a box pixel filter.

**A seam group's material** (`<family>/_seam_<group>/`, `seamless` `cut`): `_seam_<group>.png`
(the seamless texture at the group's size), `_seam_<group>_tile2x2.png` (a preview) and
`_seam_<group>_colour.json` (`forge-tile-group-colour`: `saturation_gain`,
`original_luminance` {kind: the originals' mean brightness}, `wrap` {axis: `length`, `stretch`,
`cut_cost`} and `seam` {axis: the seam ratio of the result}).

**The map layout** (a family's `layout`, `forge-tile-layout`): `{"format": "forge-tile-layout",
"maps": [{"name", "size", "tiles": [{"at": [x, y, z], "floor": piece, "west_wall": piece,
"north_wall": piece, "object": piece}]}]}`; a part names a piece id of the family (others are
skipped and listed). The set sheet draws each map back to front (by level, then x + y, then x;
per tile its floor, west wall, north wall, object) three times: the originals (nearest), the
picked frames shaded, and their albedo lit by one light from their normal maps; a piece without
a pick is drawn as its original. `set_sheet.json` lists the maps, the pieces picked, not picked
and the ids skipped.
