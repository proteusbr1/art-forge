"""Machine-wide GPU coordination: one lock file for every art-forge process of this user.

A factory run holds the lock for the whole run and each local GPU job (forge.local_gpu)
takes it too (in the same process the lock is re-entrant), so two factory instances, or a
factory and a local tool started by hand, never share the card. The lock is an operating
system lock on the file (released by the system when the process ends, even when it is
killed); the file also holds who took it (pid, what, since). A lock file whose owner is
gone is taken over, and the taker logs it.

The lock file is <folder>/gpu.lock, the folder being the FORGE_LOCK_DIR environment
variable (the tests use a temporary folder; the launcher passes its own folder to the
detached factory with --lock-dir, which sets it), else %USERPROFILE%\\.art-forge on Windows,
else ~/.local/state/art-forge.

Not under %LOCALAPPDATA%: a packaged (MSIX) desktop application redirects the AppData writes
of every process it starts into its own private copy of AppData, while a factory started
detached (WMI) writes the real one, so the same path named two different files and two
processes could both "hold" the lock. A lock file that resolves to another file than the
path asked for is refused (GpuLockError).

gpu_processes() lists the processes on the card from nvidia-smi (None when nvidia-smi is
missing or fails: the caller logs that they could not be listed and goes on).
"""

import json
import os
import subprocess
import time

from .factory_util import pid_alive

LOCK_NAME = "gpu.lock"
# Windows locks are mandatory: the lock covers one byte far past the information written at
# the start of the file, so that other processes can still read who holds it.
LOCK_OFFSET = 1 << 30

_held = {}  # path -> [fd, count, info]


class GpuBusy(RuntimeError):
    def __init__(self, info, path):
        self.info = info or {}
        self.path = path
        RuntimeError.__init__(self, "the GPU lock %s is held by %s" % (path, describe(self.info)))


class GpuLockError(RuntimeError):
    """The lock file cannot serve as the machine-wide lock (it is redirected elsewhere)."""


def lock_dir(folder=None):
    if folder:
        return folder
    if os.environ.get("FORGE_LOCK_DIR"):
        return os.environ["FORGE_LOCK_DIR"]
    return default_lock_dir()


def default_lock_dir():
    """The lock folder without FORGE_LOCK_DIR: this user's machine-wide one, outside AppData
    (see the module's note)."""
    if os.name == "nt":
        home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        return os.path.join(home, ".art-forge")
    return os.path.join(os.path.expanduser("~"), ".local", "state", "art-forge")


def lock_path(folder=None):
    return os.path.join(lock_dir(folder), LOCK_NAME)


def redirected(path):
    """Why the existing file at `path` is not the file its folder names (a packaged
    application's file system redirection), or None. Windows only: the file's final path
    (what the system opened) against its folder's final path joined with its name."""
    if os.name != "nt" or not os.path.exists(path):
        return None
    try:
        got = os.path.realpath(path)
        want = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    except (OSError, ValueError):
        return None
    if os.path.normcase(got) != os.path.normcase(want):
        return ("the lock file %s is redirected to %s (file system redirection of a packaged application): "
                "processes started elsewhere would lock another file; use a folder outside AppData "
                "(FORGE_LOCK_DIR)" % (path, got))
    return None


def describe(info):
    if not info:
        return "another process"
    return "pid %s (%s, since %s)" % (info.get("pid"), info.get("what") or "?", info.get("since") or "?")


def _read_fd(fd):
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 65536)
        return json.loads(raw.decode("utf-8")) if raw.strip() else None
    except (OSError, ValueError):
        return None


