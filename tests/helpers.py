"""Shared plain helpers for the Context Rail test suite."""

from __future__ import annotations

import json
import hashlib
import multiprocessing
import os
import queue as queue_module
import socket
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.core.util import dump_frontmatter, load_yaml
from context_rail.store.doc import record_digest

_REPO_SRC = Path(__file__).resolve().parents[1] / "src"
_STARTUP_TIMEOUT = 120.0
_JOIN_TIMEOUT = 30.0
DEFAULT_FAKE_BEHAVIORS = ("success",)


def child_env(env: Mapping[str, str | None] | None = None) -> dict[str, str]:
    """The environment for a CLI child: this one, plus ``src`` on the path.

    ``RAIL_ACTOR`` is dropped so a developer's shell never changes which actor a
    test's records name. ``env`` overrides: a string sets, ``None`` removes.
    """
    child = dict(os.environ)
    child["PYTHONPATH"] = str(_REPO_SRC)
    child.pop("RAIL_ACTOR", None)
    for name, value in (env or {}).items():
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value
    return child


_PROBE = (
    "import sys\n"
    "from context_rail import cli\n"
    "code = cli.main(sys.argv[1:])\n"
    "sys.stderr.write('GW=%s\\nYAML=%s\\nSQLITE=%s\\n' % "
    "('context_rail.gateway.gateway' in sys.modules, 'yaml' in sys.modules, "
    "'sqlite3' in sys.modules))\n"
    "sys.exit(code)\n"
)


def run_cli_probe(
    cwd: Path,
    *args: str,
    store: Path | None = None,
    env: Mapping[str, str | None] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, bool]]:
    """Run the CLI as a user would, and report which heavy modules it loaded.

    Returns the process and ``{"GW": ..., "YAML": ..., "SQLITE": ...}``: whether
    the gateway, YAML and SQLite were imported by the time the command finished.
    The suite's daemon switch is removed from the child, so a daemon is used
    when one is live.
    """
    child = child_env({"RAIL_NO_DAEMON": None, **(env or {})})
    command = [sys.executable, "-c", _PROBE]
    if store is not None:
        command += ["--store", str(store)]
    command += [str(arg) for arg in args]
    proc = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child,
        input="",
        timeout=600,
    )
    flags = {
        key: value == "True"
        for key, _, value in (
            line.partition("=")
            for line in proc.stderr.splitlines()
            if line.startswith(("GW=", "YAML=", "SQLITE="))
        )
    }
    return proc, flags


def run_cli(
    cwd: Path,
    *args: str,
    store: Path | None = None,
    env: Mapping[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``py -m context_rail`` in ``cwd`` against the repo's ``src`` tree.

    The global ``--store`` goes before the subcommand when ``store`` is given.
    ``RAIL_ACTOR`` is dropped from the child environment so a developer's shell
    never changes which actor a test's records name. ``env`` overrides that:
    a string sets a variable in the child, ``None`` removes it.
    """
    child = child_env(env)
    command = [sys.executable, "-m", "context_rail"]
    if store is not None:
        command += ["--store", str(store)]
    command += [str(arg) for arg in args]
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child,
        input="",
        timeout=600,
    )


def start_cli(
    cwd: Path, *args: str, store: Path | None = None
) -> subprocess.Popen[str]:
    """Start ``py -m context_rail`` without waiting, for crash scenarios.

    Uses the same environment as :func:`run_cli`; the caller owns the process
    and must terminate it. Output is captured as text.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_SRC)
    env.pop("RAIL_ACTOR", None)
    command = [sys.executable, "-m", "context_rail"]
    if store is not None:
        command += ["--store", str(store)]
    command += [str(arg) for arg in args]
    return subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        start_new_session=os.name != "nt",
    )


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in ``cwd``; an error fails the calling test setup."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


@contextmanager
def serving_daemon(store: Path) -> Iterator[None]:
    """Run an in-process daemon for ``store``; callers also need ``daemon_enabled``."""
    from context_rail.gateway.daemon import _DaemonServer

    server = _DaemonServer(store)
    server.start()
    try:
        yield
    finally:
        server.stop()


