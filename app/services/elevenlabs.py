"""
ElevenLabs text-to-speech integration for natural, premium-quality AI voices.
Gracefully handles API availability and fallback to client-side Web Speech API.
"""
import logging
from typing import Optional
import httpx
from app.core.config import get_settings

logger = logging.getLogger("eliza.elevenlabs")
settings = get_settings()

VOICE_PROFILE_MAP = {
    "eliza": {
        "id": "eliza",
        "name": "ELIZA",
        "gender": "female",
        "accent": "Indian English",
        "voice_id": "21m00Tcm4TlvDq8ikWAM",  # Rachel / friendly conversational
        "description": "Warm, confident, friendly female AI assistant (Recommended Default)",
        "lang": "en-IN",
    },
    "arjun": {
        "id": "arjun",
        "name": "ARJUN",
        "gender": "male",
        "accent": "Indian English",
        "voice_id": "pNInz6obpgDQGcFmaJgB",  # Adam / articulate
        "description": "Calm, mature, articulate male assistant (Secondary Default)",
        "lang": "en-IN",
    },
    "nova": {
        "id": "nova",
        "name": "NOVA",
        "gender": "female",
        "accent": "General American",
        "voice_id": "EXAVITQu4vr4xnSDxMaL",  # Sarah / energetic
        "description": "Clear, energetic, natural American female assistant",
        "lang": "en-US",
    },
    "alex": {
        "id": "alex",
        "name": "ALEX",
        "gender": "male",
        "accent": "British English",
        "voice_id": "nPczCjzI2devNBz1zQrb",  # Brian / narrator
        "description": "Polished, composed, articulate British male assistant",
        "lang": "en-GB",
    },
    "kavya": {
        "id": "kavya",
        "name": "KAVYA",
        "gender": "female",
        "accent": "Hindi",
        "voice_id": "ThT5KcBeYPX3keUQqHPh",  # Dorothy / multilingual
        "description": "Expressive, friendly, natural Hindi female assistant",
        "lang": "hi-IN",
    },
    "vikram": {
        "id": "vikram",
        "name": "VIKRAM",
        "gender": "male",
        "accent": "Hindi",
        "voice_id": "VR6AewLTigWG4xSOukaG",  # Arnold / multilingual
        "description": "Reassuring, clear, confident Hindi male assistant",
        "lang": "hi-IN",
    },
    "marcus": {
        "id": "marcus",
        "name": "MARCUS",
        "gender": "male",
        "accent": "General American",
        "voice_id": "ErXwobaYiN019PkySvjV",  # Antoni
        "description": "Confident, relaxed American male assistant",
        "lang": "en-US",
    },
    "emma": {
        "id": "emma",
        "name": "EMMA",
        "gender": "female",
        "accent": "British English",
        "voice_id": "MF3mGyEYCl7XYWbV9V6O",  # Elli
        "description": "Polished, articulate British female assistant",
        "lang": "en-GB",
    },
}


def is_elevenlabs_configured() -> bool:
    return bool(settings.elevenlabs_api_key and settings.elevenlabs_api_key.strip())


async def synthesize_speech(
    text: str,
    voice_id: Optional[str] = None,
    voice_profile: Optional[str] = None
) -> Optional[bytes]:
    """
    Synthesize text to speech using ElevenLabs API.
    Returns MP3 audio bytes if successful, None if unconfigured or failed.
    """
    if not is_elevenlabs_configured():
        return None

    # Resolve target voice ID from profile or explicit voice_id
    target_voice_id = voice_id
    if not target_voice_id and voice_profile:
        prof = VOICE_PROFILE_MAP.get(voice_profile.lower())
        if prof:
            target_voice_id = prof["voice_id"]

    if not target_voice_id:
        target_voice_id = VOICE_PROFILE_MAP["eliza"]["voice_id"]

    # Sanitize and truncate text to prevent excessive token usage
    clean_text = text.strip()[:2500]
    if not clean_text:
        return None

    model_id = settings.elevenlabs_model_id or "eleven_multilingual_v2"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{target_voice_id}"

    headers = {
        "xi-api-key": settings.elevenlabs_api_key.strip(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": clean_text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return resp.content
            logger.warning("ElevenLabs TTS returned HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
    except Exception as exc:
        logger.error("ElevenLabs TTS request error: %s", exc)
        return None
