# Conventions of the local tools

The formats the `humanoid` tools read and write: the motion library and the held-item
socket file a game supplies, and what the exporter (`tools/blender/humanoid_export.py`)
and the review sheet (`tools/review_godot/`) produce. The runners are in
`forge/local_gpu.py`; the synthetic examples are in `tests/fixtures/humanoid/`. The
formats of the `articulated`, `creature` and `held_item` tools follow in
[their own sections](#articulated-parts).

## Axes and units

- **Exported model:** metres, glTF axes (Y up). The figure faces glTF +Z, stands on the
  ground (y = 0) with its hips over the origin, and is `--height` metres tall at rest
  (`model.json` `height`, the height of the rest mesh after any reduction). A rig that faced
  another way (the front the bone map finds from the feet) is turned about the vertical
  first, so `model.json` `front` is always `[0, 0, 1]`.
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

## Held item

`tools/blender/held_item_export.py` (run by `run_item`, the held-item recipe's `item` stage;
the geometry in `tools/blender/held_item_geometry.py`, no `bpy`). It reads the painted mesh
(glTF axes) and writes the item in item space ([above](#axes-and-units)).

The frame: the generated front points along the glTF axis `--front` (default `+x`, the right
of the recipe's side-view concept) and its top along `--top` (default `+y`). A long, straight
item (the template's `square`, or `--snap 1`) is turned so that its longest principal axis
lies on +x, then rolled so that its second axis lies on +z, each only when the turn is 8 to
30 degrees and the item is elongated (spread ratios 1.3 and 1.2); an item whose longest axis
is further than 30 degrees from +x with a ratio of at least 1.5 gets the flag `frame: ...`.
Then it is scaled to `--length` (its extent along +x) or `--height` (along +z), and moved so
that its grip is the origin.

The sockets: the class template merged with the order's `item.sockets`
([order.md](order.md#the-held-item-recipe)), placed on surface samples (20 000 points spread
by area with a fixed seed, plus the vertices): `section` takes the points within 3 % of the
length of station `u` (6 %, when fewer than 6 points are there) whose height is inside
`band`, and puts the socket at that station, in the middle of their extent across and up;
`surface` takes the closest point; `none` the template position. Flags: `socket <name> is
missing: no geometry at its station` (a required socket), `socket <name> is off the mesh:
<d> cm from the surface and outside its cross-section` (farther than `--tolerance-cm` from
every surface point and outside the extent, widened by the tolerance, of the points of its
own station).

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
  `required`, `found`, `shift_m` (how far the snap moved it), `points`), `required`, `frame`
  (`source` `order` or `manual`, `front_given`, `top_given`, `square` (`turned_deg`,
  `length_deg`, `roll_deg`, `ratio`, `squared`, `flag`), `scale`), `sockets_source`, `fit`,
  `mesh`, `seconds_export`. It has no top-level `class` and `sockets`, so the humanoid
  exporter never reads it as a socket file.
- `frame.json` (`forge-item-frame`): `front`, `top`, `snap`, `turned_deg`, `square`, `scale`,
  `size_m`, `manual`. With `"manual": true` it is read and kept as written: `front`, `top`,
  `snap`, and `rotate_deg` `[about x, about y, about z]` (degrees, applied in that order in item
  axes) instead of the squaring.
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
`parts` (per part: `parent`, `node`, `triangles`, `cut_triangles`, `open_edges`, `share` of
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
`part <id> is empty: its region holds no geometry`, `part <id> is tiny: ...`, `part <id> has
<n> open edges after its cut was closed`, the material slot flags. No UV layer or no
base-colour texture stops the tool (exit code 4).

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
`joints` (per role with joints: `[{bone, joint, bend}]`), `unmapped`, `split` (a chain taken
from the end of its parent), `notes`, `front`, `left`. The precedence and the manual rule
are the humanoid's; an invalid map stops the export (exit code 3).

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
template expanded), `roles`, `root`, `joints`, `unmapped_bones`, `bone_map`, `item_bones`,
`ends` (each role's tip bone), `animations` (per motion: `name`, `kind`, `source`,
`frames`, `fps`, `loop`, `seconds`, `holds`, `stance`, `travel`, `params` (the effective
settings, with the automatic ones: a gait's stride, lift, duty, phases, crouch; a
collapse's drop)), `clips`, `mesh`, `seconds_export`.

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
