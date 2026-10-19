# Conventions of the humanoid tools

The formats the `humanoid` tools read and write: the motion library and the held-item
socket file a game supplies, and what the exporter (`tools/blender/humanoid_export.py`)
and the review sheet (`tools/review_godot/`) produce. The runners are in
`forge/local_gpu.py`; the synthetic examples are in `tests/fixtures/humanoid/`.

## Axes and units

- **Exported model:** metres, glTF axes (Y up). The figure faces glTF +Z, stands on the
  ground (y = 0) with its hips over the origin, and is `--height` metres tall at rest
  (`model.json` `height`, the height of the rest mesh after any reduction).
- **Body coordinates** (motion library and hold placements): `[side, forward, up]` in
  body heights; side + is the figure's left, the ground is up = 0, the pelvis stands over
  side = forward = 0.
- **Item space** (held items): metres, +x is the item's front (a muzzle, a blade's tip),
  +z its top, +y = z x x; the grip socket is the origin.

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

## Held-item socket file (`inputs.items`)

A folder with one `<name>.json` per item, next to its `<name>.glb` (item space):

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
counts, `glow_hues`), `seconds_export`.

`<out>/regions_mask.png`: the painted texture's size; red = skin (warm, moderately
saturated colour on the head), green = hair (darker warm colour on the top of the head),
blue = what glows (bright saturated colour in the `--glow-hues` range, only with
`--glow`). A game can recolour these regions; the factory does not.

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
sheet says `NO VARIANT PASSED: MOST OFTEN THE <STAGE> (<n> OF <m>)`.

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
