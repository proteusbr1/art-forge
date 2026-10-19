extends Node
## The motion bench's renderer (forge/bench.py): an exported humanoid holding an item, every
## frame of the requested clips, seen from several views at once (one SubViewport per view, all
## sharing one world), composed into one PNG per frame. A checkered ground under the figure
## moves back by the clip's travel, so a planted foot stays on its square. Off screen; exits by
## itself.
##
## User arguments (after "--"):
##   --request <request.json>
## The request: "out" (folder), "figure" (GLB), "item" (GLB or null: put under the figure's
## item_ref node), "height" (metres), "size" ([w, h] of a frame), "background" ([r, g, b]),
## "views" ([{"name", "rect": [x, y, w, h], "yaw" (degrees, the camera around the figure: 0 in
## front of it, positive towards its left), "pitch" (degrees above the horizon), "ppm" (pixels
## per metre), "target" ([x, y, z] metres)}]) and "clips" ([{"name", "frames", "fps",
## "travel": [side, forward] body heights per pass, or null}]).
## Writes <out>/<clip>/<frame>.png and <out>/render.json: per clip "frames" written, "missing"
## (the GLB has no such animation), "item": the held item's +x per frame in the figure's
## frame ("yaw_deg": positive towards the figure's left, "pitch_deg": up), null when the item
## is hidden, and "ankles": per frame the ankles' [side, forward, up] in metres (the LeftFoot
## and RightFoot joints; side + is the figure's left), null without those bones. Exit code 0
## when every clip was rendered or reported missing.

const GROUND_CELL := 0.25
const KEY_LIGHT := Vector3(-50, -35, 0)

var _views := []
var _world: World3D
var _root: Node3D
var _ground: MeshInstance3D


func _ready() -> void:
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	Engine.max_fps = 0
	var code := 1
	var args := OS.get_cmdline_user_args()
	var path := ""
	for i in args.size():
		if args[i] == "--request" and i + 1 < args.size():
			path = args[i + 1]
	if path == "" or not FileAccess.file_exists(path):
		printerr("BENCH: --request <file> is required")
	else:
		var req = JSON.parse_string(FileAccess.get_file_as_string(path))
		if req is Dictionary:
			code = await _run(req)
		else:
			printerr("BENCH: the request is not a JSON object")
	get_tree().quit(code)


func _load(path: String) -> Node3D:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(path, state) != OK:
		printerr("BENCH: cannot load ", path)
		return null
	return doc.generate_scene(state) as Node3D


func _checker() -> ImageTexture:
	var img := Image.create(64, 64, true, Image.FORMAT_RGB8)
	for y in 64:
		for x in 64:
			var odd := ((x / 32) + (y / 32)) % 2 == 1
			var c := Color(0.33, 0.34, 0.33) if odd else Color(0.40, 0.41, 0.40)
			if x % 32 == 0 or y % 32 == 0:
				c = Color(0.28, 0.29, 0.28)
			img.set_pixel(x, y, c)
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


func _setup(req: Dictionary) -> void:
	_world = World3D.new()
	var env := Environment.new()
	var bg: Array = req.get("background", [0.15, 0.16, 0.18])
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(float(bg[0]), float(bg[1]), float(bg[2]))
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color.WHITE
	env.ambient_light_energy = 0.6
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	_world.environment = env
	var first: SubViewport = null
	for v: Dictionary in req.get("views", []):
		var r: Array = v["rect"]
		var vp := SubViewport.new()
		vp.size = Vector2i(int(r[2]), int(r[3]))
		vp.world_3d = _world
		vp.msaa_3d = Viewport.MSAA_4X
		vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
		var cam := Camera3D.new()
		cam.projection = Camera3D.PROJECTION_ORTHOGONAL
		cam.keep_aspect = Camera3D.KEEP_HEIGHT
		cam.near = 0.05
		cam.far = 100.0
		cam.size = float(r[3]) / maxf(float(v.get("ppm", 50.0)), 0.001)
		vp.add_child(cam)
		add_child(vp)
		var t: Array = v.get("target", [0, 1, 0])
		var target := Vector3(float(t[0]), float(t[1]), float(t[2]))
		var yaw := deg_to_rad(float(v.get("yaw", 0.0)))
		var p := deg_to_rad(float(v.get("pitch", 0.0)))
		cam.position = target + Vector3(sin(yaw) * cos(p), sin(p), cos(yaw) * cos(p)) * 30.0
		cam.look_at(target, Vector3.UP)
		_views.append({"vp": vp, "rect": Rect2i(int(r[0]), int(r[1]), int(r[2]), int(r[3]))})
		if first == null:
			first = vp
	# The scenery lives in the first viewport's tree; every viewport shares its world.
	_root = Node3D.new()
	first.add_child(_root)
	var key := DirectionalLight3D.new()
	key.light_energy = 1.0
	key.rotation_degrees = KEY_LIGHT
	key.shadow_enabled = true
	key.directional_shadow_max_distance = 12.0
	_root.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.light_energy = 0.35
	fill.rotation_degrees = Vector3(-20, 150, 0)
	_root.add_child(fill)
	if bool(req.get("ground", true)):
		var plane := PlaneMesh.new()
		plane.size = Vector2(16, 16)
		var mat := StandardMaterial3D.new()
		mat.albedo_texture = _checker()
		mat.uv1_scale = Vector3(16.0 / (2.0 * GROUND_CELL), 16.0 / (2.0 * GROUND_CELL), 1)
		mat.roughness = 1.0
		plane.material = mat
		_ground = MeshInstance3D.new()
		_ground.mesh = plane
		_ground.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		_root.add_child(_ground)


