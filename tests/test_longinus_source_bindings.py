import hashlib

from ooptdd.engine.gate import evaluate_events, green_banner


def _sha(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return hashlib.sha256(("\n".join(lines).rstrip("\n") + "\n").encode()).hexdigest()


def test_require_source_bindings_blocks_source_less_green():
    spec = {
        "cid": "c1",
        "require_source_bindings": True,
        "expect": [{"event": "payment_authorized", "op": ">=", "count": 1}],
    }

    res = evaluate_events(
        spec,
        [{"event": "payment_authorized", "_timestamp": 1}],
        reachable=True,
    )

    assert res["checks"][0]["passed"] is True
    assert res["source_unbound"] is True
    assert res["ok"] is False
    assert res["longinus"]["missing"] == ["payment_authorized"]


def test_source_binding_resolves_real_symbol(tmp_path, monkeypatch):
    src = tmp_path / "app.py"
    src.write_text(
        "def emit_payment_authorized():\n"
        "    return {'event': 'payment_authorized'}\n"
    )
    monkeypatch.chdir(tmp_path)
    spec = {
        "cid": "c1",
        "require_source_bindings": True,
        "source_bindings": {
            "payment_authorized": {
                "path": "app.py",
                "symbol": "emit_payment_authorized",
            }
        },
        "expect": [{"event": "payment_authorized", "op": ">=", "count": 1}],
    }

    res = evaluate_events(
        spec,
        [{"event": "payment_authorized", "_timestamp": 1}],
        reachable=True,
    )

    assert res["ok"] is True
    assert res["source_unbound"] is False
    assert res["longinus"]["bound"] == 1
    assert "Longinus: 1/1 required event binding(s) resolved." in green_banner(res)


def test_source_binding_detects_symbol_sha_drift(tmp_path, monkeypatch):
    src = tmp_path / "app.py"
    original = "def emit_payment_authorized():\n    return {'event': 'payment_authorized'}\n"
    src.write_text(
        "def emit_payment_authorized():\n"
        "    return {'event': 'payment_authorized_v2'}\n"
    )
    monkeypatch.chdir(tmp_path)
    spec = {
        "cid": "c1",
        "require_source_bindings": True,
        "source_bindings": [{
            "event": "payment_authorized",
            "path": "app.py",
            "symbol": "emit_payment_authorized",
            "sha256": _sha(original),
        }],
        "expect": [{"event": "payment_authorized", "op": ">=", "count": 1}],
    }

    res = evaluate_events(
        spec,
        [{"event": "payment_authorized", "_timestamp": 1}],
        reachable=True,
    )

    assert res["ok"] is False
    assert res["source_unbound"] is True
    assert res["longinus"]["drifted"][0]["reason"] == "symbol_sha256_drift"