@contextmanager
def holding_daemon_lock(store: Path) -> Iterator[None]:
    """Hold the store's daemon lock, as a live daemon would, without serving."""
    from context_rail.gateway.daemon import _hold_lock, _lock_path, _release_lock

    held = _hold_lock(store)
    assert held is not None, "a daemon already holds this store's lock"
    try:
        yield
    finally:
        _release_lock(held, _lock_path(Path(store)))


def daemon_descriptor(store: Path) -> dict[str, Any] | None:
    """The store's daemon descriptor as written, or None when absent or unreadable."""
    from context_rail.gateway.daemon import daemon_endpoint

    path = daemon_endpoint(store)
    assert path is not None
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return held if isinstance(held, dict) else None


def daemon_request(port: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Send one raw request to a daemon and return its parsed reply."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(json.dumps(dict(payload)).encode("utf-8") + b"\n")
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


def assert_parity(
    store: Path, tool: str, params: dict[str, Any], actor: str = "human"
) -> dict[str, Any]:
    """The daemon answers exactly as a direct call.

    The daemon is asked first: a direct call syncs the index the two share, which
    would hide a daemon that serves rows it never refreshed.
    """
    from context_rail.gateway.gateway import invoke, try_daemon_invoke

    served = try_daemon_invoke(tool, dict(params), store=store, actor=actor)
    assert served is not None, "the daemon did not answer"
    direct = invoke(tool, dict(params), store=store, actor=actor)
    expected = json.loads(json.dumps(direct))
    actual = served
    assert actual == expected, f"daemon: {json.dumps(actual)[:600]}\ndirect: {json.dumps(expected)[:600]}"
    return served


def junction(link: Path, target: Path) -> None:
    """Create a real Windows directory junction, skipping where unsupported."""
    if os.name != "nt":
        pytest.skip("directory junctions are a Windows feature")
    Path(link).parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"cannot create a junction: {(proc.stderr or proc.stdout).strip()}")


def edited_block_before(path: Path) -> str:
    """The hand-edited text a refused adapter sync must leave byte-for-byte."""
    return Path(path).read_text(encoding="utf-8")


def process_alive(pid: int) -> bool:
    """True while the OS still lists ``pid`` as a running process."""
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
    # A killed child of a killed parent is a zombie until something reaps it,
    # and a zombie still accepts signal 0; its exit status is all that is left.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return True
    return not stat.rpartition(") ")[2].startswith("Z")


