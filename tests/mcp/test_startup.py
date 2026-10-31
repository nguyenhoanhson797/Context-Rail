from __future__ import annotations

import subprocess
import sys


def test_non_serve_commands_never_import_the_sdk() -> None:
    probe = (
        "import sys, runpy\n"
        "sys.argv = ['rail', '--help']\n"
        "try:\n"
        "    runpy.run_module('context_rail', run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in {'mcp', 'anyio', 'pydantic'}))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "[]"
