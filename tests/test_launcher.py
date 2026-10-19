"""The launcher (Run-Forge.ps1): its command line for the detached factory (Format-Argument:
each argument must come back unchanged when Windows splits the line, CommandLineToArgvW, a
folder in a drive root (C:\\) included), and a detached start through the same path as a
night's (WMI, pythonw) against the fake server: the factory holds the GPU lock of the
launcher's lock folder while it runs, and the default lock folder names one file for a
detached process and for a process of this one's tree."""

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from fake_comfy import FakeComfy  # noqa: E402  (also a private FORGE_LOCK_DIR for this process)
from forge import gpu_lock  # noqa: E402
from forge.factory_util import pid_alive  # noqa: E402

SCRIPT = os.path.join(ROOT, "Run-Forge.ps1")
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
ARGS = ["--order-base", "C:\\", "D:\\games\\my order\\", "C:\\Program Files\\x\\order.json", "plain",
        "C:\\a\\\\", "with space"]
PLAIN = {"workflow": "qwen_image_21_turbo_t2i.json", "values": {"$PROMPT$": "A plain backdrop.", "$NEGATIVE$": ""},
         "roles": {"12": "rgb"}}


def split_command_line(line):
    """The arguments Windows gives a program for a command line."""
    fn = ctypes.windll.shell32.CommandLineToArgvW
    fn.restype = ctypes.POINTER(ctypes.c_wchar_p)
    fn.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    n = ctypes.c_int()
    argv = fn("program.exe " + line, ctypes.byref(n))
    try:
        return [argv[i] for i in range(1, n.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def powershell(command, env=None, timeout=120):
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", command], capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, env=env, **FLAGS)


def wait_for(test, seconds, step=0.2):
    deadline = time.time() + seconds
    while time.time() < deadline:
        got = test()
        if got:
            return got
        time.sleep(step)
    return test()


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


@unittest.skipUnless(os.name == "nt", "Windows command lines")
class LauncherQuotingTest(unittest.TestCase):
    def test_arguments_survive_the_command_line(self):
        with open(SCRIPT, encoding="ascii") as f:
            text = f.read()
        fn = re.search(r"# BEGIN Format-Argument\n(.*?)# END Format-Argument", text, re.S).group(1)
        quoted = ", ".join("'%s'" % a.replace("'", "''") for a in ARGS)
        command = fn + "\n$line = ((@(%s) | ForEach-Object { Format-Argument $_ }) -join ' ')\n" \
                       "[Console]::Out.Write($line)" % quoted
        res = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                             capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, **FLAGS)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(split_command_line(res.stdout), ARGS, res.stdout)


@unittest.skipUnless(os.name == "nt", "the launcher starts the factory through WMI on Windows")
class DetachedStartTest(unittest.TestCase):
    """The night's path: Run-Forge.ps1 copies the program and starts it detached (WMI,
    pythonw). Such a process gets the user's stored environment, not the launcher's: before
    the fix it ignored FORGE_LOCK_DIR (and COMFY_SERVER) and locked the default folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_launch_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fake = FakeComfy(delay=1.0)
        self.addCleanup(self.fake.close)
        # A copy of the repository without this machine's configuration; its own
        # configuration points to the fake server too, so a launcher that failed to pass
        # -Server on could still never reach a real ComfyUI.
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        shutil.copy(SCRIPT, self.repo)
        shutil.copytree(os.path.join(ROOT, "forge"), os.path.join(self.repo, "forge"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        for sub in ("tools", "data"):
            if os.path.isdir(os.path.join(ROOT, sub)):
                shutil.copytree(os.path.join(ROOT, sub), os.path.join(self.repo, sub),
                                ignore=shutil.ignore_patterns("__pycache__", ".godot"))
        with open(os.path.join(self.repo, "forge.local.json"), "w", encoding="utf-8") as f:
            json.dump({"comfy_url": self.fake.url, "python": sys.executable}, f)
        self.order = os.path.join(self.tmp, "order.json")
        tasks = [dict(PLAIN, id="extra/backdrop/plain%d" % i, values=dict(PLAIN["values"], **{"$SEED$": 40 + i}))
                 for i in range(3)]
        with open(self.order, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "style": {}, "families": [], "tasks": tasks}, f)
        self.out = os.path.join(self.tmp, "out")
        self.lock_dir = os.path.join(self.tmp, "lock")

    def test_the_detached_factory_holds_the_launchers_lock_folder(self):
        env = dict(os.environ, FORGE_LOCK_DIR=self.lock_dir)
        env.pop("COMFY_SERVER", None)
        res = powershell("& '%s' -Order '%s' -Out '%s' -Server '%s' -Limit 1" % (
            os.path.join(self.repo, "Run-Forge.ps1"), self.order, self.out, self.fake.url), env=env)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("Factory started in the background", res.stdout)
        pid_file = os.path.join(self.out, "factory.pid")
        self.assertTrue(wait_for(lambda: read_text(pid_file), 60), "the detached factory did not start: %s" % res.stdout)
        pid = int(read_text(pid_file))
        self.assertTrue(wait_for(lambda: self.fake.submitted, 60), "the detached factory sent nothing")
        holder = gpu_lock.holder(self.lock_dir)
        self.assertIsNotNone(holder, "the detached factory does not hold the launcher's lock folder")
        self.assertEqual(holder["pid"], pid)
        with self.assertRaises(gpu_lock.GpuBusy):
            gpu_lock.acquire("by hand", self.lock_dir)
        self.assertTrue(wait_for(lambda: not pid_alive(pid), 120, 0.5), "the detached factory did not finish")
        self.assertIsNone(gpu_lock.holder(self.lock_dir))
        self.assertIn("factory finished", read_text(os.path.join(self.out, "factory.log")))
        self.assertEqual(len(self.fake.submitted), 3)

    def test_the_default_lock_folder_is_one_file_for_a_detached_process(self):
        """The cause of the night's lock that another process could take: the old default
        folder (%LOCALAPPDATA%\\art-forge) is redirected for the processes a packaged desktop
        application starts, so a detached factory and such a process locked two files. The
        default folder must name the same file for both."""
        folder = gpu_lock.default_lock_dir()
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, "probe-%s.tmp" % uuid.uuid4().hex)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("probe")
        self.addCleanup(lambda: os.path.exists(probe) and os.remove(probe))
        report = os.path.join(self.tmp, "seen.json")
        script = os.path.join(self.tmp, "see.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("import json, os, sys\n"
                    "p = sys.argv[1]\n"
                    "seen = {'exists': os.path.isfile(p)}\n"
                    "if seen['exists']:\n"
                    "    st = os.stat(p)\n"
                    "    seen.update(ino=st.st_ino, dev=st.st_dev)\n"
                    "json.dump(seen, open(sys.argv[2], 'w'))\n")
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(pythonw):
            pythonw = sys.executable
        line = '"%s" "%s" "%s" "%s"' % (pythonw, script, probe, report)
        res = powershell("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
                         "@{ CommandLine = '%s'; CurrentDirectory = '%s' }; exit $r.ReturnValue" % (
                             line.replace("'", "''"), self.tmp))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(wait_for(lambda: read_text(report), 30), "the detached probe did not run")
        with open(report, encoding="utf-8") as f:
            seen = json.load(f)
        st = os.stat(probe)
        self.assertTrue(seen["exists"], "a detached process does not see %s: the folder is redirected" % probe)
        self.assertEqual((seen["ino"], seen["dev"]), (st.st_ino, st.st_dev))
        self.assertIsNone(gpu_lock.redirected(probe))


if __name__ == "__main__":
    unittest.main()