def write_fake_droid(root: Path, behaviors: Sequence[Any]) -> Path:
    """Install the scripted fake droid under ``root`` and return its script."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / "fakes" / "droid.py"
    script = root / "fake-droid.py"
    script.write_bytes(source.read_bytes())
    (root / "fake-droid.json").write_text(
        json.dumps({"behaviors": list(behaviors)}), encoding="utf-8", newline="\n"
    )
    (root / "fake-droid-cursor.json").write_text(
        '{"launches": 0}', encoding="utf-8", newline="\n"
    )
    return script


class FakeDroid:
    """A scripted fake ``droid`` installed under one temporary root."""

    def __init__(self, root: Path, behaviors: Sequence[Any]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.script = write_fake_droid(self.root, behaviors)

    def set_behaviors(self, behaviors: Sequence[Any]) -> "FakeDroid":
        """Replace the scripted behaviors and rewind the behavior cursor."""
        write_fake_droid(self.root, behaviors)
        return self

    def log(self) -> list[dict[str, Any]]:
        """One record per launch, in order: argv, env, prompt and session id."""
        path = self.root / "fake-droid-argv.jsonl"
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def session_ids(self) -> list[str]:
        """The session id each launch reported, in order."""
        return [str(row.get("session_id")) for row in self.log()]


def write_record(
    store: Path,
    record_id: str,
    kind: str,
    data: dict[str, Any],
    *,
    at: str = "2026-10-28T00:00:00Z",
    actor: str = "cto",
    about: tuple[str, ...] = (),
    preset: str = "event",
) -> Path:
    """Write one JSON Record with a valid digest and return its path."""
    payload = {
        "id": record_id,
        "primitive": "record",
        "preset": preset,
        "kind": kind,
        "at": at,
        "actor": actor,
        "about": list(about),
        "data": data,
    }
    payload["digest"] = record_digest(payload)
    path = Path(store) / "records" / f"{at[:4]}-{at[5:7]}" / f"{record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    return path


def write_run(
    store: Path,
    run_id: str,
    *,
    item: str | None = "t0",
    role: str = "implementer",
    status: str = "verified",
    candidate: str | None = None,
    at: str = "2026-10-28T12:00:00Z",
    dispatch: str = "dispatch-1",
    report: str | None = None,
    accepted: bool | None = None,
    usage: dict[str, Any] | None = None,
    duration: float = 1.0,
    files_changed: Sequence[str] | None = None,
    symbols_changed: Sequence[str] | None = None,
) -> Path:
    """Write one completed-run Record with a valid payload and return its path.

    ``status`` is the run's own outcome; ``candidate`` lets a failing Check
    verdict bind to the run for triage weighting; ``report`` is the Record id
    the run points at, as a dispatch writes it; ``accepted`` is the run's
    outcome attribution when no evaluation exists; ``role`` names the run's own
    role, so a CTO tick run carries ``item: None`` as the design pins it;
    ``usage`` is the counters an attempt reported (absent counters stay
    ``unknown``) and ``duration`` is the run's measured seconds.
    """
    failed = status == "failed"
    counters = {"input_tokens": 2, "output_tokens": 3} if usage is None else usage
    return write_record(
        store,
        run_id,
        "run.completed",
        {
            "schema": 1,
            "dispatch_id": dispatch,
            "role": role,
            "actor": "cto",
            "profile": role,
            "profile_version": 1,
            "skills": [],
            "model": "custom:Deepseek-V4-flash-0",
            "item": item,
            "started_at": at,
            "ended_at": at,
            "duration_s": duration,
            "exit": 1 if failed else 0,
            "status": status,
            "attempts": [
                {
                    "number": 1,
                    "start": at,
                    "end": at,
                    "duration_s": duration,
                    "status": "failed" if failed else "completed",
                    "usage": dict(counters),
                }
            ],
            "candidate_commit": candidate,
            "accepted": accepted,
            "verdicts": [],
            "usage": dict(counters),
            "report": report,
            **({} if files_changed is None else {"files_changed": list(files_changed)}),
            **(
                {}
                if symbols_changed is None
                else {"symbols_changed": list(symbols_changed)}
            ),
        },
        at=at,
        about=() if item is None else (item,),
    )


def write_versioned_run(
    store: Path,
    run_id: str,
    *,
    item: str = "t0",
    role: str = "implementer",
    model: str = "custom:Deepseek-V4-flash-0",
    profile: str = "implementer",
    profile_version: int = 1,
    skills: Sequence[tuple[str, int]] = (),
    attempts: int = 1,
    duration_s: float = 60.0,
    accepted: bool | None = None,
    usage: dict[str, Any] | None = None,
    at: str = "2026-10-28T12:00:00Z",
) -> Path:
    """Write one completed run with skill pins and, when given, its evaluation."""
    counters = {} if usage is None else dict(usage)
    pins = [
        {"identity": identity, "version": version, "etag": f"{index:016x}"}
        for index, (identity, version) in enumerate(skills, start=1)
    ]
    path = write_record(
        store,
        run_id,
        "run.completed",
        {
            "schema": 1,
            "dispatch_id": f"dispatch-{run_id}",
            "role": role,
            "actor": "cto",
            "profile": profile,
            "profile_version": profile_version,
            "skills": pins,
            "model": model,
            "item": item,
            "started_at": at,
            "ended_at": at,
            "duration_s": duration_s,
            "exit": 0,
            "status": "completed",
            "attempts": [
                {
                    "number": number,
                    "start": at,
                    "end": at,
                    "duration_s": duration_s,
                    "status": "completed",
                    "usage": dict(counters),
                }
                for number in range(1, attempts + 1)
            ],
            "candidate_commit": None,
            "accepted": None,
            "verdicts": [],
            "usage": dict(counters),
            "report": None,
        },
        at=at,
        about=(item,),
    )
    if accepted is not None:
        write_record(
            store,
            f"{run_id}-eval",
            "run.evaluated",
            {
                "schema": 1,
                "run": run_id,
                "accepted": accepted,
                "at": at,
                "candidate_commit": None,
            },
            at=at,
            about=(item,),
        )
    return path


def records_of(store: Path, kind: str) -> list[dict[str, Any]]:
    """Every JSON Record of ``kind``, oldest path first."""
    found: list[dict[str, Any]] = []
    for path in sorted((Path(store) / "records").rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == kind:
            found.append(payload)
    return found


def item_status(run: Any) -> str:
    """The current status of one fixture's Item, read through the gateway."""
    from context_rail.gateway.gateway import invoke

    shown = invoke("show", {"slug": run.item}, store=run.store, actor="cto")
    if not shown["ok"]:
        raise AssertionError(shown)
    return shown["value"]["doc"]["status"]


