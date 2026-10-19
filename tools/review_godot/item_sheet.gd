extends Node
## Renders the held-item recipe's review sheet laid out by the factory
## (forge/item_sheet_layout.py): cells of the item alone (subject "item") and of the reference
## figure holding it (subject "figure": the hold stage's GLB with the item GLB put under its
## item_ref node, the identity of item space on the item bone), socket marks and text lines,
## into one PNG. Off screen; exits by itself.
##
## User arguments (after "--"):
##   --request <request.json>   the layout with "out" (the PNG to write), "size", "variants"
##                              ([{"label", "glb", "figure", "failed"}]), "cells", "marks",
##                              "failures", "texts"
## A cell: {"variant", "subject", "rect": [x, y, w, h], "yaw", "pitch", "ppm" (pixels per
## metre), "target" ([x, y, z] the camera looks at), "anim" (a clip of the figure, or ""),
## "time" (seconds)}. A mark: {"at": [x, y], "r", "color"}.
## Prints "SHEET: failed <label> <reason>" for a GLB it cannot load (its cells get a cross).
## Exit code 0 when the sheet was written.

const BACK := Color(0.12, 0.13, 0.15, 1.0)
const COLORS := {"text": Color(0.92, 0.93, 0.95), "flag": Color(1.0, 0.72, 0.30),
	"fail": Color(0.86, 0.16, 0.16, 1.0), "pass": Color(0.45, 0.85, 0.50), "socket": Color(0.25, 0.85, 1.0)}
const LINE := 18

var _vp: SubViewport
var _cam: Camera3D
var _pivot: Node3D
var _scenes := {}


func _ready() -> void:
	var code := 1
	var args := OS.get_cmdline_user_args()
	var path := ""
	for i in args.size():
		if args[i] == "--request" and i + 1 < args.size():
			path = args[i + 1]
	if path == "" or not FileAccess.file_exists(path):
		printerr("SHEET: --request <file> is required")
	else:
		var req = JSON.parse_string(FileAccess.get_file_as_string(path))
		if req is Dictionary:
			code = await _run(req)
		else:
			printerr("SHEET: the request is not a JSON object")
	get_tree().quit(code)


func _setup() -> void:
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
	_cam.far = 200.0
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


func _scene_of(path: String, label: String) -> Node3D:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(path, state) != OK:
		print("SHEET: failed ", label, " cannot load ", path.get_file())
		return null
	var scene := doc.generate_scene(state) as Node3D
	if scene == null:
		print("SHEET: failed ", label, " empty GLB ", path.get_file())
	return scene


func _entry(scene: Node3D) -> Dictionary:
	var rest := {}
	for n: Node in scene.find_children("*", "Node3D", true, false):
		rest[n] = (n as Node3D).transform
	var players := scene.find_children("*", "AnimationPlayer", true, false)
	return {"scene": scene, "rest": rest, "player": players[0] if players.size() > 0 else null}


func _load(index: int, v: Dictionary) -> void:
	if bool(v.get("failed", false)) or v.get("glb") == null:
		return
	var label := str(v.get("label", "?"))
	var item := _scene_of(str(v["glb"]), label)
	if item == null:
		return
	_scenes["item%d" % index] = _entry(item)
	if v.get("figure") == null:
		return
	var figure := _scene_of(str(v["figure"]), label)
	if figure == null:
		return
	var refs := figure.find_children("item_ref*", "Node3D", true, false)
	if refs.is_empty():
		print("SHEET: failed ", label, " the figure has no item_ref")
		return
	var held := _scene_of(str(v["glb"]), label)
	if held != null:
		(refs[0] as Node3D).add_child(held)
	_scenes["figure%d" % index] = _entry(figure)


func _attach(s: Dictionary) -> void:
	var scene: Node3D = s["scene"]
	for c in _pivot.get_children():
		if c != scene:
			_pivot.remove_child(c)
	if scene.get_parent() != _pivot:
		_pivot.add_child(scene)


func _pose(s: Dictionary, cell: Dictionary) -> void:
	for n in s["rest"]:
		(n as Node3D).transform = s["rest"][n]
	var player: AnimationPlayer = s["player"]
	var anim := str(cell.get("anim", ""))
	if player != null:
		if anim != "" and player.has_animation(anim):
			player.play(anim)
			player.seek(float(cell.get("time", 0.0)), true)
			player.pause()
		else:
			player.stop()
			for sk: Skeleton3D in (s["scene"] as Node3D).find_children("*", "Skeleton3D", true, false):
				sk.reset_bone_poses()


