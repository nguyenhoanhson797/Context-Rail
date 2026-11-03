"""Stage 3 refusals write nothing and never expose a second transport."""

from __future__ import annotations

import json
from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.ui.server import MAX_BODY
from helpers import init_store, serve_ui, tree_bytes, ui_call, ui_oversize_probe


def _refused(result: dict, code: str) -> None:
    assert is_ok(result) is False, result
    assert result["error"]["code"] == code, result


def test_migration_sources_outside_the_contract_write_nothing(
    legacy_v2_store: Path, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    before = tree_bytes(legacy_v2_store)

    v3 = tmp_path / "v3"
    v3.mkdir()
    (v3 / "rail.yaml").write_text(
        "schema: 3\npack: small\nplacement: {}\npresets: {}\n", encoding="utf-8"
    )
    _refused(
        invoke(
            "migrate",
            {"source": str(v3), "dir": str(target)},
            actor="human",
        ),
        "VALIDATION_FAILED",
    )

    unknown = tmp_path / "unknown"
    unknown.mkdir()
    (unknown / "rail.json").write_text(
        json.dumps({"schema_version": 9}), encoding="utf-8"
    )
    _refused(
        invoke(
            "migrate",
            {"source": str(unknown), "dir": str(target)},
            actor="human",
        ),
        "SCHEMA_UNSUPPORTED",
    )

    nested = tmp_path / "nested"
    (nested / "legacy").mkdir(parents=True)
    _refused(
        invoke(
            "migrate",
            {"source": str(nested / "legacy"), "dir": str(nested), "root": "."},
            actor="human",
        ),
        "FORBIDDEN",
    )

    _refused(
        invoke(
            "migrate",
            {"source": str(legacy_v2_store), "dir": str(target)},
            actor="cto",
        ),
        "PERMISSION_DENIED",
    )

    assert not target.exists()
    assert tree_bytes(legacy_v2_store) == before


def test_the_ui_binds_loopback_and_refuses_a_second_transport(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    with serve_ui(store) as ui:
        assert ui.base.startswith("http://127.0.0.1:")
        assert ui.server.server_address[0] == "127.0.0.1"

        status, payload, _ = ui_call(ui.base, "/api/context", token=None)
        assert status == 401
        assert payload["code"] == "PERMISSION_DENIED"

        for name in ("mcp%20serve", "ui%20serve"):
            status, payload, _ = ui_call(
                ui.base,
                f"/api/tool/{name}",
                token=ui.token,
                method="POST",
                body={"args": {}},
            )
            assert status == 403
            assert payload["code"] == "FORBIDDEN"

        assert ui_oversize_probe(ui.base, ui.token, MAX_BODY + 1) == 413
