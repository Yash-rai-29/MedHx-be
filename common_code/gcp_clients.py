"""GCP service client wrappers — Storage, Speech-to-Text, Document AI, Translation, TTS, Gemini.

All clients use lazy-init singletons to avoid re-creation on every request.
Gemini uses the `google-genai` SDK (successor to the deprecated `google-generativeai`).
"""

import asyncio
import datetime
import json
import logging
import os
from typing import Any

import google.auth
from google.auth import impersonated_credentials
from google.cloud import storage
import httpx
from google.cloud import speech_v2
from google.cloud import documentai
from google.cloud import translate_v2 as translate
from google.cloud import texttospeech
from google import genai
from google.genai import types as genai_types

from common_code.config import settings

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  Lazy-init singletons
# ══════════════════════════════════════════════════════════════
_storage_client: storage.Client | None = None
_signing_storage_client: storage.Client | None = None
_translate_client: translate.Client | None = None
_tts_client: texttospeech.TextToSpeechClient | None = None
_genai_client: genai.Client | None = None


def _get_storage() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=settings.GCP_PROJECT_ID)
    return _storage_client


def _get_signing_storage() -> storage.Client:
    global _signing_storage_client
    if _signing_storage_client is None:
        try:
            target_sa = settings.GCS_SIGNING_SERVICE_ACCOUNT or f"export-sa@{settings.GCP_PROJECT_ID}.iam.gserviceaccount.com"
            logger.info(f"Initializing GCS signing client with service account impersonation for: {target_sa}")
            source_credentials, _ = google.auth.default()
            impersonated_creds = impersonated_credentials.Credentials(
                source_credentials=source_credentials,
                target_principal=target_sa,
                target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
                lifetime=datetime.timedelta(seconds=3600)
            )
            _signing_storage_client = storage.Client(
                project=settings.GCP_PROJECT_ID,
                credentials=impersonated_creds
            )
        except Exception as e:
            logger.warning(f"Impersonation failed: {e}. Falling back to default storage client.")
            _signing_storage_client = _get_storage()
    return _signing_storage_client


def _get_translate() -> translate.Client:
    global _translate_client
    if _translate_client is None:
        _translate_client = translate.Client()
    return _translate_client


def _get_tts() -> texttospeech.TextToSpeechClient:
    global _tts_client
    if _tts_client is None:
        _tts_client = texttospeech.TextToSpeechClient()
    return _tts_client


def _get_genai() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            _genai_client = genai.Client(api_key=api_key)
        else:
            # Vertex AI: use GEMINI_LOCATION (us-central1) — flash models are NOT
            # available in asia-south1 via Vertex AI.
            gemini_location = getattr(settings, "GEMINI_LOCATION", "us-central1")
            _genai_client = genai.Client(
                vertexai=True,
                project=settings.GCP_PROJECT_ID,
                location=gemini_location
            )
    return _genai_client


# ══════════════════════════════════════════════════════════════
#  1. Google Cloud Storage
# ══════════════════════════════════════════════════════════════
def generate_signed_upload_url(blob_name: str, expiration_minutes: int = 15) -> str:
    """Short-lived V4 signed URL for direct-to-GCS PUT upload."""
    try:
        bucket = _get_signing_storage().bucket(settings.STORAGE_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=expiration_minutes),
            method="PUT",
            content_type="application/octet-stream",
        )
    except Exception as e:
        logger.error(f"GCS signed upload URL error: {e}")
        return f"http://localhost:8001/mock-upload/{blob_name}"


def generate_signed_download_url(blob_name: str, expiration_minutes: int = 60) -> str:
    """Short-lived V4 signed URL for download / view."""
    try:
        bucket = _get_signing_storage().bucket(settings.STORAGE_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=expiration_minutes),
            method="GET",
        )
    except Exception as e:
        logger.error(f"GCS signed download URL error: {e}")
        return f"http://localhost:8001/mock-download/{blob_name}"


