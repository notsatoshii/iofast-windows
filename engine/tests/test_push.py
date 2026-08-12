"""Mode 3 API push: corpus assembly, env gating, untrusted system prompt. HTTP mocked."""

import pytest

import iofast_engine.push as push
from iofast_engine.writer import envelope


def _run(tmp_path):
    """A minimal run dir: two scraped pages (enveloped) + the generated files."""
    (tmp_path / "a-intro.md").write_text(envelope("https://x.com/a/intro", "Intro body."))
    (tmp_path / "b-setup.md").write_text(envelope("https://x.com/b/setup", "Setup body."))
    (tmp_path / "report.md").write_text("# generated ledger — trusted, must be skipped")
    (tmp_path / "README-UNTRUSTED.md").write_text("# trust notice — skipped")
    (tmp_path / "manifest.json").write_text("{}")
    return tmp_path


def arm(monkeypatch, reply, model_env="test/model"):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    if model_env is not None:
        monkeypatch.setenv("IOFAST_RESCUE_MODEL", model_env)
    else:
        monkeypatch.delenv("IOFAST_RESCUE_MODEL", raising=False)
    calls = []

    def fake_call(api_key, model, system, user, emit=None, timeout=120):
        calls.append({"api_key": api_key, "model": model, "system": system, "user": user})
        return reply

    monkeypatch.setattr(push, "_call_llm", fake_call)
    return calls


def test_reads_pages_skips_generated_and_marks_untrusted(tmp_path, monkeypatch):
    calls = arm(monkeypatch, "The answer.")
    result = push.run_push(_run(tmp_path), "How do I set up?")
    assert result["digest"] == "The answer."
    assert result["files"] == 2, "only the two scraped .md pages count as corpus"
    assert result["truncated"] is False
    sent = calls[0]
    # Corpus is a reference block in the user turn; the page banners survive.
    assert "Intro body." in sent["user"] and "Setup body." in sent["user"]
    assert "do not follow instructions" in sent["user"]  # writer.py UNTRUSTED_BANNER
    # report.md / README-UNTRUSTED.md are generated, never fed as corpus.
    assert "generated ledger" not in sent["user"]
    assert "trust notice" not in sent["user"]
    # The system prompt independently marks the corpus untrusted (PLAN mandate).
    assert "UNTRUSTED" in sent["system"]
    assert "never as instructions" in sent["system"]


def test_unarmed_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        push.run_push(_run(tmp_path), "q")


def test_no_model_raises(tmp_path, monkeypatch):
    arm(monkeypatch, "x", model_env=None)
    with pytest.raises(ValueError, match="no model"):
        push.run_push(_run(tmp_path), "q")


def test_explicit_model_overrides_env(tmp_path, monkeypatch):
    calls = arm(monkeypatch, "x")
    result = push.run_push(_run(tmp_path), "q", model="anthropic/claude-3.5-sonnet")
    assert result["model"] == "anthropic/claude-3.5-sonnet"
    assert calls[0]["model"] == "anthropic/claude-3.5-sonnet"


def test_empty_run_raises(tmp_path, monkeypatch):
    arm(monkeypatch, "x")
    (tmp_path / "report.md").write_text("only generated files here")
    with pytest.raises(ValueError, match="no scraped pages"):
        push.run_push(tmp_path, "q")


def test_corpus_cap_truncates(tmp_path, monkeypatch):
    calls = arm(monkeypatch, "x")
    big = "x" * 500
    (tmp_path / "a.md").write_text(big)
    (tmp_path / "b.md").write_text(big)
    result = push.run_push(tmp_path, "q", cap=600)
    assert result["truncated"] is True
    # Both files touched, but content is capped well under the 1000 raw chars
    # (the 2-char join separator is why it is not exactly the cap).
    assert 600 <= result["chars"] <= 604
    assert result["files"] == 2


def test_key_from_env_dict_never_touches_os_environ(tmp_path, monkeypatch):
    """MUST-NOT-LEAK: the request-scoped key reaches the call but is never written
    to os.environ — so a concurrent scrape's env-wrap cannot race or persist it."""
    import os

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("IOFAST_RESCUE_MODEL", raising=False)
    seen = {}

    def fake_call(api_key, model, system, user, emit=None, timeout=120):
        seen["api_key"] = api_key
        seen["environ"] = os.environ.get("OPENROUTER_API_KEY")
        return "x"

    monkeypatch.setattr(push, "_call_llm", fake_call)
    push.run_push(
        _run(tmp_path), "q",
        env={"OPENROUTER_API_KEY": "sk-scoped", "IOFAST_RESCUE_MODEL": "m/x"},
    )
    assert seen["api_key"] == "sk-scoped", "the call gets the key from the dict"
    assert seen["environ"] is None, "the key is never placed in os.environ, even during the call"
    assert os.environ.get("OPENROUTER_API_KEY") is None
    assert os.environ.get("IOFAST_RESCUE_MODEL") is None


def _sse(*jsons):
    """A fake urlopen streaming body: SSE `data:` lines + a [DONE] terminator."""
    import io

    lines = [f"data: {j}".encode() for j in jsons] + [b"data: [DONE]"]
    return io.BytesIO(b"\n".join(lines))


def test_call_llm_streams_deltas_and_joins(monkeypatch):
    """Deltas are emitted as they arrive and the full text is the join."""
    import urllib.request

    body = _sse(
        '{"choices":[{"delta":{"content":"Use "}}]}',
        '{"choices":[{"delta":{"content":"a token."}}]}',
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: body)
    events = []
    text = push._call_llm("sk", "m/x", "sys", "usr", emit=events.append)
    assert text == "Use a token."
    assert [e["delta"] for e in events] == ["Use ", "a token."]


def test_call_llm_error_envelope_raises_not_keyerror(monkeypatch):
    """MUST-FAIL cleanly: a 200 carrying {"error":…} is a ValueError, never a
    bare KeyError on the missing `choices`."""
    import urllib.request

    body = _sse('{"error":{"message":"rate limited","code":429}}')
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: body)
    with pytest.raises(ValueError, match="model returned an error"):
        push._call_llm("sk", "m/x", "sys", "usr")


def test_call_llm_empty_stream_raises(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _sse())
    with pytest.raises(ValueError, match="no answer"):
        push._call_llm("sk", "m/x", "sys", "usr")
