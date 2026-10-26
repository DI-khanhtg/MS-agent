"""Authenticated audio upload. Transcripts are input, not a second agent workflow."""

import re
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from enterprise_agent.api.auth import PrincipalDependency
from enterprise_agent.voice.transcriber import SpeechConfigurationError, SpeechRecognitionError

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/transcribe")
async def transcribe(
    request: Request,
    principal: PrincipalDependency,
    audio: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "vi-VN",
) -> dict:
    if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", language):
        raise HTTPException(422, "Invalid language code")
    try:
        data = await audio.read(2_000_001)
        result = await request.app.state.transcriber.transcribe(data, language)
        return asdict(result)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SpeechConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except SpeechRecognitionError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await audio.close()