def item_etag(run: Any) -> str:
    """The current etag of one fixture's Item, read through the gateway."""
    from context_rail.gateway.gateway import invoke

    shown = invoke("show", {"slug": run.item}, store=run.store, actor="cto")
    if not shown["ok"]:
        raise AssertionError(shown)
    return shown["value"]["doc"]["etag"]


def merge_call(run: Any, key: str, **extra: Any) -> dict[str, Any]:
    """Invoke ``rail loop merge`` for one fixture through the gateway as the CTO."""
    from context_rail.gateway.gateway import invoke

    args: dict[str, Any] = {
        "item": run.item,
        "run": run.run,
        "expect": run.etag,
        "key": key,
    }
    args.update(extra)
    return invoke("merge", args, store=run.store, actor="cto")


def review_call(run: Any, key: str, **extra: Any) -> dict[str, Any]:
    """Invoke ``rail loop review`` for one fixture through the gateway as the CTO."""
    from context_rail.gateway.gateway import invoke

    args: dict[str, Any] = {"item": run.item, "run": run.run, "key": key}
    args.update(extra)
    return invoke("review", args, store=run.store, actor="cto")


def latest_record(store: Path, kind: str) -> dict[str, Any]:
    """The newest JSON Record of ``kind`` by ``(at, id)``.

    Raises ``LookupError`` when the store holds no such Record.
    """
    found: list[dict[str, Any]] = []
    for path in (Path(store) / "records").rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == kind:
            found.append(payload)
    if not found:
        raise LookupError(f"no {kind} record")
    return max(found, key=lambda row: (str(row.get("at") or ""), str(row.get("id") or "")))


def default_head(run: Any) -> str:
    """The canonical default branch HEAD of one dispatch or merge fixture."""
    return run_git(Path(run.project), "rev-parse", "HEAD").stdout.strip()


