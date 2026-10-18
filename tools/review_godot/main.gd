extends Node
## Review sheet of humanoid variants: every variant seen by the review camera (small, in
## every direction, orthographic) and from the front (large), an animated strip (frames of
## the walk, plus start and stop when the model has them) and the flags of its mesh and
## motion reports. Every candidate is labelled "pending review". Renders off screen and
## exits by itself.
##
## User arguments (after "--"):
##   --out <sheet.png>                 the sheet; <sheet.png>.json gets the layout
##   --variant <label>=<model.glb>     a variant (repeated, in order)
##   --variant <label>=!<reason>       a variant that failed: a failure mark in its place
##   --pitch <degrees>                 camera above the horizon (default 60)
##   --yaws <d,d,...>                  directions of the figure, degrees (default 0,45,...,315)
##   --figure <px>                     height of a standing figure in the small views (96)
##   --front <0|1>                     the large front view (1)
##   --front-height <px>               its figure height (default 3 x --figure)
##   --animation <name>                pose shown (default: the first idle, else the rest pose)
##   --strip-frames <n>                frames of each strip clip (8)
##   --strip-yaw <degrees>             direction of the strip (default: the second yaw)
## Next to each GLB it reads model.json (height, animation kinds), motion_report.json and
## mesh_report.json when present. Exit code 0 when the sheet was written.

const BACK := Color(0.12, 0.13, 0.15, 1.0)
const TEXT := Color(0.92, 0.93, 0.95)
const FLAG := Color(1.0, 0.72, 0.30)
const FAIL := Color(0.86, 0.16, 0.16, 1.0)
const PAD := 6
const LINE := 18

var _args := {}
var _variants: Array = []
var _vp: SubViewport
var _cam: Camera3D
var _pivot: Node3D


func _ready() -> void:
	var code := 1
	_parse_args()
	if not _args.has("out"):
		printerr("REVIEW: --out is required")
	elif _variants.is_empty():
		printerr("REVIEW: no --variant")
	else:
		code = await _run()
	get_tree().quit(code)


func _parse_args() -> void:
	var a := OS.get_cmdline_user_args()
	var i := 0
	while i < a.size():
		var k := a[i].trim_prefix("--")
		var v := ""
		if i + 1 < a.size() and not a[i + 1].begins_with("--"):
			v = a[i + 1]
			i += 1
		i += 1
		if k == "variant":
			var eq := v.find("=")
			var label := v.substr(0, eq) if eq >= 0 else v
			var what := v.substr(eq + 1) if eq >= 0 else "!no file"
			if what.begins_with("!") or what == "":
				_variants.append({"label": label, "failed": true, "reason": what.trim_prefix("!")})
			else:
				_variants.append({"label": label, "failed": false, "glb": what})
		else:
			_args[k] = v


func _read_json(path: String) -> Variant:
	if not FileAccess.file_exists(path):
		return null
	return JSON.parse_string(FileAccess.get_file_as_string(path))


func _load(v: Dictionary) -> void:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(v["glb"], state) != OK:
		v["failed"] = true
		v["reason"] = "cannot load " + str(v["glb"]).get_file()
		return
	var scene := doc.generate_scene(state) as Node3D
	if scene == null:
		v["failed"] = true
		v["reason"] = "empty GLB"
		return
	v["scene"] = scene
	var players := scene.find_children("*", "AnimationPlayer", true, false)
	v["player"] = players[0] if players.size() > 0 else null
	var dir := str(v["glb"]).get_base_dir()
	v["model"] = _read_json(dir.path_join("model.json"))
	v["motion"] = _read_json(dir.path_join("motion_report.json"))
	v["mesh"] = _read_json(dir.path_join("mesh_report.json"))
	var lo := INF
	var hi := -INF
	for mi: MeshInstance3D in scene.find_children("*", "MeshInstance3D", true, false):
		if mi.mesh == null or String(mi.name).begins_with("item_ref"):
			continue
		var box := mi.get_aabb()
		lo = minf(lo, box.position.y)
		hi = maxf(hi, box.end.y)
	v["height"] = (hi - lo) if hi > lo else 1.8
	if v["model"] is Dictionary and v["model"].has("height"):
		v["height"] = float(v["model"]["height"])