func _item_dir(refs: Array) -> Variant:
	if refs.is_empty():
		return null
	var b := (refs[0] as Node3D).global_transform.basis
	if b.x.length() < 0.01:
		return null
	var x := b.x.normalized()
	return {"yaw_deg": rad_to_deg(atan2(x.x, x.z)), "pitch_deg": rad_to_deg(asin(clampf(x.y, -1.0, 1.0)))}


func _ankles(skel: Skeleton3D, bones: Array) -> Variant:
	if skel == null or bones.size() != 2 or int(bones[0]) < 0 or int(bones[1]) < 0:
		return null
	var out := []
	for b in bones:
		var p := skel.global_transform * skel.get_bone_global_pose(int(b)).origin
		# The figure faces +z with its left along +x: [side, forward, up].
		out.append([snappedf(p.x, 0.0001), snappedf(p.z, 0.0001), snappedf(p.y, 0.0001)])
	return out


func _run(req: Dictionary) -> int:
	_setup(req)
	var figure := _load(str(req["figure"]))
	if figure == null:
		return 1
	var refs := figure.find_children("item_ref*", "Node3D", true, false)
	if req.get("item") != null and str(req["item"]) != "":
		if refs.is_empty():
			printerr("BENCH: the figure has no item_ref node")
			return 1
		var held := _load(str(req["item"]))
		if held == null:
			return 1
		(refs[0] as Node3D).add_child(held)
	_root.add_child(figure)
	var players := figure.find_children("*", "AnimationPlayer", true, false)
	var player: AnimationPlayer = players[0] if players.size() > 0 else null
	var skels := figure.find_children("*", "Skeleton3D", true, false)
	var skel: Skeleton3D = skels[0] if skels.size() > 0 else null
	var feet := []
	if skel != null:
		feet = [skel.find_bone("LeftFoot"), skel.find_bone("RightFoot")]
	var height := float(req.get("height", 1.8))
	var size: Array = req.get("size", [64, 64])
	var bg: Array = req.get("background", [0.15, 0.16, 0.18])
	var out := str(req["out"])
	var result := {}
	for clip: Dictionary in req.get("clips", []):
		var name := str(clip["name"])
		if player == null or not player.has_animation(name):
			print("BENCH: missing ", name)
			result[name] = {"frames": 0, "missing": true, "item": [], "ankles": []}
			continue
		var n := int(clip.get("frames", 1))
		var fps := float(clip.get("fps", 24.0))
		var travel = clip.get("travel")
		DirAccess.make_dir_recursive_absolute(out.path_join(name))
		player.play(name)
		var dirs := []
		var ankles := []
		for i in n:
			var t := float(i) / fps
			player.seek(t, true)
			player.pause()
			if _ground != null:
				var g := Vector3.ZERO
				if travel is Array and (travel as Array).size() == 2:
					# The figure walks in place: the ground goes back under it (+x is its left).
					var k := float(i) / float(n) * height
					g = Vector3(-float(travel[0]) * k, 0.0, -float(travel[1]) * k)
				_ground.position = Vector3(fposmod(g.x, 2.0 * GROUND_CELL), 0.0, fposmod(g.z, 2.0 * GROUND_CELL))
			await RenderingServer.frame_post_draw
			await RenderingServer.frame_post_draw
			dirs.append(_item_dir(refs))
			ankles.append(_ankles(skel, feet))
			var frame := Image.create(int(size[0]), int(size[1]), false, Image.FORMAT_RGBA8)
			frame.fill(Color(float(bg[0]), float(bg[1]), float(bg[2])))
			for v: Dictionary in _views:
				var img := (v["vp"] as SubViewport).get_texture().get_image()
				img.convert(Image.FORMAT_RGBA8)
				var r: Rect2i = v["rect"]
				frame.blit_rect(img, Rect2i(Vector2i.ZERO, img.get_size()), r.position)
			if frame.save_png(out.path_join(name).path_join("%03d.png" % i)) != OK:
				printerr("BENCH: cannot write a frame of ", name)
				return 1
		result[name] = {"frames": n, "missing": false, "item": dirs, "ankles": ankles}
		print("BENCH: rendered ", name, " ", n, " frames")
	var f := FileAccess.open(out.path_join("render.json"), FileAccess.WRITE)
	f.store_string(JSON.stringify({"clips": result}, " "))
	f.close()
	figure.queue_free()
	return 0
