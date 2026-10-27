"""Speech to text with whisper.cpp (whisper-cli), run locally on a downloaded model."""

import asyncio
import io
import re
import wave
from array import array
from pathlib import Path

from . import BadAudio, NoSpeech, NotInstalled, TooLarge, VoiceError, check_executable, last_line, private_tempdir, run

# The HUD records 16 kHz, 16-bit mono WAV, which is what whisper.cpp wants. Anything else is refused.
SAMPLE_RATE = 16000
MAX_SECONDS = 120
MAX_BYTES = 44 + SAMPLE_RATE * 2 * MAX_SECONDS + 4096  # two minutes of audio plus header slack (about 3.8 MB)
MIN_SECONDS = 0.3
# Peak level (out of 32767) below which a recording counts as silence.
SILENCE_PEAK = 300

INSTALL_HINT = "Run ./scripts/install_whisper.sh in the jarvis folder, then restart Jarvis."

# whisper.cpp marks non-speech like [BLANK_AUDIO], [Music] or (wind blowing).
_NON_SPEECH = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")


def check_wav(data: bytes) -> float:
    """Checks a recording is a WAV the way the HUD makes it, with some sound in it. Returns its length in seconds."""
    if len(data) > MAX_BYTES:
        raise TooLarge(f"The recording is too long. The limit is {MAX_SECONDS} seconds.")
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise BadAudio("That isn't a WAV recording.")
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            pcm = w.readframes(frames)
    except (wave.Error, EOFError) as e:
        raise BadAudio(f"The recording couldn't be read ({e}).")
    if (channels, width, rate) != (1, 2, SAMPLE_RATE):
        raise BadAudio(f"Recordings must be 16 kHz, 16-bit mono (got {rate} Hz, {width * 8}-bit, {channels} channels).")
    seconds = frames / rate
    if seconds > MAX_SECONDS:
        raise TooLarge(f"The recording is too long. The limit is {MAX_SECONDS} seconds.")
    if seconds < MIN_SECONDS:
        raise NoSpeech("The recording was too short. Hold the mic a little longer.")
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples or max(max(samples), -min(samples)) < SILENCE_PEAK:
        raise NoSpeech("I didn't hear anything. Check the right microphone is selected and try again.")
    return seconds


def clean_transcript(raw: str) -> str:
    text = _NON_SPEECH.sub(" ", raw)
    return re.sub(r"\s+", " ", text).strip()


class WhisperCpp:
    name = "whisper.cpp"

    def __init__(self, binary: Path, model: Path, language: str = "en", threads: int = 4, timeout: float = 90):
        self.binary = binary
        self.model = model
        self.language = language if re.fullmatch(r"[a-z]{2,3}|auto", language) else "en"
        self.threads = threads
        self.timeout = timeout
        # One transcription at a time: whisper already uses every thread it is given.
        self._lock = asyncio.Lock()

    def check(self) -> None:
        check_executable(self.binary, "whisper.cpp (whisper-cli)", INSTALL_HINT)
        if not self.model.is_file():
            raise NotInstalled(f"The speech model was not found at {self.model}. {INSTALL_HINT}")
        if self.model.stat().st_size < 10_000_000:
            raise NotInstalled(f"The speech model at {self.model} looks incomplete. {INSTALL_HINT}")

    def status(self) -> dict:
        try:
            self.check()
            return {"engine": self.name, "available": True, "model": self.model.name}
        except VoiceError as e:
            return {"engine": self.name, "available": False, "model": self.model.name, "problem": str(e)}

    async def transcribe(self, wav_bytes: bytes) -> str:
        self.check()
        check_wav(wav_bytes)
        async with self._lock:
            with private_tempdir() as tmp:
                audio = tmp / "speech.wav"
                audio.write_bytes(wav_bytes)
                out_base = tmp / "transcript"
                argv = [
                    str(self.binary),
                    "-m", str(self.model),
                    "-f", str(audio),
                    "-l", self.language,
                    "-t", str(self.threads),
                    "-nt",  # no timestamps
                    "-np",  # print nothing but the result
                    "-otxt",
                    "-of", str(out_base),
                ]
                code, _out, err = await run(argv, self.timeout)
                if code != 0:
                    raise VoiceError(f"whisper.cpp failed (exit {code}). {last_line(err)}".strip())
                try:
                    raw = (tmp / "transcript.txt").read_text("utf-8", "replace")
                except FileNotFoundError:
                    raise VoiceError("whisper.cpp finished without writing a transcript.")
        text = clean_transcript(raw)
        if not text:
            raise NoSpeech("I couldn't make out any words. Try again a little closer to the microphone.")
        return text