func _anim_of_kind(v: Dictionary, kind: String) -> String:
	var player: AnimationPlayer = v.get("player")
	if player == null:
		return ""
	if v.get("model") is Dictionary:
		for a: Dictionary in v["model"].get("animations", []):
			if str(a.get("kind", "")) == kind and player.has_animation(str(a["name"])):
				return str(a["name"])
	for n in player.get_animation_list():
		if String(n).begins_with(kind):
			return String(n)
	return ""


func _pose(v: Dictionary, anim: String, t: float) -> void:
	var player: AnimationPlayer = v.get("player")
	if player == null:
		return
	if anim == "" or not player.has_animation(anim):
		player.stop()
		for sk: Skeleton3D in (v["scene"] as Node3D).find_children("*", "Skeleton3D", true, false):
			sk.reset_bone_poses()
		return
	player.play(anim)
	player.seek(t, true)
	player.pause()


func _setup_viewport() -> void:
	_vp = SubViewport.new()
	_vp.transparent_bg = true
	_vp.own_world_3d = true
	_vp.msaa_3d = Viewport.MSAA_4X
	_vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	var env := Environment.new()
	env.background_mode = Environment.BG_CLEAR_COLOR
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color.WHITE
	env.ambient_light_energy = 0.55
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	_cam = Camera3D.new()
	_cam.projection = Camera3D.PROJECTION_ORTHOGONAL
	_cam.keep_aspect = Camera3D.KEEP_HEIGHT
	_cam.environment = env
	_cam.near = 0.05
	_cam.far = 100.0
	_vp.add_child(_cam)
	var key := DirectionalLight3D.new()
	key.light_energy = 0.9
	key.rotation_degrees = Vector3(-50, 35, 0)
	_vp.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.light_energy = 0.35
	fill.rotation_degrees = Vector3(-20, -140, 0)
	_vp.add_child(fill)
	_pivot = Node3D.new()
	_vp.add_child(_pivot)
	add_child(_vp)


func _render(v: Dictionary, size: Vector2i, px_per_m: float, pitch: float, yaw: float,
		anim: String, t: float) -> Image:
	var scene: Node3D = v["scene"]
	if scene.get_parent() != _pivot:
		if scene.get_parent():
			scene.get_parent().remove_child(scene)
		_pivot.add_child(scene)
	_pivot.rotation_degrees = Vector3(0, yaw, 0)
	_vp.size = size
	_cam.size = size.y / px_per_m
	var target := Vector3(0, float(v["height"]) * 0.5, 0)
	var p := deg_to_rad(pitch)
	var back := Vector3(0, sin(p), cos(p))
	_cam.position = target + back * 20.0
	var up := Vector3.UP if absf(pitch) < 89.0 else Vector3(0, 0, -1)
	_cam.look_at(target, up)
	_pose(v, anim, t)
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var img := _vp.get_texture().get_image()
	img.convert(Image.FORMAT_RGBA8)
	return img


func _text(s: String, color: Color, size := 13) -> Image:
	var vp := SubViewport.new()
	vp.transparent_bg = true
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	var label := Label.new()
	label.text = s
	label.add_theme_font_size_override("font_size", size)
	label.add_theme_color_override("font_color", color)
	vp.add_child(label)
	add_child(vp)
	var font := ThemeDB.fallback_font
	var w := int(ceil(font.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)) + 4
	vp.size = Vector2i(maxi(w, 4), LINE)
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var img := vp.get_texture().get_image()
	img.convert(Image.FORMAT_RGBA8)
	vp.queue_free()
	return img


