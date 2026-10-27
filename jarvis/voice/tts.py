"""Text to speech. macOS `say` today; Piper can be switched in with JARVIS_TTS=piper.

Every engine turns text into WAV bytes, which the HUD plays (and can stop) in the browser.
"""

import re
import time
from pathlib import Path
from typing import Protocol

from . import BadAudio, NotInstalled, VoiceError, check_executable, last_line, private_tempdir, run

MAX_CHARS = 4000

_VOICE_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<lang>[a-z]{2,3}_[A-Za-z0-9]+)\s+#")
_VOICE_NAME = re.compile(r"[\w .()'-]{1,64}")


def prepare_text(text: str) -> str:
    """Plain words for speaking: no Markdown symbols, links or `say` control sequences, and at most MAX_CHARS."""
    text = text.replace("[[", " ").replace("]]", " ")  # `say` treats [[...]] as commands
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [label](url) -> label
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_`#>|~]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = cut[: end + 1] if end > MAX_CHARS // 2 else cut
    return text


class SpeechEngine(Protocol):
    name: str

    def status(self) -> dict: ...

    async def voices(self) -> list[dict]: ...

    async def synthesize(self, text: str, voice: str | None = None, rate: int | None = None) -> bytes: ...


class SayEngine:
    """macOS's built-in `say`, writing 16-bit WAV."""

    name = "say"
    _voices_cache: tuple[float, list[dict]] | None = None

    def __init__(self, binary: Path, voice: str = "", rate: int = 0, timeout: float = 30):
        self.binary = binary
        self.voice = voice
        self.rate = rate
        self.timeout = timeout

    def check(self) -> None:
        check_executable(self.binary, "macOS speech (say)", "Spoken replies through `say` need macOS.")

    def status(self) -> dict:
        try:
            self.check()
            return {"engine": self.name, "available": True, "voice": self.voice or "System voice"}
        except VoiceError as e:
            return {"engine": self.name, "available": False, "problem": str(e)}

    async def voices(self) -> list[dict]:
        self.check()
        if self._voices_cache and time.monotonic() - self._voices_cache[0] < 600:
            return self._voices_cache[1]
        code, out, err = await run([str(self.binary), "-v", "?"], 10)
        if code != 0:
            raise VoiceError(f"Couldn't list voices. {last_line(err)}".strip())
        found = []
        for line in out.decode("utf-8", "replace").splitlines():
            m = _VOICE_LINE.match(line)
            if m:
                found.append({"name": m["name"].strip(), "lang": m["lang"]})
        self._voices_cache = (time.monotonic(), found)
        return found

    async def _checked_voice(self, voice: str | None) -> str | None:
        voice = (voice or self.voice or "").strip()
        if not voice:
            return None
        # Only names `say` itself lists, so a voice can never be read as another option.
        if not _VOICE_NAME.fullmatch(voice) or voice.startswith("-"):
            raise BadAudio("That voice name isn't valid.")
        if voice not in {v["name"] for v in await self.voices()}:
            raise BadAudio(f"The voice {voice!r} isn't installed on this Mac.")
        return voice

    async def synthesize(self, text: str, voice: str | None = None, rate: int | None = None) -> bytes:
        self.check()
        text = prepare_text(text)
        if not text:
            raise BadAudio("There's nothing to say.")
        voice = await self._checked_voice(voice)
        rate = rate or self.rate
        with private_tempdir() as tmp:
            source = tmp / "reply.txt"
            source.write_text(text, "utf-8")
            out = tmp / "reply.wav"
            # The text goes in a file (-f), never on the command line.
            argv = [str(self.binary), "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@22050", "-f", str(source)]
            if voice:
                argv += ["-v", voice]
            if rate:
                argv += ["-r", str(max(80, min(400, int(rate))))]
            code, _out, err = await run(argv, self.timeout)
            if code != 0 or not out.is_file():
                raise VoiceError(f"`say` failed (exit {code}). {last_line(err)}".strip())
            return out.read_bytes()


class PiperEngine:
    """Piper (https://github.com/rhasspy/piper), a free neural voice that also runs offline. Optional."""

    name = "piper"

    def __init__(self, binary: Path, model: Path, timeout: float = 60):
        self.binary = binary
        self.model = model
        self.timeout = timeout

    def check(self) -> None:
        hint = "Install Piper and set JARVIS_PIPER_BIN and JARVIS_PIPER_MODEL in .env."
        check_executable(self.binary, "Piper", hint)
        if not self.model.is_file():
            raise NotInstalled(f"The Piper voice was not found at {self.model}. {hint}")

    def status(self) -> dict:
        try:
            self.check()
            return {"engine": self.name, "available": True, "voice": self.model.stem}
        except VoiceError as e:
            return {"engine": self.name, "available": False, "problem": str(e)}

    async def voices(self) -> list[dict]:
        self.check()
        return [{"name": self.model.stem, "lang": ""}]

    async def synthesize(self, text: str, voice: str | None = None, rate: int | None = None) -> bytes:
        self.check()
        text = prepare_text(text)
        if not text:
            raise BadAudio("There's nothing to say.")
        with private_tempdir() as tmp:
            out = tmp / "reply.wav"
            argv = [str(self.binary), "--model", str(self.model), "--output_file", str(out)]
            code, _out, err = await run(argv, self.timeout, stdin=text.encode("utf-8"))
            if code != 0 or not out.is_file():
                raise VoiceError(f"Piper failed (exit {code}). {last_line(err)}".strip())
            return out.read_bytes()