def remote_head(run: Any) -> str | None:
    """The bare remote's default branch HEAD, or None when it has no such ref."""
    remote = getattr(run, "remote", None)
    if remote is None:
        return None
    listed = subprocess.run(
        [
            "git",
            "-C",
            str(remote),
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/heads/{run.branch}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return listed.stdout.strip() or None


def advance_default_branch(run: Any, filename: str) -> str:
    """Commit one human change on the canonical default branch and return it."""
    project = Path(run.project)
    target = project / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("human work\n", encoding="utf-8", newline="\n")
    run_git(project, "add", "--", filename)
    run_git(project, "commit", "-m", f"human: {filename}")
    return default_head(run)


def touch_doc(store: Path, rel: str, **frontmatter: Any) -> None:
    """Replace frontmatter keys in an existing document and bump its mtime.

    The body is preserved and the mtime moves two seconds ahead, so a change is
    visible even on filesystems with coarse timestamps.
    """
    path = Path(store) / rel
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{rel} has no frontmatter")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
    payload = dict(load_yaml("\n".join(lines[1:end])) or {})
    payload.update(frontmatter)
    body = "\n".join(lines[end + 1 :])
    path.write_text(
        "---\n" + dump_frontmatter(payload) + "---\n" + body,
        encoding="utf-8",
        newline="\n",
    )
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


def approve_picture(
    store: Path, *, confirmer: str = "validator", expect: str | None = None
) -> dict[str, Any]:
    """Approve the current picture as the human, sending the displayed digest.

    ``expect`` defaults to the ``snapshot_digest`` ``show charter`` displays, so
    a re-approval after a mismatch only needs the new digest passed in.
    """
    from context_rail.gateway.gateway import invoke

    shown = invoke("show", {"slug": "charter"}, store=store, actor="human")
    if expect is None:
        expect = shown["value"]["approval"]["snapshot_digest"]
    return invoke(
        "approve",
        {
            "target": "charter",
            "confirm": "APPROVE charter",
            "confirmer": confirmer,
            "expect": expect,
        },
        store=store,
        actor="human",
    )


def make_pilot_repo(root: Path) -> Any:
    """Create the pilot project and its bare remote under ``root`` in-process."""
    from support.pilot_repo import build_pilot

    return build_pilot(Path(root))


def seed_activity(store: Path, *, stuck: str, too_long: str) -> Any:
    """Seed old activity and an over-budget run into one pilot store."""
    from support.seed_activity import seed_activity as seed

    return seed(Path(store), stuck=stuck, too_long=too_long)


def median_ms(fn: Callable[[], object], runs: int) -> float:
    """Median wall time of ``runs`` calls to ``fn``, in milliseconds."""
    samples: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def _move_worker(
    store: str,
    slug: str,
    etag: str,
    actor: str,
    barrier: Any,
    results: Any,
) -> None:
    """One spawned mover: announce readiness, wait for the release, then move."""
    from context_rail.gateway.gateway import invoke

    results.put(("ready", actor))
    try:
        barrier.wait(timeout=_STARTUP_TIMEOUT)
        result = invoke(
            "move",
            {"slug": slug, "status": "doing", "expect": etag},
            store=Path(store),
            actor=actor,
        )
    except BaseException as exc:  # surface a child fault in the parent
        results.put(("failed", actor, f"{type(exc).__name__}: {exc}"))
        raise
    results.put(("result", actor, result))


def parallel_moves(store: Path, slug: str, etag: str, n: int) -> list[dict[str, Any]]:
    """Run ``n`` simultaneous ``move`` calls that all expect the same etag.

    The workers spawn, announce readiness, and are released together by a
    barrier; every child is joined, a straggler is terminated, and a child
    exception or a missing result fails the call instead of being hidden.
    """
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    barrier = context.Barrier(n + 1)
    workers = [
        context.Process(
            target=_move_worker,
            args=(str(store), slug, etag, f"w{index}", barrier, results),
            name=f"mover-{index}",
        )
        for index in range(n)
    ]
    for worker in workers:
        worker.start()
    try:
        _await_readiness(results, n)
        barrier.wait(timeout=_STARTUP_TIMEOUT)
        collected = _collect_results(results, n)
    finally:
        for worker in workers:
            worker.join(timeout=_JOIN_TIMEOUT)
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=_JOIN_TIMEOUT)
    return collected


def _await_readiness(results: Any, n: int) -> None:
    """Wait until every worker announced it is ready to be released."""
    seen = 0
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while seen < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"only {seen} of {n} movers became ready in time")
        try:
            message = results.get(timeout=remaining)
        except queue_module.Empty:
            raise AssertionError(f"only {seen} of {n} movers became ready in time") from None
        if message[0] != "ready":
            raise AssertionError(f"a mover failed before the release: {message!r}")
        seen += 1


def _collect_results(results: Any, n: int) -> list[dict[str, Any]]:
    """Collect every worker's Result, failing on a child fault or a shortfall."""
    collected: list[dict[str, Any]] = []
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while len(collected) < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"only {len(collected)} of {n} movers reported a result")
        try:
            message = results.get(timeout=remaining)
        except queue_module.Empty:
            raise AssertionError(
                f"only {len(collected)} of {n} movers reported a result"
            ) from None
        if message[0] == "failed":
            raise AssertionError(f"mover {message[1]} failed: {message[2]}")
        if message[0] != "result":
            raise AssertionError(f"unexpected mover message: {message!r}")
        collected.append(message[2])
    return collected


