"""Small helpers shared by the factory modules: log, hashes, atomic JSON, seeds, pids."""

import ctypes
import hashlib
import json
import os
import time


class Log:
    def __init__(self, path):
        self.path = path

    def __call__(self, msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
        try:
            print(line, flush=True)
        except Exception:
            pass
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_SHA_CACHE = {}


def sha_file_cached(path):
    """sha_file of a file the order supplies, hashed again only when its size or modification
    time changes (the state of every job is worked out on every pass)."""
    st = os.stat(path)
    key = (os.path.realpath(path), st.st_size, st.st_mtime_ns)
    if key not in _SHA_CACHE:
        _SHA_CACHE[key] = sha_file(path)
    return _SHA_CACHE[key]


def sha_obj(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def slug(job_id):
    return job_id.split("/")[-1]


def seeds_for(key, n):
    base = int(hashlib.sha1(key.encode()).hexdigest()[:6], 16) % 900000 + 1000
    return [base + 17 * i for i in range(n)]


def env_seconds(name, default):
    """A waiting time in seconds, overridable by an environment variable (the tests use it)."""
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