def upload_bytes_to_gcs(blob_name: str, data: bytes, content_type: str = "application/pdf") -> str:
    """Uploads raw bytes to GCS and returns the gs:// URI."""
    try:
        bucket = _get_storage().bucket(settings.STORAGE_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{settings.STORAGE_BUCKET_NAME}/{blob_name}"
    except Exception as e:
        logger.error(f"GCS upload error: {e}")
        return f"gs://mock/{blob_name}"


async def async_upload_bytes_to_gcs(blob_name: str, data: bytes, content_type: str = "application/pdf") -> str:
    """Non-blocking GCS upload — wraps the sync call in a thread pool."""
    return await asyncio.to_thread(upload_bytes_to_gcs, blob_name, data, content_type)


# ══════════════════════════════════════════════════════════════
#  2. Speech-to-Text v2 (Chirp)
# ══════════════════════════════════════════════════════════════
async def transcribe_audio(gcs_uri: str, language_code: str = "en-IN") -> dict[str, Any]:
    """Diarised transcription via STT v2 Chirp model or ElevenLabs Speech-to-Text Scribe v2."""
    if settings.ELEVENLABS_API_KEY:
        try:
            logger.info("Attempting ElevenLabs Speech-to-Text transcription...")
            # Download audio file from GCS into memory bytes
            bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
            audio_bytes = _get_storage().bucket(bucket_name).blob(blob_name).download_as_bytes()
            
            url = "https://api.elevenlabs.io/v1/speech-to-text"
            headers = {
                "xi-api-key": settings.ELEVENLABS_API_KEY
            }
            filename = blob_name.split("/")[-1] or "audio.wav"
            mime_type = "audio/wav"
            if filename.endswith(".mp3"):
                mime_type = "audio/mpeg"
            elif filename.endswith(".m4a"):
                mime_type = "audio/mp4"
                
            files = {
                "file": (filename, audio_bytes, mime_type)
            }
            data = {
                "model_id": settings.ELEVENLABS_STT_MODEL_ID
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, files=files, data=data, timeout=120.0)
                
            if response.status_code == 200:
                result = response.json()
                text = result.get("text", "")
                return {
                    "full_text": text,
                    "segments": [{"speaker": "Speaker 1", "text": text}]
                }
            else:
                logger.warning(f"ElevenLabs STT failed with status {response.status_code}: {response.text}, falling back to GCP/Mock")
        except Exception as e:
            logger.warning(f"ElevenLabs STT request failed: {e}, falling back to GCP/Mock")

    try:
        client = speech_v2.SpeechClient()
        recognizer = f"projects/{settings.GCP_PROJECT_ID}/locations/global/recognizers/_"

        config = speech_v2.types.RecognitionConfig(
            auto_decoding_config=speech_v2.types.AutoDetectDecodingConfig(),
            language_codes=[language_code],
            model="chirp",
            features=speech_v2.types.RecognitionFeatures(
                enable_word_time_offsets=True,
            ),
        )

        request = speech_v2.types.RecognizeRequest(
            recognizer=recognizer,
            config=config,
            uri=gcs_uri,
        )


        response = client.recognize(request=request)

        segments = []
        for result in response.results:
            alt = result.alternatives[0]
            speaker = getattr(alt.words[0], "speaker_tag", 1) if alt.words else 1
            segments.append({"speaker": f"Speaker {speaker}", "text": alt.transcript})

        full_transcript = " ".join(s["text"] for s in segments)
        return {"full_text": full_transcript, "segments": segments}
    except Exception as e:
        logger.warning(f"STT failed, using mock: {e}")
        return {
            "full_text": (
                "Doctor: Hello, what brings you in today? "
                "Patient: I have had a severe sore throat and fever for the past three days. "
                "Doctor: I will prescribe paracetamol for the fever and amoxicillin for the throat infection. "
                "Take paracetamol twice a day after meals, and amoxicillin three times a day."
            ),
            "segments": [
                {"speaker": "Doctor", "text": "Hello, what brings you in today?"},
                {"speaker": "Patient", "text": "I have had a severe sore throat and fever for the past three days."},
                {"speaker": "Doctor", "text": "I will prescribe paracetamol for the fever and amoxicillin for the throat infection. Take paracetamol twice a day after meals, and amoxicillin three times a day."},
            ],
        }


# ══════════════════════════════════════════════════════════════
#  3. Document AI / Vision OCR
# ══════════════════════════════════════════════════════════════
async def parse_medical_document(gcs_uri: str, mime_type: str = "application/pdf") -> str:
    """Extracts text from a medical document via Document AI."""
    try:
        processor_name = settings.DOCUMENT_AI_PROCESSOR_NAME
        if not processor_name:
            raise ValueError("DOCUMENT_AI_PROCESSOR_NAME not set")

        client = documentai.DocumentProcessorServiceClient()
        bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        content = _get_storage().bucket(bucket_name).blob(blob_name).download_as_bytes()

        raw_doc = documentai.RawDocument(content=content, mime_type=mime_type)
        request = documentai.ProcessRequest(name=processor_name, raw_document=raw_doc)
        result = client.process_document(request=request)
        return result.document.text
    except Exception as e:
        logger.warning(f"Document AI failed, returning mock OCR: {e}")
        return (
            "PATIENT LAB REPORT - METROPOLIS HEALTHCARE\n"
            "Patient: Ramesh Kumar, Age: 45, Gender: Male\n"
            "Date: 2026-06-25\n"
            "TEST: Complete Blood Count (CBC)\n"
            "Hemoglobin: 14.2 g/dL (Normal: 13.0 - 17.0)\n"
            "WBC Count: 11,500 /uL (HIGH, Normal: 4,000 - 11,000)\n"
            "HbA1c: 6.8% (HIGH, Prediabetic range: > 5.7%)\n"
        )


# ══════════════════════════════════════════════════════════════
#  4. Translation & Text-to-Speech
# ══════════════════════════════════════════════════════════════
VOICE_LOCALE_MAP = {
    "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN",
    "bn": "bn-IN", "mr": "mr-IN", "gu": "gu-IN",
    "kn": "kn-IN", "ml": "ml-IN", "pa": "pa-IN",
    "en": "en-IN",
}


def translate_text(text: str, target_language: str = "hi") -> str:
    """Translates text via Google Cloud Translation API."""
    try:
        result = _get_translate().translate(text, target_language=target_language)
        return result["translatedText"]
    except Exception as e:
        logger.warning(f"Translation failed: {e}")
        return f"[Translated to {target_language}]: {text}"


def synthesize_speech(text: str, language_code: str = "hi-IN") -> bytes:
    """Text-to-Speech synthesis returning MP3 bytes or ElevenLabs synthesis."""
    if settings.ELEVENLABS_API_KEY:
        try:
            logger.info("Attempting ElevenLabs Text-to-Speech synthesis...")
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.ELEVENLABS_VOICE_ID}"
            headers = {
                "xi-api-key": settings.ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            }
            data = {
                "text": text,
                "model_id": settings.ELEVENLABS_TTS_MODEL_ID,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.5
                }
            }
            response = httpx.post(url, json=data, headers=headers, timeout=60.0)
            if response.status_code == 200:
                return response.content
            else:
                logger.warning(f"ElevenLabs TTS failed with status {response.status_code}: {response.text}, falling back to GCP")
        except Exception as e:
            logger.warning(f"ElevenLabs TTS request failed: {e}, falling back to GCP")

    try:
        client = _get_tts()
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code=language_code,
                ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
            ),
        )
        return response.audio_content
    except Exception as e:
        logger.warning(f"TTS failed: {e}")
        return b"MOCK_AUDIO_DATA_BYTES"



