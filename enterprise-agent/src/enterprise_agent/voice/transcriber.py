"""Validate bounded PCM WAV commands and transcribe using the optional Azure SDK."""

import asyncio
import io
import wave
from dataclasses import dataclass
from typing import Protocol

from enterprise_agent.config import Settings


class SpeechConfigurationError(RuntimeError):
    pass


class SpeechRecognitionError(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    language: str
    duration_seconds: float
    provider: str = "azure"


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, language: str) -> Transcript: ...


def validate_wav(audio: bytes, max_seconds: int = 60) -> tuple[bytes, float]:
    if len(audio) > 2_000_000:
        raise ValueError("Audio exceeds 2 MB")
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (
                1,
                2,
                16000,
                "NONE",
            ):
                raise ValueError("Upload PCM WAV, 16 kHz, mono, 16-bit")
            duration = wav.getnframes() / 16000
            if not 0 < duration <= max_seconds:
                raise ValueError(f"Audio must contain 1 sample to {max_seconds} seconds")
            pcm = wav.readframes(wav.getnframes())
            if len(pcm) != wav.getnframes() * 2:
                raise ValueError("Truncated WAV data")
            return pcm, duration
    except (wave.Error, EOFError) as exc:
        raise ValueError("Invalid WAV file") from exc


class AzureSpeechTranscriber:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(self, audio: bytes, language: str) -> Transcript:
        pcm, duration = validate_wav(audio, self.settings.speech_max_seconds)
        if not self.settings.speech_is_configured:
            raise SpeechConfigurationError(
                "Configure SPEECH_PROVIDER=azure, SPEECH_KEY and SPEECH_REGION"
            )
        try:
            async with asyncio.timeout(self.settings.speech_timeout_seconds):
                text = await asyncio.to_thread(self._recognize, pcm, language)
        except TimeoutError as exc:
            raise SpeechRecognitionError("Speech transcription timed out") from exc
        except (SpeechConfigurationError, SpeechRecognitionError):
            raise
        except Exception as exc:
            raise SpeechRecognitionError("Azure Speech could not transcribe the request") from exc
        return Transcript(text, language, duration)

    def _recognize(self, pcm: bytes, language: str) -> str:
        try:
            import azure.cognitiveservices.speech as speech
        except ImportError as exc:
            raise SpeechConfigurationError(
                "Install the voice extra: uv sync --extra voice"
            ) from exc
        config = speech.SpeechConfig(
            subscription=self.settings.speech_key.get_secret_value(),
            region=self.settings.speech_region,
        )
        config.speech_recognition_language = language
        stream = speech.audio.PushAudioInputStream(
            stream_format=speech.audio.AudioStreamFormat(
                samples_per_second=16000, bits_per_sample=16, channels=1
            )
        )
        audio = speech.audio.AudioConfig(stream=stream)
        recognizer = speech.SpeechRecognizer(speech_config=config, audio_config=audio)
        stream.write(pcm)
        stream.close()
        result = recognizer.recognize_once_async().get()
        if result.reason != speech.ResultReason.RecognizedSpeech or not result.text.strip():
            raise SpeechRecognitionError("No speech recognized or Azure rejected the request")
        return result.text.strip()