func _flags(v: Dictionary) -> Array[String]:
	var out: Array[String] = []
	var mesh = v.get("mesh")
	if mesh is Dictionary:
		for f in mesh.get("flags", []):
			out.append("mesh: " + str(f))
	else:
		out.append("mesh: no report")
	var motion = v.get("motion")
	if motion is Dictionary:
		# One line per measure: how many clips are over its threshold and the worst one.
		var by: Dictionary = {}
		for f: Dictionary in motion.get("flags", []):
			var k := str(f.get("measure"))
			if not by.has(k):
				by[k] = {"n": 0, "worst": null, "clip": "", "threshold": f.get("threshold")}
			by[k]["n"] += 1
			var val = f.get("value")
			if val != null and (by[k]["worst"] == null or float(val) > float(by[k]["worst"])):
				by[k]["worst"] = val
				by[k]["clip"] = str(f.get("clip"))
		for k in by:
			var e: Dictionary = by[k]
			if e["worst"] == null:
				out.append("motion: %s in %d clip(s)" % [k, e["n"]])
			else:
				out.append("motion: %s over %s in %d clip(s), worst %s %.2f" % [k, str(e["threshold"]), e["n"],
					e["clip"], float(e["worst"])])
	else:
		out.append("motion: no report")
	if out.is_empty():
		out.append("no flags")
	return out


func _cross(img: Image, r: Rect2i) -> void:
	img.fill_rect(r, Color(0.25, 0.05, 0.05, 0.85))
	var n := maxi(r.size.x, r.size.y)
	for i in n:
		var x := r.position.x + int(float(i) * r.size.x / n)
		var dy := int(float(i) * r.size.y / n)
		for d in range(-2, 3):
			for yy in [r.position.y + dy + d, r.end.y - 1 - dy + d]:
				if yy >= r.position.y and yy < r.end.y:
					img.set_pixel(x, yy, FAIL)