def init_store(tmp_path: Path, pack: str = "basic") -> Path:
    """Create a fresh store under ``tmp_path`` and return its directory."""
    from context_rail.gateway.gateway import invoke

    result = invoke(
        "init",
        {"dir": str(tmp_path), "pack": pack, "scaffold": True},
        actor="human",
    )
    assert result["ok"] is True, result
    return Path(result["value"]["store"])


def write_pack(store: Path, name: str, document: dict[str, Any]) -> Path:
    """Write ``packs/<name>/pack.yaml`` from ``document`` and return its path."""
    path = Path(store) / "packs" / name / "pack.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return path


def compiled_model(store: Path, name: str = "basic") -> Any:
    """Load a pack and its ``surface.yaml`` and return the compiled ProjectModel.

    No index is opened, so a test can compile a pack in isolation.
    """
    from context_rail.model.compiler import load_project

    return load_project(name, Path(store))


NESTED_LAYOUT: dict[str, str] = {
    "phase": "items/{preset}/{slug}.md",
    "sprint": "{part_of}/{slug}.md",
    "task": "{part_of}/{field:area}/{slug}.md",
    "result": "records/{about}/{id}.json",
}


def nested_pack_document(
    name: str = "nested", extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A pack with phase, sprint, an ``area``-carrying task and a result record."""
    document: dict[str, Any] = {
        "name": name,
        "version": 1,
        "statuses": {"todo": "not_started", "done": "done"},
        "initial": "todo",
        "transitions": {"todo": ["done"], "done": []},
        "presets": {
            "phase": {"primitive": "item", "tags": ["phase"], "container": True},
            "sprint": {"primitive": "item", "tags": ["sprint"], "container": True},
            "task": {
                "primitive": "item",
                "tags": ["task"],
                "container": False,
                "required": ["area"],
                "defaults": {"area": "general"},
                "fields": {"area": ["frontend", "backend", "general"]},
            },
            "result": {"primitive": "record", "tags": ["result"]},
            "decision": {"primitive": "record", "tags": ["decision"]},
            "event": {"primitive": "record", "tags": ["event"]},
            "note": {"primitive": "note", "tags": []},
            "check": {"primitive": "check", "tags": []},
            "charter": {"primitive": "policy", "tags": []},
            "goal": {"primitive": "item", "tags": ["goal"], "container": True},
            "scope": {"primitive": "item", "tags": ["scope"], "container": True},
            "vision": {"primitive": "note", "tags": []},
            "wont": {"primitive": "note", "tags": []},
            "skill": {"primitive": "note", "tags": []},
            "cto": {"primitive": "actor", "tags": []},
            "profile": {"primitive": "actor", "tags": []},
        },
        "policy": {"stuck_hours": 24, "retries": 1},
    }
    if extra:
        document["presets"].update(extra)
    return document


def copy_pack_templates(store: Path, name: str) -> None:
    """Copy the built-in Markdown templates into a store-local pack."""
    from importlib import resources

    templates = Path(store) / "packs" / name / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    source = resources.files("context_rail").joinpath("packs", "basic", "templates")
    for item in source.iterdir():
        if item.name.endswith(".md"):
            templates.joinpath(item.name).write_text(
                item.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
            )


def workflow_pack_document(name: str = "twoways") -> dict[str, Any]:
    """A pack with a ``sprint`` and a ``bugfix`` workflow over one status map."""
    document = nested_pack_document(name)
    document["statuses"] = {
        "todo": "not_started",
        "doing": "active",
        "fixing": "active",
        "done": "done",
    }
    document["initial"] = "todo"
    document["transitions"] = {"todo": ["done"], "done": []}
    document["workflows"] = {
        "sprint": {
            "initial": "todo",
            "transitions": {"todo": ["doing"], "doing": ["done"], "done": []},
        },
        "bugfix": {
            "initial": "todo",
            "transitions": {"todo": ["fixing"], "fixing": ["done"], "done": []},
        },
    }
    document["presets"]["task"]["workflow"] = "sprint"
    document["presets"]["bug"] = {
        "primitive": "item",
        "tags": ["bug"],
        "workflow": "bugfix",
    }
    return document


def init_pack_store(
    tmp_path: Path,
    document: dict[str, Any],
    *,
    layout: dict[str, str] | None = None,
) -> Path:
    """Initialize a store on a local pack document and return its directory."""
    from context_rail.gateway.gateway import invoke

    name = document["name"]
    store = Path(tmp_path) / "context-rail"
    write_pack(store, name, document)
    copy_pack_templates(store, name)
    if layout is not None:
        (store / "packs" / name / "layout.yaml").write_text(
            yaml.safe_dump({"presets": dict(layout)}, sort_keys=False),
            encoding="utf-8",
            newline="\n",
        )
    result = invoke(
        "init",
        {"dir": str(tmp_path), "pack": name, "scaffold": True},
        actor="human",
    )
    assert result["ok"] is True, result
    return Path(result["value"]["store"])


def init_nested_store(
    tmp_path: Path,
    *,
    name: str = "nested",
    layout: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Initialize a store on the nested pack, seeded with a structural layout."""
    return init_pack_store(
        tmp_path,
        nested_pack_document(name, extra),
        layout=NESTED_LAYOUT if layout is None else layout,
    )


def set_preset_placement(store: Path, **presets: str) -> None:
    """Rewrite a store's ``rail.yaml`` preset placement mapping in place."""
    path = Path(store) / "rail.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["presets"].update(presets)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8", newline="\n"
    )


