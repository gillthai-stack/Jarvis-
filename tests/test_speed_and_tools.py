"""The speed changes (notes and time in the prompt, fast mode, request log) and the new everyday tools."""

import asyncio
import json
import platform
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from fakes import FakeAnthropic, text, tool_use
from test_computer_use import Harness

from jarvis import agent, config, mac_tools, tools
from jarvis.agent import Jarvis


def ask(jarvis, prompt, h):
    asyncio.run(jarvis.ask(prompt, h.emit))


def test_saved_notes_and_time_come_with_the_request_so_no_extra_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MEMORY_FILE", tmp_path / "memory.json")
    (tmp_path / "memory.json").write_text(json.dumps([{"note": "Gill likes tea", "saved": "2026-10-26 09:00"}]))
    h = Harness(tmp_path)
    fake = FakeAnthropic([lambda p: [text("Tea it is.")], lambda p: [text("Still tea.")]])
    jarvis = Jarvis(client=fake, computer=h.cu)
    ask(jarvis, "What do I like?", h)
    first = fake.calls[0]
    assert "Gill likes tea" in first["system"]
    assert "call recall" not in first["system"]
    stamp, said = first["messages"][0]["content"]
    assert stamp["text"].startswith("[") and said["text"] == "What do I like?"
    # A note saved mid-conversation doesn't change the system prompt, so the prompt cache keeps working.
    (tmp_path / "memory.json").write_text(json.dumps([{"note": "changed", "saved": "now"}]))
    ask(jarvis, "And?", h)
    assert fake.calls[1]["system"] == first["system"]
    jarvis.reset()
    assert jarvis.system is None


def test_progress_updates_and_fallbacks_on_opus_and_sonnet_only(tmp_path):
    h = Harness(tmp_path)
    fake = FakeAnthropic([lambda p: [text("Hi.")], lambda p: [text("Hi.")]])
    jarvis = Jarvis(client=fake, computer=h.cu)
    jarvis.model = "claude-sonnet-5-5"
    ask(jarvis, "hello", h)
    call = fake.calls[0]
    assert call["thinking"] == {"type": "adaptive", "display": "updates"}
    assert "thinking-display-updates-2026-08-18" in call["betas"] and call["fallbacks"] == "default"
    assert "speed" not in call
    jarvis.model = "claude-haiku-5-5"
    ask(jarvis, "hello", h)
    assert "betas" not in fake.calls[1] and fake.calls[1]["thinking"]["display"] == "summarized"


def test_fast_mode_only_on_opus_and_falls_back_when_unavailable(tmp_path):
    h = Harness(tmp_path)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def limited(p):
        raise anthropic.RateLimitError("fast limit", response=httpx.Response(429, request=request), body=None)

    fake = FakeAnthropic([limited, lambda p: [text("Done at normal speed.")]])
    jarvis = Jarvis(client=fake, computer=h.cu)
    jarvis.model, jarvis.fast = "claude-opus-5-5", True
    ask(jarvis, "hello", h)
    assert fake.calls[0]["speed"] == "fast" and "fast-mode-2026-02-01" in fake.calls[0]["betas"]
    assert "speed" not in fake.calls[1]
    assert any(e["type"] == "notice" for e in h.events)
    assert not any(e["type"] == "error" for e in h.events)

    fake2 = FakeAnthropic([lambda p: [text("Hi.")]])
    jarvis2 = Jarvis(client=fake2, computer=h.cu)
    jarvis2.model, jarvis2.fast = "claude-sonnet-5-5", True
    ask(jarvis2, "hello", h)
    assert "speed" not in fake2.calls[0]


def test_timing_is_reported_and_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "REQUEST_LOG", tmp_path / "requests.jsonl")
    h = Harness(tmp_path)
    fake = FakeAnthropic([lambda p: [tool_use("get_time", {})], lambda p: [text("It's late.")]])
    jarvis = Jarvis(client=fake, computer=h.cu)
    ask(jarvis, "what time is it", h)
    timing = next(e for e in h.events if e["type"] == "timing")
    assert timing["steps"] == 2 and timing["total_ms"] >= 0 and timing["first_text_ms"] is not None
    entry = json.loads((tmp_path / "requests.jsonl").read_text().splitlines()[-1])
    assert entry["request"] == "what time is it" and entry["tools"] == ["get_time"] and entry["error"] is None
    assert h.events[-1] == {"type": "done"}


def test_longer_tasks_are_allowed():
    assert agent.MAX_TURNS >= 50
    assert config.DEFAULT_EFFORT in config.EFFORTS


# ---------- files ----------

