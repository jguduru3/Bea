"""Sarvam AI voice: text-to-speech (Bulbul) and speech-to-text (Saaras)."""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import httpx

from ..config import Settings
from .base import UpstreamError, request_with_retry

TTS_LANGUAGES = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}


@dataclass
class SpeechResult:
    audio: bytes
    media_type: str
    latency_ms: int


@dataclass
class Transcript:
    text: str
    language_code: str | None
    latency_ms: int


class SarvamClient:
    name = "sarvam"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.s, self.client = settings, client

    def _headers(self) -> dict[str, str]:
        key = self.s.sarvam_api_key.get_secret_value() if self.s.sarvam_api_key else ""
        if not key:
            raise UpstreamError(self.name, 503, "Voice is not configured. Set POM_SARVAM_API_KEY.")
        return {"api-subscription-key": key}

    async def speak(self, text: str, language_code: str | None = None, speaker: str | None = None, pace: float | None = None) -> SpeechResult:
        lang = language_code if language_code in TTS_LANGUAGES else self.s.default_language
        started = time.perf_counter()
        resp = await request_with_retry(
            self.client, self.name, "tts", self.s.upstream_retries, "POST",
            self.s.sarvam_base_url.rstrip("/") + "/text-to-speech",
            headers={**self._headers(), "content-type": "application/json"},
            json={
                "text": text,
                "language_code": lang,
                "model": self.s.sarvam_tts_model,
                "speaker": (speaker or self.s.sarvam_tts_speaker).lower(),
                "pace": pace or self.s.sarvam_tts_pace,
                "speech_sample_rate": self.s.sarvam_tts_sample_rate,
                "output_audio_codec": self.s.sarvam_tts_codec,
            },
        )
        data = resp.json()
        audios = data.get("audios") or []
        if not audios:
            raise UpstreamError(self.name, 502, "Sarvam returned no audio")
        audio = b"".join(base64.b64decode(a) for a in audios)
        media = "audio/mpeg" if self.s.sarvam_tts_codec == "mp3" else "audio/wav"
        return SpeechResult(audio, media, int((time.perf_counter() - started) * 1000))

    async def transcribe(self, audio: bytes, filename: str, content_type: str, language_code: str | None = None) -> Transcript:
        started = time.perf_counter()
        form = {"model": self.s.sarvam_stt_model, "language_code": language_code or "unknown"}
        resp = await request_with_retry(
            self.client, self.name, "stt", self.s.upstream_retries, "POST",
            self.s.sarvam_base_url.rstrip("/") + "/speech-to-text",
            headers=self._headers(),
            data=form,
            files={"file": (filename, audio, content_type)},
        )
        data = resp.json()
        return Transcript(
            text=(data.get("transcript") or "").strip(),
            language_code=data.get("language_code"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
