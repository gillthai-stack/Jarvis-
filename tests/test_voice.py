"""Local voice: whisper.cpp transcription and `say` speech, with stand-in programs so it runs anywhere.

To also run against the real whisper.cpp, set JARVIS_TEST_WHISPER_BIN and JARVIS_TEST_WHISPER_MODEL
(the install script prints the paths); the real tests are skipped otherwise.
"""

import io
import math
import os
import sys
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakes import FakeAnthropic, text

from jarvis import server, voice
from jarvis.agent import Jarvis
from jarvis.voice import api, stt, tts

ORIGIN = {"origin": "http://127.0.0.1:8765"}

FAKE_WHISPER = """#!{python}
import os, sys, time
args = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(" ".join(args) + "\\n")
if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
if os.environ.get("FAKE_FAIL"):
    sys.exit(3)
out = args[args.index("-of") + 1]
open(out + ".txt", "w").write(os.environ.get("FAKE_TRANSCRIPT", " Hello Jarvis. [BLANK_AUDIO]"))
"""

FAKE_SAY = """#!{python}
import os, sys, wave
args = sys.argv[1:]
if args[:2] == ["-v", "?"]:
    print("Daniel              en_GB    # Hello! My name is Daniel.")
    print("Eddy (English (US)) en_US    # Hello! My name is Eddy.")
    sys.exit(0)
open(os.environ["FAKE_LOG"], "a").write(" ".join(args) + "\\n")
out = args[args.index("-o") + 1]
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b"\\0\\0" * 2205)
"""


def script(path: Path, body: str) -> Path:
    path.write_text(body.format(python=sys.executable))
    path.chmod(0o755)
    return path


def make_wav(seconds=1.0, rate=16000, channels=1, width=2, amplitude=8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(seconds * rate)
        frames = bytearray()
        for i in range(n):
            v = int(amplitude * math.sin(2 * math.pi * 220 * i / rate))
            frames += v.to_bytes(width, "little", signed=True) * channels
        w.writeframes(bytes(frames))
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    log.write_text("")
    monkeypatch.setenv("FAKE_LOG", str(log))
    tmp = tmp_path / "voice-tmp"
    tmp.mkdir()
    monkeypatch.setattr(voice, "TMP_PARENT", tmp)
    model = tmp_path / "ggml-base.en.bin"
    model.write_bytes(b"\0" * 10_000_001)
    whisper = stt.WhisperCpp(script(tmp_path / "whisper-cli", FAKE_WHISPER), model, timeout=5)
    say = tts.SayEngine(script(tmp_path / "say", FAKE_SAY))
    monkeypatch.setattr(api, "transcriber", whisper)
    monkeypatch.setattr(api, "speaker", say)
    monkeypatch.setattr(server, "jarvis", Jarvis(client=FakeAnthropic([])))

    class Env:
        pass

    e = Env()
    e.client, e.log, e.tmp, e.model, e.whisper, e.say = TestClient(server.app), log, tmp, model, whisper, say
    return e


def post_wav(c, data, headers=None):
    return c.post("/api/voice/transcribe", content=data, headers={"content-type": "audio/wav", **ORIGIN, **(headers or {})})


# ---------- transcription ----------


def test_transcribes_and_cleans_up(env):
    r = post_wav(env.client, make_wav())
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "Hello Jarvis."}
    call = env.log.read_text().split()
    assert call[call.index("-l") + 1] == "en" and "-nt" in call and "-np" in call
    assert list(env.tmp.iterdir()) == []  # the recording and transcript are gone


def test_health(env):
    assert env.client.get("/api/health").json() == {"ok": True, "voice": {"stt": True, "tts": True}}
    env.model.unlink()
    assert env.client.get("/api/health").json()["voice"]["stt"] is False


def test_status_reports_ready(env):
    s = env.client.get("/api/voice/status").json()
    assert s["stt"]["available"] is True
    assert s["tts"]["available"] is True
    assert s["limits"]["max_seconds"] == stt.MAX_SECONDS


def test_missing_model(env):
    env.model.unlink()
    r = post_wav(env.client, make_wav())
    assert r.status_code == 503
    assert "install_whisper.sh" in r.json()["detail"]
    assert env.client.get("/api/voice/status").json()["stt"]["available"] is False


def test_missing_binary(env, tmp_path):
    env.whisper.binary = tmp_path / "nope" / "whisper-cli"
    assert post_wav(env.client, make_wav()).status_code == 503


def test_silent_recording_is_no_speech(env):
    r = post_wav(env.client, make_wav(amplitude=0))
    assert r.status_code == 422
    assert env.log.read_text() == ""  # whisper never ran


def test_too_short_recording(env):
    assert post_wav(env.client, make_wav(seconds=0.1)).status_code == 422


def test_empty_transcript_is_no_speech(env, monkeypatch):
    monkeypatch.setenv("FAKE_TRANSCRIPT", " [BLANK_AUDIO]\n (wind blowing) ")
    assert post_wav(env.client, make_wav()).status_code == 422


def test_empty_upload(env):
    assert post_wav(env.client, b"").status_code == 400


def test_oversized_upload_rejected(env):
    r = post_wav(env.client, b"RIFF" + b"\0" * (stt.MAX_BYTES + 10))
    assert r.status_code == 413
    assert env.log.read_text() == ""


def test_too_long_recording_rejected():
    # A valid header claiming more audio than allowed is refused before whisper runs.
    with pytest.raises(voice.TooLarge):
        stt.check_wav(make_wav(seconds=0.5)[:40] + (stt.MAX_BYTES).to_bytes(4, "little") + b"\0" * (stt.MAX_BYTES + 1))