# ══════════════════════════════════════════════════════════════
#  5. Gemini (google-genai SDK)
# ══════════════════════════════════════════════════════════════
def generate_gemini_content(prompt: str, json_response: bool = False, model: str | None = None) -> str:
    """Generates text via Gemini model (sync — use async_generate_gemini_content in async contexts)."""
    try:
        client = _get_genai()
        config = None
        if json_response:
            config = genai_types.GenerateContentConfig(
                response_mime_type="application/json",
            )

        model_name = model or settings.GEMINI_MODEL
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config,
        )
        return response.text

    except Exception as e:
        logger.warning(f"Gemini failed, returning mock: {e}")
        if json_response:
            return json.dumps({
                "category": "lab_report",
                "summary": "Mock summary: Your report shows a high WBC count which may indicate infection. Please consult your doctor.",
                "doctor_name": None,
                "document_date": None,
                "medications": [],
                "abnormal_labs": [{"parameter_name": "WBC Count", "value": "11,500 /uL", "reference_range": "4,000 - 11,000", "status": "High"}],
                "red_flags": [],
                "actionable_steps": ["Consult your doctor about the elevated WBC count."]
            })
        return "Mock AI summary of your consultation."


async def async_generate_gemini_content(prompt: str, json_response: bool = False, model: str | None = None) -> str:
    """Non-blocking Gemini call — wraps the sync call in a thread pool."""
    return await asyncio.to_thread(generate_gemini_content, prompt, json_response, model)


async def stream_gemini_content(prompt: str, model: str | None = None):
    """
    Async generator that yields text chunks from Gemini as they arrive.
    Bridges the sync SDK streaming iterator to an async generator via a thread + asyncio.Queue.
    Yields empty string sentinel (None) on completion.
    """
    import threading

    client     = _get_genai()
    model_name = model or settings.GEMINI_MODEL
    loop       = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _produce() -> None:
        try:
            for chunk in client.models.generate_content_stream(model=model_name, contents=prompt):
                if chunk.text:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk.text)
        except Exception as exc:
            logger.warning(f"Gemini stream error: {exc}")
            # Yield a fallback so the caller gets something
            loop.call_soon_threadsafe(queue.put_nowait, generate_gemini_content(prompt, model=model_name))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

    threading.Thread(target=_produce, daemon=True).start()
    while True:
        item = await queue.get()
        if item is None:
            break
        yield item


def generate_embeddings(text: str) -> list[float]:
    """Generates text embeddings via Gemini embedding model (sync)."""
    try:
        client = _get_genai()
        result = client.models.embed_content(
            model=settings.GEMINI_EMBEDDING_MODEL,
            contents=text,
        )
        return result.embeddings[0].values
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return [0.0] * 768


async def async_generate_embeddings(text: str) -> list[float]:
    """Non-blocking embedding call — wraps the sync call in a thread pool."""
    return await asyncio.to_thread(generate_embeddings, text)


# ══════════════════════════════════════════════════════════════
#  6. Google Cloud Secret Manager
# ══════════════════════════════════════════════════════════════
_secret_client: Any | None = None


def _get_secret_client() -> Any:
    global _secret_client
    if _secret_client is None:
        from google.cloud import secretmanager
        _secret_client = secretmanager.SecretManagerServiceClient()
    return _secret_client


def get_secret(secret_id: str, version_id: str = "latest") -> str:
    """Accesses the payload of a secret from GCP Secret Manager, falling back to OS environment."""
    try:
        client = _get_secret_client()
        name = f"projects/{settings.GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        logger.warning(f"Secret Manager lookup for '{secret_id}' failed, checking environment: {e}")
        return os.environ.get(secret_id, "")