func _run() -> int:
	var pitch := float(_args.get("pitch", "60"))
	var yaws: Array[float] = []
	for s in str(_args.get("yaws", "0,45,90,135,180,225,270,315")).split(","):
		if s.strip_edges() != "":
			yaws.append(float(s))
	var fig := int(_args.get("figure", "96"))
	var front := str(_args.get("front", "1")) != "0"
	var front_h := int(_args.get("front-height", str(fig * 3)))
	var strip_n := int(_args.get("strip-frames", "8"))
	var strip_yaw := float(_args.get("strip-yaw", str(yaws[1] if yaws.size() > 1 else yaws[0])))
	_setup_viewport()
	var h_max := 0.0
	for v: Dictionary in _variants:
		if not v["failed"]:
			_load(v)
		if not v["failed"]:
			h_max = maxf(h_max, float(v["height"]))
	if h_max <= 0.0:
		h_max = 1.8
	# Cells fit a standing figure seen from the pitch (its footprint and a stride included).
	var p := deg_to_rad(pitch)
	var small := Vector2i(int(ceil(fig * 0.9)), int(ceil(fig * (absf(cos(p)) + 0.6 * absf(sin(p))) * 1.12)) + 4)
	var large := Vector2i(int(ceil(front_h * 0.75)), int(ceil(front_h * 1.1)))
	var ppm_small := fig / h_max
	var ppm_large := front_h / h_max
	# Lay out every block first, then render into one image.
	var blocks: Array = []
	var width := 0
	var y := PAD
	for v: Dictionary in _variants:
		var b := {"label": v["label"], "failed": v["failed"], "reason": v.get("reason", ""), "cells": [],
			"texts": [], "flags": []}
		var x := PAD
		b["texts"].append({"at": Vector2i(x, y), "text": "%s   %s" % [v["label"],
			("FAILED: " + str(v.get("reason", ""))) if v["failed"] else "pending review"],
			"color": FAIL if v["failed"] else TEXT, "size": 14})
		y += LINE + 2
		if v["failed"]:
			var w := yaws.size() * (small.x + PAD) - PAD
			var r := Rect2i(x, y, maxi(w, small.x), small.y)
			b["cells"].append({"kind": "failure", "rect": r})
			x = r.end.x + PAD
			y += small.y + PAD
		else:
			var anim := str(_args.get("animation", ""))
			if anim == "":
				anim = _anim_of_kind(v, "idle")
			b["pose"] = anim if anim != "" else "rest"
			var row_h := small.y
			for yaw in yaws:
				b["cells"].append({"kind": "small", "yaw": yaw, "rect": Rect2i(x, y, small.x, small.y),
					"anim": anim, "time": 0.0, "pitch": pitch, "ppm": ppm_small})
				x += small.x + PAD
			if front:
				b["cells"].append({"kind": "front", "yaw": 0.0, "rect": Rect2i(x, y, large.x, large.y),
					"anim": anim, "time": 0.0, "pitch": 0.0, "ppm": ppm_large})
				x += large.x + PAD
				row_h = maxi(row_h, large.y)
			width = maxi(width, x)
			y += row_h + PAD
			for kind in ["walk", "start", "stop"]:
				var clip := _anim_of_kind(v, kind)
				if clip == "":
					continue
				var player: AnimationPlayer = v["player"]
				var length := player.get_animation(clip).length
				b["texts"].append({"at": Vector2i(PAD, y), "text": "%s (%s)" % [kind, clip], "color": TEXT, "size": 12})
				y += LINE
				x = PAD
				for i in strip_n:
					var t := length * i / float(strip_n)
					b["cells"].append({"kind": "strip", "clip": clip, "yaw": strip_yaw, "time": t,
						"rect": Rect2i(x, y, small.x, small.y), "anim": clip, "pitch": pitch, "ppm": ppm_small})
					x += small.x + PAD
				width = maxi(width, x)
				y += small.y + PAD
			for f in _flags(v):
				b["texts"].append({"at": Vector2i(PAD, y), "text": f, "color": FLAG, "size": 12})
				b["flags"].append(f)
				y += LINE
		width = maxi(width, x)
		y += PAD * 2
		blocks.append(b)
	var sheet := Image.create(maxi(width, 64), y, false, Image.FORMAT_RGBA8)
	sheet.fill(Color(0, 0, 0, 0))
	var layout := {"size": [sheet.get_width(), sheet.get_height()], "status": "pending review", "variants": []}
	for i in blocks.size():
		var b: Dictionary = blocks[i]
		var v: Dictionary = _variants[i]
		var lv := {"label": b["label"], "failed": b["failed"], "reason": b["reason"], "pose": b.get("pose", ""),
			"flags": b["flags"], "cells": []}
		for c: Dictionary in b["cells"]:
			var r: Rect2i = c["rect"]
			if c["kind"] == "failure":
				_cross(sheet, r)
			else:
				var img := await _render(v, r.size, c["ppm"], c["pitch"], c["yaw"], c["anim"], c["time"])
				sheet.blend_rect(img, Rect2i(Vector2i.ZERO, img.get_size()), r.position)
			lv["cells"].append({"kind": c["kind"], "yaw": c.get("yaw", 0.0), "clip": c.get("clip", c.get("anim", "")),
				"time": c.get("time", 0.0), "rect": [r.position.x, r.position.y, r.size.x, r.size.y]})
		for t: Dictionary in b["texts"]:
			var img := await _text(t["text"], t["color"], t["size"])
			var at: Vector2i = t["at"]
			var band := Rect2i(at, Vector2i(mini(img.get_width(), sheet.get_width() - at.x), img.get_height()))
			sheet.fill_rect(band, BACK)
			sheet.blend_rect(img, Rect2i(Vector2i.ZERO, band.size), at)
		layout["variants"].append(lv)
	var out := str(_args["out"])
	DirAccess.make_dir_recursive_absolute(out.get_base_dir())
	if sheet.save_png(out) != OK:
		printerr("REVIEW: cannot write ", out)
		return 1
	var f := FileAccess.open(out + ".json", FileAccess.WRITE)
	f.store_string(JSON.stringify(layout, " "))
	f.close()
	print("REVIEW: sheet ", sheet.get_size(), " ", out)
	for v: Dictionary in _variants:
		if v.has("scene"):
			(v["scene"] as Node3D).queue_free()
	return 0