func _render(s: Dictionary, cell: Dictionary) -> Image:
	_attach(s)
	var r: Array = cell["rect"]
	var size := Vector2i(int(r[2]), int(r[3]))
	_pivot.rotation_degrees = Vector3(0, float(cell.get("yaw", 0.0)), 0)
	_vp.size = size
	_cam.size = size.y / maxf(float(cell.get("ppm", 50.0)), 0.001)
	var t: Array = cell.get("target", [0, 1, 0])
	var target := Vector3(float(t[0]), float(t[1]), float(t[2]))
	target = Basis(Vector3.UP, deg_to_rad(float(cell.get("yaw", 0.0)))) * target
	var p := deg_to_rad(float(cell.get("pitch", 0.0)))
	_cam.position = target + Vector3(0, sin(p), cos(p)) * 50.0
	_cam.look_at(target, Vector3.UP if absf(rad_to_deg(p)) < 89.0 else Vector3(0, 0, -1))
	_pose(s, cell)
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var img := _vp.get_texture().get_image()
	img.convert(Image.FORMAT_RGBA8)
	return img


func _text(s: String, color: Color, size: int) -> Image:
	var vp := SubViewport.new()
	vp.transparent_bg = true
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	var label := Label.new()
	label.text = s
	label.add_theme_font_size_override("font_size", size)
	label.add_theme_color_override("font_color", color)
	vp.add_child(label)
	add_child(vp)
	var w := int(ceil(ThemeDB.fallback_font.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x * 1.1)) + 8
	vp.size = Vector2i(maxi(w, 4), LINE)
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var img := vp.get_texture().get_image()
	img.convert(Image.FORMAT_RGBA8)
	vp.queue_free()
	return img


func _cross(img: Image, r: Rect2i) -> void:
	img.fill_rect(r, Color(0.25, 0.05, 0.05, 0.85))
	var n := maxi(r.size.x, r.size.y)
	for i in n:
		var x := r.position.x + int(float(i) * r.size.x / n)
		var dy := int(float(i) * r.size.y / n)
		for d in range(-2, 3):
			for yy in [r.position.y + dy + d, r.end.y - 1 - dy + d]:
				if yy >= r.position.y and yy < r.end.y:
					img.set_pixel(x, yy, COLORS["fail"])


func _mark(img: Image, at: Vector2i, radius: int, color: Color) -> void:
	for dy in range(-radius - 1, radius + 2):
		for dx in range(-radius - 1, radius + 2):
			var x := at.x + dx
			var y := at.y + dy
			if x < 0 or y < 0 or x >= img.get_width() or y >= img.get_height():
				continue
			var d := sqrt(float(dx * dx + dy * dy))
			if d <= radius - 1:
				img.set_pixel(x, y, color)
			elif d <= radius + 1:
				img.set_pixel(x, y, BACK)


func _rect(a: Array) -> Rect2i:
	return Rect2i(int(a[0]), int(a[1]), int(a[2]), int(a[3]))


func _run(req: Dictionary) -> int:
	_setup()
	var variants: Array = req.get("variants", [])
	for i in variants.size():
		_load(i, variants[i])
	var size: Array = req.get("size", [64, 64])
	var sheet := Image.create(maxi(int(size[0]), 64), maxi(int(size[1]), 16), false, Image.FORMAT_RGBA8)
	sheet.fill(Color(0, 0, 0, 0))
	for f: Array in req.get("failures", []):
		_cross(sheet, _rect(f))
	for cell: Dictionary in req.get("cells", []):
		var r := _rect(cell["rect"])
		var key := "%s%d" % [str(cell.get("subject", "item")), int(cell.get("variant", 0))]
		if not _scenes.has(key):
			_cross(sheet, r)
			continue
		var img := await _render(_scenes[key], cell)
		sheet.blend_rect(img, Rect2i(Vector2i.ZERO, img.get_size()), r.position)
	for m: Dictionary in req.get("marks", []):
		var at: Array = m.get("at", [0, 0])
		var color: Color = COLORS.get(str(m.get("color", "socket")), COLORS["socket"])
		_mark(sheet, Vector2i(int(at[0]), int(at[1])), int(m.get("r", 5)), color)
	for t: Dictionary in req.get("texts", []):
		var color: Color = COLORS.get(str(t.get("color", "text")), COLORS["text"])
		var img := await _text(str(t["text"]), color, int(t.get("size", 13)))
		var at := Vector2i(int(t["at"][0]), int(t["at"][1]))
		var band := Rect2i(at, Vector2i(mini(img.get_width(), sheet.get_width() - at.x), img.get_height()))
		if band.size.x <= 0:
			continue
		sheet.fill_rect(band, BACK)
		sheet.blend_rect(img, Rect2i(Vector2i.ZERO, band.size), at)
	var out := str(req.get("out", ""))
	DirAccess.make_dir_recursive_absolute(out.get_base_dir())
	if sheet.save_png(out) != OK:
		printerr("SHEET: cannot write ", out)
		return 1
	print("SHEET: written ", sheet.get_size(), " ", out)
	for k in _scenes:
		(_scenes[k]["scene"] as Node3D).queue_free()
	return 0