class InjectedFault(RuntimeError):
    """Raised by ``inject_fault`` in ``raise`` mode."""


def record_inventory(store: Path) -> dict[str, tuple[str, str]]:
    """Map every Record id to its store-relative path and SHA-256 of its bytes."""
    from context_rail.store.doc import parse_doc

    found: dict[str, tuple[str, str]] = {}
    root = Path(store) / "records"
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".md"):
            continue
        data = path.read_bytes()
        rel = path.relative_to(store).as_posix()
        try:
            if path.suffix == ".json":
                payload = json.loads(data.decode("utf-8"))
                record_id = payload.get("id") if isinstance(payload, dict) else None
            else:
                record_id = parse_doc(rel, data).fields.get("id")
        except (OSError, ValueError):
            continue
        if isinstance(record_id, str) and record_id:
            found[record_id] = (rel, hashlib.sha256(data).hexdigest())
    return found


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` keyed by its relative POSIX path."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(Path(root).rglob("*"))
        if path.is_file()
    }


def ui_call(
    base: str,
    path: str,
    *,
    token: str | None,
    method: str = "GET",
    body: Any = None,
    host: str | None = None,
) -> tuple[int, Any, dict[str, str]]:
    """One request to the loopback UI: status, decoded payload and headers."""
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Rail-Token"] = token
    if host is not None:
        headers["Host"] = host
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        returned = {name.lower(): value for name, value in response.getheaders()}
    finally:
        connection.close()
    try:
        decoded = json.loads(raw)
    except ValueError:
        decoded = raw.decode("utf-8", "replace")
    return response.status, decoded, returned


def ui_oversize_probe(base: str, token: str, size: int) -> int:
    """Announce a ``size``-byte body without sending it; return the status code."""
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        connection.putrequest("POST", "/api/tool/find")
        connection.putheader("X-Rail-Token", token)
        connection.putheader("Content-Length", str(size))
        connection.putheader("Content-Type", "application/json")
        connection.endheaders()
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


@contextmanager
def serve_ui(store: Path, *, actor: str = "human") -> Iterator[Any]:
    """Run the loopback UI on an ephemeral port for the duration of the block."""
    import threading

    from context_rail.ui.server import create_server

    created = create_server(store, actor=actor, port=0)
    thread = threading.Thread(target=created.server.serve_forever, daemon=True)
    thread.start()
    try:
        yield created
    finally:
        created.server.shutdown()
        created.server.server_close()
        thread.join(5)


def bump_versioned(
    store: Path, slug: str, *, body_file: Path, actor: str = "cto"
) -> dict[str, Any]:
    """Read the current etag through ``show`` and update the body with it."""
    from context_rail.gateway.gateway import invoke

    shown = invoke("show", {"slug": slug}, store=store, actor=actor)
    etag = shown["value"]["doc"]["etag"]
    return invoke(
        "update",
        {"slug": slug, "expect": etag, "body_file": str(body_file)},
        store=store,
        actor=actor,
    )


def twin_stores(tmp_path: Path, fixed_clock: Any) -> Any:
    """Two stores populated by one identical, fixed-clock invoke sequence."""
    from types import SimpleNamespace

    from context_rail.gateway.gateway import invoke

    def populate(store: Path) -> None:
        for title in ("Login", "Parity Task"):
            result = invoke(
                "add", {"preset": "task", "title": title}, store=store, actor="cto"
            )
            assert result["ok"] is True, result
        skill = invoke(
            "add",
            {
                "preset": "skill",
                "title": "test-first",
                "set": ["version=1", "description=Write the failing test first"],
            },
            store=store,
            actor="cto",
        )
        assert skill["ok"] is True, skill

    stores = []
    for name in ("a", "b"):
        fixed_clock.reset()
        store = init_store(tmp_path / name)
        fixed_clock.reset()
        populate(store)
        stores.append(store)
    return SimpleNamespace(a=stores[0], b=stores[1])


def mcp_call(
    store: Path,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    actor: str | None = None,
) -> dict[str, Any]:
    """One in-memory MCP tool call, with a protocol error reported separately."""
    import anyio
    from mcp.client import Client
    from mcp.shared.exceptions import MCPError

    from context_rail.gateway.mcp_server import build_server

    outcome: dict[str, Any] = {
        "is_error": False,
        "structured": None,
        "text": "",
        "rpc_error": None,
    }

    async def scenario() -> None:
        async with Client(
            build_server(store=store, actor=actor), raise_exceptions=True
        ) as client:
            try:
                response = await client.call_tool(name, arguments or {})
            except MCPError as exc:
                outcome["rpc_error"] = exc.code
                return
            outcome["is_error"] = bool(response.is_error)
            outcome["structured"] = response.structured_content
            outcome["text"] = response.content[0].text if response.content else ""

    saved = os.environ.pop("RAIL_ACTOR", None)
    try:
        anyio.run(scenario)
    finally:
        if saved is not None:
            os.environ["RAIL_ACTOR"] = saved
    return outcome


def mcp_list(store: Path, *, actor: str | None = None) -> list[dict[str, Any]]:
    """Every tool one in-memory MCP server lists."""
    import anyio
    from mcp.client import Client

    from context_rail.gateway.mcp_server import build_server

    found: list[dict[str, Any]] = []

    async def scenario() -> None:
        async with Client(
            build_server(store=store, actor=actor), raise_exceptions=True
        ) as client:
            response = await client.list_tools()
            for tool in response.tools:
                annotations = tool.annotations
                found.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                        "meta": tool.meta,
                        "annotations": (
                            annotations.model_dump(by_alias=True)
                            if annotations is not None
                            else None
                        ),
                    }
                )

    saved = os.environ.pop("RAIL_ACTOR", None)
    try:
        anyio.run(scenario)
    finally:
        if saved is not None:
            os.environ["RAIL_ACTOR"] = saved
    return found


def stdio_session(store: Path, *, actor: str | None = None) -> Any:
    """An initialized client session against ``context_rail mcp serve``."""
    import contextlib
    import os

    @contextlib.asynccontextmanager
    async def session():
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        env = dict(os.environ)
        env["PYTHONPATH"] = str(_REPO_SRC)
        env.pop("RAIL_ACTOR", None)
        command = [sys.executable, "-m", "context_rail", "--store", str(store)]
        if actor is not None:
            command += ["--actor", actor]
        command += ["mcp", "serve"]
        params = StdioServerParameters(command=command[0], args=command[1:], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                yield client

    return session()


def require_git_identity() -> None:
    """Skip a commit-producing test when no Git author identity is configured."""
    probe = subprocess.run(
        ["git", "var", "GIT_COMMITTER_IDENT"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode != 0:
        pytest.skip("git author identity is not configured")


def git(cwd: Path, *args: str) -> str:
    """Run one git command and return its stdout, as the plan's snippets expect."""
    return run_git(cwd, *args).stdout