def test_open_file_only_opens_documents_in_allowed_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_DIRS", [tmp_path])
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    opened = []
    monkeypatch.setattr(tools.subprocess, "run", lambda argv, **kw: opened.append(argv))
    (tmp_path / "report.pdf").write_text("x")
    (tmp_path / "evil.command").write_text("rm -rf ~")
    (tmp_path / "run.sh").write_text("echo hi")
    assert tools.open_file({"path": str(tmp_path / "report.pdf")}) == "Opened report.pdf."
    for bad in ("evil.command", "run.sh"):
        with pytest.raises(PermissionError):
            tools.open_file({"path": str(tmp_path / bad)})
    # Revealing in Finder is fine for any file; it doesn't run it.
    tools.open_file({"path": str(tmp_path / "run.sh"), "reveal": True})
    with pytest.raises(PermissionError):
        tools.open_file({"path": "/etc/hosts"})
    assert opened == [["open", str(tmp_path / "report.pdf")], ["open", "-R", str(tmp_path / "run.sh")]]


def test_search_uses_spotlight_on_a_mac_and_falls_back_to_walking(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_DIRS", [tmp_path])
    (tmp_path / "Budget 2026.xlsx").write_text("x")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "budget.txt").write_text("x")
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    calls = []

    def mdfind(argv, **kw):
        calls.append(argv)
        out = f"{tmp_path / 'Budget 2026.xlsx'}\n{tmp_path / '.hidden' / 'budget.txt'}\n"
        return SimpleNamespace(returncode=0, stdout=out)

    monkeypatch.setattr(tools.subprocess, "run", mdfind)
    hits = json.loads(tools.search_files({"query": "budget"}))
    assert [h["path"] for h in hits] == [str(tmp_path / "Budget 2026.xlsx")]
    assert calls == [["mdfind", "-onlyin", str(tmp_path), "-name", "budget"]]
    # A query that looks like an option is never passed to mdfind.
    calls.clear()
    tools.search_files({"query": "-onlyin"})
    assert calls == []
    # Off the Mac, the folder walk still works.
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    hits = json.loads(tools.search_files({"query": "budget"}))
    assert [h["path"] for h in hits] == [str(tmp_path / "Budget 2026.xlsx")]


def test_number_inputs_validate():
    assert tools.validate("set_timer", {"minutes": 0.5}) is None
    assert tools.validate("set_timer", {"minutes": 5}) is None
    assert tools.validate("set_timer", {"minutes": True}) is not None
    assert tools.validate("set_timer", {"minutes": "5"}) is not None


# ---------- timers and clipboard ----------

def test_timers_ring_and_can_be_cancelled(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    rung = []
    monkeypatch.setattr(mac_tools, "_ring", lambda timer_id: rung.append(timer_id))
    out = mac_tools.set_timer({"minutes": 10, "label": "pasta"})
    timer_id = out.split()[1]
    assert "pasta" in mac_tools.list_timers({})
    assert mac_tools.cancel_timer({"id": timer_id}) == f"Cancelled {timer_id}."
    assert mac_tools.list_timers({}) == "No timers are running."
    with pytest.raises(ValueError):
        mac_tools.set_timer({"minutes": 0})
    with pytest.raises(ValueError):
        mac_tools.set_timer({"minutes": 2000})
    with pytest.raises(ValueError):
        mac_tools.cancel_timer({"id": "timer999"})


def test_clipboard_text_goes_through_stdin(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    seen = []
    monkeypatch.setattr(mac_tools.subprocess, "run", lambda argv, **kw: seen.append((argv, kw.get("input"))))
    mac_tools.copy_to_clipboard({"text": "$(rm -rf ~)"})
    assert seen == [(["pbcopy"], "$(rm -rf ~)")]
    with pytest.raises(ValueError):
        mac_tools.copy_to_clipboard({"text": "x" * 30_000})


def test_refused_new_options_fall_back_to_the_old_settings(tmp_path):
    h = Harness(tmp_path)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def refused(p):
        raise anthropic.BadRequestError("unknown display", response=httpx.Response(400, request=request), body=None)

    fake = FakeAnthropic([refused, lambda p: [text("Hi.")], lambda p: [text("Hi again.")]])
    jarvis = Jarvis(client=fake, computer=h.cu)
    jarvis.model = "claude-opus-5-5"
    ask(jarvis, "hello", h)
    assert fake.calls[1]["thinking"]["display"] == "summarized" and fake.calls[1]["fallbacks"] == "default"
    assert not any(e["type"] == "error" for e in h.events)
    ask(jarvis, "hello", h)  # remembered for the rest of the session
    assert fake.calls[2]["thinking"]["display"] == "summarized"
