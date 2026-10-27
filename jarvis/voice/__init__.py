"""Local voice: speech to text with whisper.cpp and text to speech with macOS `say` (or Piper).

Everything here runs on this Mac. Recordings and spoken text only exist in a private temporary
folder for the length of one request, and are never logged.
"""

import asyncio
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

# Parent folder for per-request temp folders. None means the system temp folder; tests point it elsewhere.
TMP_PARENT: Path | None = None


class VoiceError(Exception):
    """A voice request failed. `status` is the HTTP status the API answers with; the message is safe to show."""

    status = 500

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        if status is not None:
            self.status = status


class NotInstalled(VoiceError):
    status = 503


class BadAudio(VoiceError):
    status = 400


class NoSpeech(VoiceError):
    status = 422


class TooLarge(VoiceError):
    status = 413


class TimedOut(VoiceError):
    status = 504


@contextmanager
def private_tempdir():
    """A folder only this user can read (mode 0700), deleted with everything in it afterwards."""
    path = Path(tempfile.mkdtemp(prefix="jarvis-voice-", dir=TMP_PARENT))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def check_executable(path: Path, what: str, fix: str) -> None:
    if not path.is_absolute():
        raise NotInstalled(f"{what} must be an absolute path, not {path}.")
    if not path.is_file() or not os.access(path, os.X_OK):
        raise NotInstalled(f"{what} was not found at {path}. {fix}")


async def run(argv: list[str], timeout: float, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    """Runs a program from an argument list (never through a shell). Kills it if it runs past `timeout`."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as e:
        # Also on cancellation (the page went away), so no program is left running.
        proc.kill()
        await proc.wait()
        if isinstance(e, asyncio.CancelledError):
            raise
        raise TimedOut(f"{Path(argv[0]).name} took longer than {timeout:g} seconds and was stopped.")
    return proc.returncode, out, err


def last_line(err: bytes) -> str:
    """The last line of a program's error output, for an error message. Never contains audio or transcripts."""
    lines = [l for l in err.decode("utf-8", "replace").splitlines() if l.strip()]
    return lines[-1][:200] if lines else ""