def _try_lock(fd):
    """True when this process got the OS lock of the file."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _write_info(fd, info):
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    if info:
        os.write(fd, json.dumps(info).encode("utf-8"))


def acquire(what, folder=None, log=None):
    """Take the machine-wide GPU lock for `what` (a short description), or raise GpuBusy
    naming the holder. Re-entrant in one process (each acquire needs one release). A lock
    file left by a process that is gone is taken over, and `log` (a function) is told."""
    path = lock_path(folder)
    if path in _held:
        _held[path][1] += 1
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    why = redirected(path)
    if why:
        os.close(fd)
        raise GpuLockError(why)
    if not _try_lock(fd):
        info = _read_fd(fd)
        os.close(fd)
        raise GpuBusy(info, path)
    previous = _read_fd(fd)
    if previous and previous.get("pid") != os.getpid() and log:
        state = "gone" if not pid_alive(int(previous.get("pid") or 0)) else "no longer holding it"
        log("GPU lock %s: taken over from %s (%s)" % (path, describe(previous), state))
    info = {"pid": os.getpid(), "what": what, "since": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write_info(fd, info)
    _held[path] = [fd, 1, info]
    return path


def release(folder=None, path=None):
    path = path or lock_path(folder)
    held = _held.get(path)
    if not held:
        return
    held[1] -= 1
    if held[1] > 0:
        return
    del _held[path]
    fd = held[0]
    try:
        _write_info(fd, None)
    except OSError:
        pass
    _unlock(fd)
    os.close(fd)


class hold:
    """with hold("what"): ... takes the lock for the block (re-entrant)."""

    def __init__(self, what, folder=None, log=None):
        self.what, self.folder, self.log = what, folder, log

    def __enter__(self):
        self.path = acquire(self.what, self.folder, self.log)
        return self

    def __exit__(self, *exc):
        release(path=self.path)
        return False


def holder(folder=None):
    """Who holds the lock, as {"pid", "what", "since"}, or None when it is free (this
    process's own hold counts as held). Takes nothing."""
    path = lock_path(folder)
    if path in _held:
        return dict(_held[path][2])
    if not os.path.exists(path):
        return None
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
        return None
    try:
        if _try_lock(fd):
            _unlock(fd)
            return None
        return _read_fd(fd) or {}
    finally:
        os.close(fd)


# --- processes on the card

def gpu_processes(exe=None, timeout=15):
    """[{"pid", "name", "mb"}] of the compute processes nvidia-smi lists ("mb" None when the
    driver does not report it, as on Windows display drivers), or None when nvidia-smi is
    missing or fails. FORGE_NVIDIA_SMI overrides the program (the tests use it)."""
    exe = exe or os.environ.get("FORGE_NVIDIA_SMI") or "nvidia-smi"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        res = subprocess.run([exe, "--query-compute-apps=pid,process_name,used_memory",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL, **flags)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    out = []
    for line in res.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        mb = parts[-1]
        out.append({"pid": int(parts[0]), "name": os.path.basename(",".join(parts[1:-1]).replace("\\", "/")),
                    "mb": int(float(mb)) if mb.replace(".", "", 1).isdigit() else None})
    return out


def describe_processes(procs):
    """One log line of gpu_processes()."""
    if procs is None:
        return "other GPU processes could not be listed (nvidia-smi missing or failing)"
    if not procs:
        return "no other compute process on the card"
    parts = ["%s (%d, %s)" % (p["name"], p["pid"], "%d MB" % p["mb"] if p["mb"] is not None else "n/a")
             for p in procs]
    unknown = sum(1 for p in procs if p["mb"] is None)
    text = "processes on the card (%d): %s" % (len(procs), ", ".join(parts))
    if unknown:
        text += "; %d without a reported VRAM amount (not waited for)" % unknown
    return text


def busy_processes(procs, busy_mb):
    """The processes of gpu_processes() that hold more than busy_mb of VRAM (other than this
    one)."""
    return [p for p in procs or [] if p["mb"] is not None and p["mb"] > busy_mb and p["pid"] != os.getpid()]


def gpu_memory(server=None, exe=None, timeout=15):
    """{"used_mb", "total_mb", "source"} of the card: from nvidia-smi --query-gpu (the first
    card), else from the ComfyUI server's /system_stats (vram_total - vram_free of its first
    device), else None. The measure of a local GPU job's wait when the driver reports no
    VRAM amount per process (Windows display drivers)."""
    exe = exe or os.environ.get("FORGE_NVIDIA_SMI") or "nvidia-smi"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        res = subprocess.run([exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **flags)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 2 and all(p.replace(".", "", 1).isdigit() for p in parts):
                    return {"used_mb": int(float(parts[0])), "total_mb": int(float(parts[1])), "source": "nvidia-smi"}
    except (OSError, subprocess.SubprocessError):
        pass
    if server is not None:
        try:
            dev = server.get_json("/system_stats")["devices"][0]
            total, free = dev.get("vram_total"), dev.get("vram_free")
            if total and free is not None:
                return {"used_mb": int((total - free) / 2 ** 20), "total_mb": int(total / 2 ** 20),
                        "source": "ComfyUI /system_stats"}
        except Exception:
            pass
    return None
