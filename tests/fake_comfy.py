"""A fake ComfyUI HTTP server for the factory tests: the endpoints the factory calls,
answered from memory, with small generated pictures for every save node.

Importing it also points FORGE_LOCK_DIR to a temporary folder (when it is not set), so the
tests and the factories they start never touch this user's machine-wide GPU lock."""

import copy
import io
import json
import os
import tempfile
import threading
import time
import urllib.parse
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from PIL import Image

if not os.environ.get("FORGE_LOCK_DIR"):
    os.environ["FORGE_LOCK_DIR"] = tempfile.mkdtemp(prefix="forge_test_lock_")

SIZE = 96
# Inputs that name files rather than settings: left out of the picture's seed.
NAME_INPUTS = ("filename_prefix", "output_mesh_name")
FAKE_GLB = b"glTF\x02\x00\x00\x00" + b"\x00" * 16


def seed_of(wf):
    """The seed of a prompt, mixed with its texts and every other setting, so that a changed
    text or setting (steps, guidance, an input image) changes the picture."""
    texts = [n["inputs"].get(k) for n in wf.values() for k in ("text", "prompt")]
    mix = zlib.crc32(json.dumps([t for t in texts if isinstance(t, str)]).encode())
    rest = sorted((nid, k, json.dumps(v)) for nid, n in wf.items() for k, v in n["inputs"].items()
                  if k not in NAME_INPUTS + ("text", "prompt", "seed", "noise_seed"))
    mix = zlib.crc32(json.dumps(rest).encode(), mix)
    for node in wf.values():
        for name in ("seed", "noise_seed"):
            v = node["inputs"].get(name)
            if isinstance(v, int):
                return v + mix
    return mix


