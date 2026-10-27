"""HTTP routes for local voice, mounted under /api/voice by the server."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import config
from . import VoiceError, stt, tts

router = APIRouter(prefix="/api/voice")

transcriber = stt.WhisperCpp(config.WHISPER_BIN, config.WHISPER_MODEL, config.WHISPER_LANGUAGE, config.WHISPER_THREADS)
speaker: tts.SpeechEngine = (
    tts.PiperEngine(config.PIPER_BIN, config.PIPER_MODEL)
    if config.TTS_ENGINE == "piper"
    else tts.SayEngine(config.SAY_BIN, config.SAY_VOICE, config.SAY_RATE)
)


def check_origin(request: Request) -> None:
    # Browsers send Origin on every POST. Without this, any website you visit could make Jarvis
    # transcribe or speak. Local tools that send no Origin (scripts, tests) are allowed.
    origin = request.headers.get("origin")
    if origin is not None and origin not in config.ALLOWED_ORIGINS:
        raise HTTPException(403, "This page isn't allowed to use Jarvis's voice.")


def fail(e: VoiceError):
    raise HTTPException(e.status, str(e))


@router.get("/status")
async def status():
    return {
        "stt": transcriber.status(),
        "tts": speaker.status(),
        "limits": {"max_seconds": stt.MAX_SECONDS, "max_bytes": stt.MAX_BYTES, "max_chars": tts.MAX_CHARS},
    }


@router.get("/voices")
async def voices():
    try:
        return {"engine": speaker.name, "voices": await speaker.voices()}
    except VoiceError as e:
        fail(e)


@router.post("/transcribe", dependencies=[Depends(check_origin)])
async def transcribe(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip() not in ("audio/wav", "audio/x-wav", "audio/wave"):
        raise HTTPException(415, "Send the recording as audio/wav.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > stt.MAX_BYTES:
        raise HTTPException(413, f"The recording is too long. The limit is {stt.MAX_SECONDS} seconds.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > stt.MAX_BYTES:
            raise HTTPException(413, f"The recording is too long. The limit is {stt.MAX_SECONDS} seconds.")
    try:
        text = await transcriber.transcribe(bytes(body))
    except VoiceError as e:
        fail(e)
    finally:
        body.clear()
    return {"text": text}


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    voice: str | None = Field(default=None, max_length=64)
    rate: int | None = Field(default=None, ge=80, le=400)


@router.post("/speak", dependencies=[Depends(check_origin)])
async def speak(body: SpeakRequest):
    try:
        audio = await speaker.synthesize(body.text, body.voice, body.rate)
    except VoiceError as e:
        fail(e)
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
