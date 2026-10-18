"""The launcher's command line for the detached factory (Run-Forge.ps1 Format-Argument): each
argument must come back unchanged when Windows splits the line (CommandLineToArgvW), a
folder in a drive root (C:\\) included."""

import ctypes
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "Run-Forge.ps1")
ARGS = ["--order-base", "C:\\", "D:\\games\\my order\\", "C:\\Program Files\\x\\order.json", "plain",
        "C:\\a\\\\", "with space"]


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


@unittest.skipUnless(os.name == "nt", "Windows command lines")
class LauncherQuotingTest(unittest.TestCase):
    def test_arguments_survive_the_command_line(self):
        with open(SCRIPT, encoding="ascii") as f:
            text = f.read()
        fn = re.search(r"# BEGIN Format-Argument\n(.*?)# END Format-Argument", text, re.S).group(1)
        quoted = ", ".join("'%s'" % a.replace("'", "''") for a in ARGS)
        command = fn + "\n$line = ((@(%s) | ForEach-Object { Format-Argument $_ }) -join ' ')\n" \
                       "[Console]::Out.Write($line)" % quoted
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW}
        res = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                             capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, **flags)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(split_command_line(res.stdout), ARGS, res.stdout)


if __name__ == "__main__":
    unittest.main()