def picture(seed, mode):
    """A light background with one blob; the seed changes its size, colour and grain."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE] / SIZE
    r = 0.15 + 0.2 * rng.random()
    blob = ((xx - 0.5) ** 2 + (yy - 0.5) ** 2) < r * r
    rgb = np.ones((SIZE, SIZE, 3)) * 0.95
    rgb[blob] = rng.random(3) * 0.6 + rng.random((int(blob.sum()), 3)) * 0.3
    if mode == "L":
        return Image.fromarray((blob * 255).astype(np.uint8), "L")
    im = Image.fromarray((rgb * 255).astype(np.uint8), "RGB")
    if mode == "RGBA":
        im.putalpha(Image.fromarray((blob * 255).astype(np.uint8), "L"))
    return im


def png_bytes(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class FakeComfy:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.lock = threading.Lock()
        self.prompts = {}      # prompt id -> {"wf", "done_at", "outputs"}
        self.order = []
        self.files = {}        # (type, subfolder, filename) -> bytes
        self.submitted = []    # (job id from the save prefix, workflow, others pending at submit)
        self.frees = 0
        self.counter = 0
        self.requests = []         # (time, method, path) of every request
        self.history_changes = {}  # {"<node>.<input>": value} the executed prompt holds instead
        self.history_prompt = True  # False: /history entries hold no prompt
        self.object_info = None     # the /object_info answer (None: 404)
        handler = self.handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def reset_log(self):
        with self.lock:
            self.submitted = []

    def external(self, delay):
        """A prompt of another client (no factory job), queued for delay seconds."""
        with self.lock:
            pid = "external-" + uuid.uuid4().hex[:8]
            self.prompts[pid] = {"wf": {}, "done_at": time.time() + delay, "outputs": {}}
            self.order.append(pid)
            return self.prompts[pid]["done_at"]

    def requests_between(self, t0, t1):
        """Requests strictly between two times (the clock may tick coarsely: a request that
        shares t0's tick came before)."""
        with self.lock:
            return [r for r in self.requests if t0 < r[0] < t1]

    def executed(self, wf):
        """The prompt as the fake server "ran" it: history_changes applied."""
        wf = copy.deepcopy(wf)
        for key, v in self.history_changes.items():
            nid, name = key.split(".", 1)
            if nid in wf:
                wf[nid]["inputs"][name] = v
        return wf

    def pending(self):
        now = time.time()
        return [p for p in self.order if self.prompts[p]["done_at"] > now]

    # --- prompt execution (instant, outputs built at submit)
    def run(self, wf):
        outputs = {}
        seed = seed_of(wf)
        for nid, node in wf.items():
            cls, ins = node["class_type"], node["inputs"]
            if cls == "SaveImage":
                src = wf[ins["images"][0]]["class_type"]
                mode = "RGBA" if src == "JoinImageWithAlpha" else "L" if src == "MaskToImage" else "RGB"
                data, ext, key = png_bytes(picture(seed + int(nid), mode)), ".png", "images"
            elif cls in ("Hy3D21ExportMesh", "SaveGLB"):
                data, ext, key = FAKE_GLB, ".glb", "3d"
            else:
                if cls == "Hy3DInPaint":
                    self.files[("output", "", ins["output_mesh_name"] + ".glb")] = FAKE_GLB
                continue
            prefix = ins.get("filename_prefix", "out")
            sub, base = prefix.rsplit("/", 1) if "/" in prefix else ("", prefix)
            self.counter += 1
            name = "%s_%05d_%s" % (base, self.counter, ext)
            self.files[("output", sub, name)] = data
            outputs[nid] = {key: [{"filename": name, "subfolder": sub, "type": "output"}]}
        return outputs

    def job_of(self, wf):
        for node in wf.values():
            p = node["inputs"].get("filename_prefix")
            if isinstance(p, str) and p.startswith("forge/"):
                return p[len("forge/"):].rsplit("_n", 1)[0]
        return None

    def handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code, body, ctype="application/json"):
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                return self.rfile.read(int(self.headers.get("Content-Length", 0)))

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                with fake.lock:
                    fake.requests.append((time.time(), "GET", url.path))
                    now = time.time()
                    if url.path == "/queue":
                        pend = fake.pending()
                        return self.send(200, {"queue_running": [[0, p] for p in pend[:1]],
                                               "queue_pending": [[i, p] for i, p in enumerate(pend[1:])]})
                    if url.path.startswith("/history/"):
                        pid = url.path.split("/")[-1]
                        p = fake.prompts.get(pid)
                        if not p or p["done_at"] > now:
                            return self.send(200, {})
                        t = int(p["done_at"] * 1000)
                        status = {"status_str": "success", "completed": True,
                                  "messages": [["execution_start", {"timestamp": t - 1500}],
                                               ["execution_success", {"timestamp": t}]]}
                        entry = {"status": status, "outputs": p["outputs"]}
                        if fake.history_prompt:
                            entry["prompt"] = [fake.order.index(pid), pid, fake.executed(p["wf"]),
                                               {"client_id": "fake"}, sorted(p["outputs"])]
                        return self.send(200, {pid: entry})
                    if url.path == "/view":
                        q = urllib.parse.parse_qs(url.query)
                        key = (q.get("type", ["output"])[0], q.get("subfolder", [""])[0], q["filename"][0])
                        if key not in fake.files:
                            return self.send(404, {"error": "not found"})
                        return self.send(200, fake.files[key], "application/octet-stream")
                    if url.path == "/object_info" and fake.object_info is not None:
                        return self.send(200, fake.object_info)
                    if url.path == "/system_stats":
                        return self.send(200, {"devices": [{"name": "fake", "vram_free": 16 * 2 ** 30}]})
                return self.send(404, {"error": "unknown path"})

            def do_POST(self):
                url = urllib.parse.urlparse(self.path)
                data = self.body()
                with fake.lock:
                    fake.requests.append((time.time(), "POST", url.path))
                    if url.path == "/prompt":
                        wf = json.loads(data)["prompt"]
                        left = [s for s in json.dumps(wf).split('"') if s.startswith("$") and s.endswith("$") and len(s) > 2]
                        if left:
                            return self.send(400, {"error": "placeholders left", "node_errors": {}})
                        for node in wf.values():
                            img = node["inputs"].get("image")
                            if node["class_type"].endswith("LoadImage") and isinstance(img, str) \
                                    and img.startswith("factory_") and ("input", "", img) not in fake.files:
                                return self.send(400, {"error": "missing input " + img, "node_errors": {}})
                        pid = uuid.uuid4().hex
                        fake.submitted.append((fake.job_of(wf), wf, len(fake.pending())))
                        fake.prompts[pid] = {"wf": wf, "done_at": time.time() + fake.delay, "outputs": fake.run(wf)}
                        fake.order.append(pid)
                        return self.send(200, {"prompt_id": pid, "number": len(fake.order), "node_errors": {}})
                    if url.path == "/upload/image":
                        name = data.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
                        start = data.index(b"\r\n\r\n", data.index(b'filename="')) + 4
                        payload = data[start:data.rindex(b"\r\n--")]
                        fake.files[("input", "", name)] = payload
                        return self.send(200, {"name": name, "subfolder": "", "type": "input"})
                    if url.path == "/free":
                        fake.frees += 1
                        return self.send(200, {})
                return self.send(404, {"error": "unknown path"})

        return Handler
