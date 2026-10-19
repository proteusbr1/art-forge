# Motion bench

A tool to refine a motion library (`inputs.clips`, [conventions](conventions.md#motion-library-inputsclips))
clip by clip on one figure holding one item, by looking at it from the game's camera. It runs
the humanoid exporter and a short off-screen Godot render; it takes no GPU lock and sends
nothing to ComfyUI.

    python -m forge.bench --rig RIG.glb --clips LIBRARY.json --out DIR
        [--items DIR [--item NAME]] [--only CLIP[,CLIP...]] [--height 1.8]
        [--yaws 45,-90,-135] [--pitch 30] [--game-px 115] [--close-px 440] [--close-yaw -35]
        [--format webp|gif] [--compare PREVIOUS_DIR] [--title TEXT] [--keep-frames]
        [--config forge.local.json]

- `--rig`: a rigged GLB (the rig stage's), or a figure the exporter already wrote (its item
  bones and animations are removed first, `tools/blender/held_item_figure.py`).
- `--only`: clip names or shell patterns (`"*_carbine*"`), in the order given; default every
  clip. `<out>/clips.json` is the library with those clips and its `holds`.
- `--items` with `--item`: only that item (`<name>.glb`, `<name>.json`) is copied to
  `<out>/items/`, so its class's sockets are its own; `--items` alone passes the folder.
- `--compare`: a previous bench folder; its previews and measures are shown under each clip.
- `forge.local.json` must point to `blender_python` and `godot`.

## What it writes

- `<out>/export/`: the exporter's `figure.glb`, `model.json`, `motion_report.json`,
  `mesh_report.json` (CPU, the mesh not reduced, a 256-pixel normal map).
- `<out>/<clip>.webp` (or `.gif`): every frame, the views side by side: a close view
  (`--close-yaw`, 12 degrees above the horizon, the figure `--close-px` tall), the game's
  orthographic camera (`--pitch` degrees above the horizon) at twice the game size for each
  of `--yaws`, and under those the game size (the standing figure `--game-px` tall) in the
  eight directions. Yaw 0 looks at the figure's front; + goes round to its left. A checkered
  ground moves back by the clip's `travel`, so a planted foot keeps its square.
- `<out>/<clip>.png`: eight frames spread over the clip, at half size.
- `<out>/index.html`: one row per clip: name, kind, hold kind, follow mode and hands; the
  preview; the motion report's measures (over their thresholds in orange) and the bench's own:
  - `item yaw` and `item pitch`: where the held item's +x points over the clip, from the
    figure's front (degrees, yaw + to its left, pitch + up);
  - `ankle gap min`: the smallest distance across between the left and the right ankle
    (cm; below 0 the legs cross);
  - `planted foot slide`: the largest slide of a planted foot in the ground's frame (an ankle
    within 0.5 % of the height of its lowest for 3 frames or more, the first and last frame
    of such a run left out, the clip's travel added). It needs no declared `stance`; the
    motion report's `foot_slide_cm` measures the declared stance frames.
- `<out>/bench.json` (`forge-bench`): the views, the rows, the timings.
- `--keep-frames` keeps Godot's PNG frames in `<out>/frames/<clip>/`.

On a 23 000-triangle figure the export and the render of eight clips took about 75 seconds
(about 10 seconds a clip).

## Notes from refining a held long gun

- Placements first: `follow: chest` with the `butt` socket on the shoulder pocket (`at`) and
  both hands by IK (`hands`) gives the item in both hands in every clip that names the hold.
- The arms' reach decides the placement. Measure the shoulder-to-socket distance against the
  arm (upper arm plus forearm) before anything else; a support socket far along the item is
  out of reach of short-armed or bulky figures, and the IK leaves the wrist short of it
  (`hand_socket_cm`, `reach_shortfall_cm`).
- The retarget keeps only each spine and head bone's swing: a twist of the chest or the head
  about its own axis is lost (the pelvis keeps its whole turn from the hips' line). A turned
  (bladed) stance comes from turning the pelvis; the collars follow the shoulders' points.
  `chest` placements turn with the chest, so a placement made on the rest body is turned back
  by the clip's own turn (its `point`) to aim where the feet face; the `item yaw` measure
  shows the result.
- A clip built from the figure's own rest joints (read from the exported GLB's inverse bind
  matrices, `forge/body.py` `joint_positions`) lands where it says: `feet: clip` puts the
  ankles exactly there.
  The head and the hands point along their bones' own rest axes, not straight up or along the
  forearm.