def test_wrong_format_rejected(env):
    assert post_wav(env.client, make_wav(rate=44100)).status_code == 400
    assert post_wav(env.client, make_wav(channels=2)).status_code == 400
    assert post_wav(env.client, b"ID3 this is an mp3" * 10).status_code == 400
    r = env.client.post("/api/voice/transcribe", content=make_wav(), headers={"content-type": "text/plain", **ORIGIN})
    assert r.status_code == 415


def test_other_websites_cannot_use_voice(env):
    evil = {"origin": "https://evil.example"}
    assert post_wav(env.client, make_wav(), evil).status_code == 403
    assert env.client.post("/api/voice/speak", json={"text": "hi"}, headers=evil).status_code == 403
    assert env.log.read_text() == ""


def test_whisper_timeout_kills_and_cleans_up(env, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "10")
    env.whisper.timeout = 0.5
    r = post_wav(env.client, make_wav())
    assert r.status_code == 504
    assert list(env.tmp.iterdir()) == []


def test_whisper_failure(env, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "1")
    assert post_wav(env.client, make_wav()).status_code == 500
    assert list(env.tmp.iterdir()) == []


def test_language_cannot_inject_options(tmp_path):
    w = stt.WhisperCpp(tmp_path / "x", tmp_path / "m", language="--dump-everything")
    assert w.language == "en"


# ---------- speech ----------


def test_speak_returns_wav_and_keeps_text_off_the_command_line(env):
    r = env.client.post("/api/voice/speak", json={"text": "Good **evening**, Gill. [[slnc 9000]]"}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"
    call = env.log.read_text()
    assert "evening" not in call and "-f" in call.split()
    assert list(env.tmp.iterdir()) == []


def test_speak_voice_must_be_installed(env):
    ok = env.client.post("/api/voice/speak", json={"text": "hi", "voice": "Eddy (English (US))"}, headers=ORIGIN)
    assert ok.status_code == 200
    assert "-v Eddy (English (US))" in env.log.read_text()
    for bad in ["-o/tmp/evil.aiff", "Nobody"]:
        r = env.client.post("/api/voice/speak", json={"text": "hi", "voice": bad}, headers=ORIGIN)
        assert r.status_code == 400


def test_speak_limits(env):
    assert env.client.post("/api/voice/speak", json={"text": ""}, headers=ORIGIN).status_code == 422
    assert env.client.post("/api/voice/speak", json={"text": "x" * 20001}, headers=ORIGIN).status_code == 422
    assert env.client.post("/api/voice/speak", json={"text": "hi", "rate": 9999}, headers=ORIGIN).status_code == 422
    assert env.client.post("/api/voice/speak", json={"text": "**"}, headers=ORIGIN).status_code == 400


def test_prepare_text_caps_length():
    long = "This is a sentence. " * 400
    out = tts.prepare_text(long)
    assert len(out) <= tts.MAX_CHARS and out.endswith(".")
    assert tts.prepare_text("See [the docs](https://x.y) or https://a.b/c") == "See the docs or a link"


def test_voices_list(env):
    names = [v["name"] for v in env.client.get("/api/voice/voices").json()["voices"]]
    assert names == ["Daniel", "Eddy (English (US))"]


def test_say_missing(env, tmp_path):
    env.say.binary = tmp_path / "no-say"
    assert env.client.post("/api/voice/speak", json={"text": "hi"}, headers=ORIGIN).status_code == 503
    assert env.client.get("/api/voice/status").json()["tts"]["available"] is False


# ---------- conversation path ----------


def test_transcript_goes_through_the_normal_chat(env):
    """The HUD sends a transcript exactly like typed text: one `ask` on /ws."""
    server.jarvis.client = FakeAnthropic([lambda p: [text("It is noon.")]])
    transcript = post_wav(env.client, make_wav()).json()["text"]
    with env.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"type": "ask", "text": transcript})
        seen = []
        while not seen or seen[-1]["type"] != "done":
            seen.append(ws.receive_json())
    assert {"type": "text", "text": "It is noon."} in seen
    assert server.jarvis.messages[0]["content"][-1]["text"] == "Hello Jarvis."


# ---------- the real whisper.cpp (optional) ----------

REAL_BIN = os.getenv("JARVIS_TEST_WHISPER_BIN")
REAL_MODEL = os.getenv("JARVIS_TEST_WHISPER_MODEL")


@pytest.mark.skipif(not REAL_BIN, reason="set JARVIS_TEST_WHISPER_BIN to test the real whisper.cpp")
def test_real_whisper_rejects_a_broken_model(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(voice, "TMP_PARENT", tmp_path)
    junk = tmp_path / "junk-model.bin"
    junk.write_bytes(b"\0" * 10_000_001)
    w = stt.WhisperCpp(Path(REAL_BIN), junk)
    with pytest.raises(voice.VoiceError):
        asyncio.run(w.transcribe(make_wav()))
    assert [p for p in tmp_path.iterdir() if p.name.startswith("jarvis-voice-")] == []


@pytest.mark.skipif(not (REAL_BIN and REAL_MODEL), reason="set JARVIS_TEST_WHISPER_BIN and _MODEL")
def test_real_whisper_transcribes_sample():
    import asyncio

    sample = Path(REAL_BIN).resolve().parents[2] / "samples" / "jfk.wav"
    if not sample.is_file():
        pytest.skip("whisper.cpp samples/jfk.wav not found")
    words = asyncio.run(stt.WhisperCpp(Path(REAL_BIN), Path(REAL_MODEL)).transcribe(sample.read_bytes())).lower()
    assert "ask not what your country can do for you" in words
