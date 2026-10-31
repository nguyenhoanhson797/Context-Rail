"""Bounded runner: scrubbed environment, redaction, timeout and output caps."""

from __future__ import annotations

import hashlib
import inspect
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from context_rail.runner.run import DEFAULT_ENV_ALLOW, SECRET_NAME, redact, run
from context_rail.store.location import PathEscape

TIMEOUT = 60


def _python(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def _running(pid: int) -> bool:
    if os.name == "nt":
        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return str(pid) in listed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


def test_default_env_allow_is_pinned() -> None:
    assert DEFAULT_ENV_ALLOW == (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
    )


def test_secret_name_matches_secret_bearing_names() -> None:
    for name in ("MY_API_KEY", "github_token", "DbPassword", "AWS_CREDENTIAL"):
        assert SECRET_NAME.search(name), name
    for name in ("PATH", "HOME", "APP_MODE"):
        assert SECRET_NAME.search(name) is None, name


def test_redact_keeps_short_values_and_replaces_long_ones() -> None:
    assert redact("abc abc", ["abc"]) == "abc abc"
    assert redact("value abcdef here", ["abcdef"]) == "value [redacted] here"


def test_redact_prefers_the_longest_overlapping_secret() -> None:
    assert redact("abcdefgh", ["abcdef", "abcdefgh"]) == "[redacted]"
    assert redact("abcdefgh", ["abcdefgh", "abcdef"]) == "[redacted]"


def test_secret_env_not_passed_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_API_KEY", "supersecret123")
    result = run(
        _python(
            "import os;print(os.environ.get('MY_API_KEY'));print('supersecret123')"
        ),
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=TIMEOUT,
    )
    assert result["exit"] == 0
    assert "None" in result["stdout"]
    assert "[redacted]" in result["stdout"]
    assert "supersecret123" not in result["stdout"]
    assert "supersecret123" not in result["stderr"]


def test_child_env_is_allowlist_plus_extra_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_API_KEY", "supersecret123")
    result = run(
        _python("import os;print('\\n'.join(sorted(os.environ)))"),
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=TIMEOUT,
        extra_env={"APP_MODE": "x"},
    )
    names = set(result["stdout"].split())
    assert names <= set(DEFAULT_ENV_ALLOW) | {"APP_MODE"}
    assert "MY_API_KEY" not in names
    assert "APP_MODE" in names

    value = run(
        _python("import os;print(os.environ['APP_MODE'])"),
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=TIMEOUT,
        extra_env={"APP_MODE": "x"},
    )
    assert value["stdout"].strip() == "x"


def test_secret_named_extra_env_value_is_redacted(tmp_path: Path) -> None:
    result = run(
        _python("import os;print(os.environ['MY_TOKEN'])"),
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=TIMEOUT,
        extra_env={"MY_TOKEN": "tokenvalue123"},
    )
    assert result["exit"] == 0
    assert "[redacted]" in result["stdout"]
    assert "tokenvalue123" not in result["stdout"]


def test_short_extra_env_secret_is_not_redacted(tmp_path: Path) -> None:
    result = run(
        _python("import os;print(os.environ['MY_TOKEN'])"),
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=TIMEOUT,
        extra_env={"MY_TOKEN": "abc"},
    )
    assert result["stdout"].strip() == "abc"

    longer = run(
        _python("import os;print(os.environ['MY_TOKEN'])"),
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=TIMEOUT,
        extra_env={"MY_TOKEN": "abcdef"},
    )
    assert "[redacted]" in longer["stdout"]
    assert "abcdef" not in longer["stdout"]


def test_inherited_secret_allow_name_is_refused_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_API_KEY", "supersecret123")
    with pytest.raises(ValueError):
        run(
            _python("open('marker.txt', 'w').write('ran')"),
            cwd=tmp_path,
            confine_to=tmp_path,
            timeout=TIMEOUT,
            env_allow=("MY_API_KEY",),
        )
    assert not (tmp_path / "marker.txt").exists()


def test_timeout_kills(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run(
        _python("import time;time.sleep(30)"),
        cwd=tmp_path,
        confine_to=tmp_path,
        timeout=1,
    )
    elapsed = time.monotonic() - started
    assert result["timed_out"] is True
    assert result["exit"] is None
    assert result["duration_s"] < 10
    assert elapsed < 10


def test_timeout_kills_grandchild(tmp_path: Path) -> None:
    source = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])\n"
        "open('grandchild.pid', 'w').write(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    result = run(
        _python(source), cwd=tmp_path, confine_to=tmp_path, timeout=2
    )
    assert result["timed_out"] is True
    pid_file = tmp_path / "grandchild.pid"
    assert pid_file.is_file()
    pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        deadline = time.monotonic() + 5
        while _running(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _running(pid), f"grandchild {pid} survived the timeout"
    finally:
        if _running(pid):
            _kill(pid)


def test_cwd_escape_refused(tmp_path: Path) -> None:
    store = tmp_path / "store"
    outside = tmp_path / "outside"
    store.mkdir()
    outside.mkdir()
    command = _python("open('marker.txt', 'w').write('ran')")
    with pytest.raises(PathEscape):
        run(command, cwd=outside, confine_to=store, timeout=TIMEOUT)
    assert not (outside / "marker.txt").exists()

    inside = run(
        _python("print('inside')"), cwd=store, confine_to=store, timeout=TIMEOUT
    )
    assert inside["exit"] == 0
    assert "inside" in inside["stdout"]


def test_output_bounded(tmp_path: Path) -> None:
    source = (
        "import sys\n"
        "sys.stdout.write('A' * 500000); sys.stdout.flush()\n"
        "sys.stderr.write('Z' * 500000); sys.stderr.flush()\n"
    )
    result = run(_python(source), cwd=tmp_path, confine_to=tmp_path, timeout=TIMEOUT)
    combined = len(result["stdout"].encode("utf-8")) + len(
        result["stderr"].encode("utf-8")
    )
    assert combined <= 24000
    assert result["stdout"].startswith("A")
    assert result["stderr"].endswith("Z")
    assert result["omitted_bytes"] >= 1000000 - 24000


@pytest.mark.parametrize("cap", [-1, 0])
@pytest.mark.timeout(60)
def test_nonpositive_max_output_returns_no_output(tmp_path: Path, cap: int) -> None:
    source = (
        "import sys\n"
        "sys.stdout.write('A' * 5000)\n"
        "sys.stderr.write('Z' * 5000)\n"
    )
    result = run(
        _python(source), cwd=tmp_path, confine_to=tmp_path, timeout=TIMEOUT, max_output=cap
    )
    assert result["exit"] == 0
    assert result["timed_out"] is False
    assert result["stdout"] == ""
    assert result["stderr"] == ""
    assert result["omitted_bytes"] == 10000


def test_multibyte_output_stays_valid_utf8(tmp_path: Path) -> None:
    source = (
        "import sys\n"
        "sys.stdout.buffer.write('\u00e9\u6f22'.encode('utf-8') * 200000)\n"
        "sys.stdout.buffer.flush()\n"
    )
    result = run(_python(source), cwd=tmp_path, confine_to=tmp_path, timeout=TIMEOUT)
    combined = len(result["stdout"].encode("utf-8")) + len(
        result["stderr"].encode("utf-8")
    )
    assert combined <= 24000
    assert "\ufffd" not in result["stdout"]
    assert "\ufffd" not in result["stderr"]
    assert result["omitted_bytes"] > 0


def test_digest_covers_full_redacted_output(tmp_path: Path) -> None:
    source = "import sys\nsys.stdout.buffer.write(b'x' * 1000000)\nsys.stdout.buffer.flush()\n"
    result = run(_python(source), cwd=tmp_path, confine_to=tmp_path, timeout=TIMEOUT)
    assert result["omitted_bytes"] > 0
    assert result["digest"] == "sha256:" + hashlib.sha256(
        b"x" * 1000000
    ).hexdigest()


def test_secret_split_across_writes_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_API_KEY", "supersecret123")
    source = (
        "import sys, time\n"
        "sys.stdout.write('super'); sys.stdout.flush()\n"
        "time.sleep(0.3)\n"
        "sys.stdout.write('secret123'); sys.stdout.flush()\n"
    )
    result = run(_python(source), cwd=tmp_path, confine_to=tmp_path, timeout=TIMEOUT)
    assert "[redacted]" in result["stdout"]
    assert "secret123" not in result["stdout"]
    assert "supersecret" not in result["stdout"]


def test_secret_straddling_a_read_chunk_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_API_KEY", "supersecret123")
    source = (
        "import sys\n"
        "sys.stdout.write('p' * 65530)\n"
        "sys.stdout.write('super'); sys.stdout.flush()\n"
        "sys.stdout.write('secret123'); sys.stdout.flush()\n"
    )
    result = run(_python(source), cwd=tmp_path, confine_to=tmp_path, timeout=TIMEOUT)
    assert "[redacted]" in result["stdout"]
    assert "supersecret" not in result["stdout"]
    assert "secret123" not in result["stdout"]


def test_runner_never_uses_a_shell() -> None:
    module = sys.modules[run.__module__]
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source
    parameters = list(inspect.signature(run).parameters.values())
    assert parameters[0].name == "command"
    assert "list[str]" in str(parameters[0].annotation)


def test_a_missing_binary_is_a_typed_process_error(tmp_path: Path) -> None:
    from context_rail.store.write import WriteRefused

    with pytest.raises(WriteRefused) as caught:
        run(
            ["definitely-not-a-real-binary-xyz"],
            cwd=tmp_path,
            confine_to=tmp_path,
            timeout=TIMEOUT,
        )

    assert caught.value.code == "PROCESS_ERROR"
    assert caught.value.details["process"] == "definitely-not-a-real-binary-xyz"


def test_a_launch_permission_error_is_a_typed_process_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from context_rail.store.write import WriteRefused

    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError("not allowed")

    monkeypatch.setattr(subprocess, "Popen", denied)

    with pytest.raises(WriteRefused) as caught:
        run(_python("print('x')"), cwd=tmp_path, confine_to=tmp_path, timeout=TIMEOUT)

    assert caught.value.code == "PROCESS_ERROR"


def test_windows_launches_without_a_console_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("CREATE_NO_WINDOW is a Windows launch flag")
    seen: dict[str, object] = {}
    real = subprocess.Popen

    def capture(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture)

    run(_python("print('x')"), cwd=tmp_path, confine_to=tmp_path, timeout=TIMEOUT)

    assert seen["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_a_stubborn_child_cannot_hang_the_reaper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from context_rail.runner.run import _stop_tree

    class Stubborn:
        pid = 99999999

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("stubborn", timeout)

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)

    _stop_tree(Stubborn())  # returns instead of waiting forever


@pytest.mark.parametrize("ref", ["-x", "a b", "a\tb", "a\x00b", "", "   "])
@pytest.mark.parametrize("kind", ["rev_parse", "is_ancestor", "update_ref", "retain_ref", "push"])
def test_git_sync_refuses_an_unsafe_ref(tmp_path: Path, ref: str, kind: str) -> None:
    from context_rail.runner import git_sync
    from context_rail.store.write import WriteRefused

    calls = {
        "rev_parse": lambda: git_sync.rev_parse(tmp_path, tmp_path, ref),
        "is_ancestor": lambda: git_sync.is_ancestor(tmp_path, tmp_path, ref, "HEAD"),
        "update_ref": lambda: git_sync.update_ref(
            tmp_path, tmp_path, "main", ref, "HEAD", "message"
        ),
        "retain_ref": lambda: git_sync.retain_ref(tmp_path, tmp_path, ref, "HEAD"),
        "push": lambda: git_sync.push(tmp_path, tmp_path, "origin", "main", ref),
    }

    with pytest.raises(WriteRefused) as caught:
        calls[kind]()

    assert caught.value.code == "VALIDATION_FAILED"
