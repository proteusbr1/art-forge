"""Client of the local ComfyUI HTTP API (/prompt, /queue, /history, /view, /upload/image,
/free, /system_stats). Nothing in ComfyUI is installed, changed or restarted."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_SERVER = "http://127.0.0.1:8188"
CLIENT_ID = "art-forge-" + uuid.uuid4().hex[:8]


class Server:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.uploaded = set()

    def req(self, path, data=None, headers=None, timeout=60):
        r = urllib.request.Request(self.url + path, data=data, headers=headers or {})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.read()

    def get_json(self, path, timeout=60):
        return json.loads(self.req(path, timeout=timeout))

    def post_json(self, path, obj):
        return self.req(path, json.dumps(obj).encode(), {"Content-Type": "application/json"})

    def queue_ids(self):
        q = self.get_json("/queue")
        ids = set()
        for key in ("queue_running", "queue_pending"):
            for item in q.get(key, []):
                if len(item) > 1:
                    ids.add(item[1])
        return ids, len(q.get("queue_running", [])), len(q.get("queue_pending", []))

    def history(self, pid):
        h = self.get_json("/history/" + pid)
        return h.get(pid)

    def upload(self, path, file_hash):
        name = "factory_%s%s" % (file_hash[:20], os.path.splitext(path)[1].lower() or ".png")
        if name in self.uploaded:
            return name
        boundary = uuid.uuid4().hex
        with open(path, "rb") as f:
            payload = f.read()
        parts = []
        for key, val in (("type", "input"), ("overwrite", "true")):
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, key, val)).encode())
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\n"
                      "Content-Type: image/png\r\n\r\n" % (boundary, name)).encode() + payload + b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode())
        res = json.loads(self.req("/upload/image", b"".join(parts),
                                  {"Content-Type": "multipart/form-data; boundary=" + boundary}, timeout=300))
        got = (res["subfolder"] + "/" + res["name"]) if res.get("subfolder") else res["name"]
        self.uploaded.add(got)
        return got

    def submit(self, wf):
        body = {"prompt": wf, "client_id": CLIENT_ID}
        try:
            res = json.loads(self.post_json("/prompt", body))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code == 400:
                raise ValueError("prompt refused: " + text[:1500])
            raise
        if res.get("node_errors"):
            raise ValueError("node errors: %s" % json.dumps(res["node_errors"])[:1500])
        return res["prompt_id"]

    def free(self):
        try:
            self.post_json("/free", {"unload_models": True, "free_memory": True})
        except Exception:
            pass

    def download(self, filename, subfolder, kind, dest):
        q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": kind})
        data = self.req("/view?" + q, timeout=600)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
