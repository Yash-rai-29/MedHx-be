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
        raise


def generate_signed_download_url(blob_name: str, expiration_minutes: int = 60) -> str | None:
    """Short-lived V4 signed URL for download / view. Returns None if signing fails."""
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
        return None


def upload_bytes_to_gcs(blob_name: str, data: bytes, content_type: str = "application/pdf") -> str:
    """Uploads raw bytes to GCS and returns the gs:// URI."""
    try:
        bucket = _get_storage().bucket(settings.STORAGE_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{settings.STORAGE_BUCKET_NAME}/{blob_name}"
    except Exception as e:
        logger.error(f"GCS upload error: {e}")
        raise


async def async_upload_bytes_to_gcs(blob_name: str, data: bytes, content_type: str = "application/pdf") -> str:
    """Non-blocking GCS upload — wraps the sync call in a thread pool."""
    return await asyncio.to_thread(upload_bytes_to_gcs, blob_name, data, content_type)


def download_bytes_from_gcs(blob_name: str) -> bytes | None:
    """Downloads a blob from GCS. Returns None if the blob does not exist."""
    try:
        blob = _get_storage().bucket(settings.STORAGE_BUCKET_NAME).blob(blob_name)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as e:
        logger.warning(f"GCS download error for {blob_name}: {e}")
        return None


async def async_download_bytes_from_gcs(blob_name: str) -> bytes | None:
    """Non-blocking GCS download — returns None on miss or error."""
    return await asyncio.to_thread(download_bytes_from_gcs, blob_name)


def delete_gcs_prefix(prefix: str) -> None:
    """Deletes all blobs under a GCS prefix. Used to invalidate TTS cache."""
    try:
        bucket = _get_storage().bucket(settings.STORAGE_BUCKET_NAME)
        blobs = list(bucket.list_blobs(prefix=prefix))
        for blob in blobs:
            blob.delete()
        if blobs:
            logger.info(f"Deleted {len(blobs)} GCS blob(s) under prefix: {prefix}")
    except Exception as e:
        logger.warning(f"GCS prefix delete error ({prefix}): {e}")


async def async_delete_gcs_prefix(prefix: str) -> None:
    """Non-blocking version of delete_gcs_prefix."""
    await asyncio.to_thread(delete_gcs_prefix, prefix)


# ══════════════════════════════════════════════════════════════
#  2. Speech-to-Text v2 (Chirp)
# ══════════════════════════════════════════════════════════════

def _parse_diarized_segments(words: list[dict]) -> list[dict]:
    """Convert ElevenLabs word-level diarization output into speaker segments.

    Groups consecutive words that share the same speaker_id into one segment.
    Spacing/punctuation tokens (type != 'word') are appended to the current
    segment without triggering a speaker change.

    Returns a list of dicts:
        {"speaker_id": "speaker_0", "text": "...", "start_time": 0.0, "end_time": 1.2}
    """
    if not words:
        return []

    segments: list[dict] = []
    current_speaker: str | None = None
    current_words:   list[str]  = []
    current_start:   float      = 0.0
    last_end:        float      = 0.0

    for w in words:
        w_type    = w.get("type", "word")
        w_text    = w.get("text", "")
        w_speaker = w.get("speaker_id")
        w_start   = w.get("start") or 0.0
        w_end     = w.get("end")   or w_start

        # Non-word tokens (spacing, punctuation) — append to current segment
        if w_type != "word":
            if current_words:
                current_words.append(w_text)
            last_end = w_end
            continue

        # Speaker changed → flush current segment
        if w_speaker != current_speaker and current_words:
            segments.append({
                "speaker_id": current_speaker,
                "text":       " ".join(current_words).strip(),
                "start_time": current_start,
                "end_time":   w_start,
            })
            current_words = []

        if not current_words:
            current_speaker = w_speaker
            current_start   = w_start

        current_words.append(w_text)
        last_end = w_end

    # Flush final segment
    if current_words:
        segments.append({
            "speaker_id": current_speaker,
            "text":       " ".join(current_words).strip(),
            "start_time": current_start,
            "end_time":   last_end,
        })

    return segments


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.wav",
) -> dict[str, Any]:
    """
    Transcribe raw audio bytes via ElevenLabs Scribe v2 with speaker diarization.
    Returns {"full_text": str, "segments": list} on success, or empty values on failure.
    Returns {"full_text": str, "segments": list}.
    """
    # ── Primary: ElevenLabs ──────────────────────────────────────
    if settings.ELEVENLABS_API_KEY:
        try:
            mime_type = "audio/wav"
            if filename.endswith(".mp3"):
                mime_type = "audio/mpeg"
            elif filename.endswith(".m4a"):
                mime_type = "audio/mp4"
            elif filename.endswith(".ogg"):
                mime_type = "audio/ogg"
            elif filename.endswith(".webm"):
                mime_type = "audio/webm"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.elevenlabs.io/v1/speech-to-text",
                    headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
                    files={"file": (filename, audio_bytes, mime_type)},
                    data={
                        "model_id":               settings.ELEVENLABS_STT_MODEL_ID,
                        "diarize":                "true",
                        "diarization_threshold":  "0.3",
                    },
                    timeout=120.0,
                )

            if response.status_code == 200:
                result   = response.json()
                full_text = result.get("text", "").strip()
                words     = result.get("words", [])
                segments  = _parse_diarized_segments(words)
                
                # Fallback to single segment if diarization yielded nothing but text exists
                if not segments and full_text:
                    segments = [{"speaker_id": "speaker_0", "text": full_text, "start_time": 0.0, "end_time": words[-1].get("end", 0.0) if words else 0.0}]

                # Retrieve or calculate audio duration
                audio_duration = result.get("audio_duration_secs")
                if not audio_duration and words:
                    audio_duration = words[-1].get("end")
                
                return {
                    "full_text": full_text,
                    "segments": segments,
                    "audio_duration_secs": audio_duration
                }
            else:
                logger.warning(f"ElevenLabs STT failed {response.status_code}: {response.text[:200]}")
        except Exception as e:
            logger.warning(f"ElevenLabs STT error: {e}")

    return {"full_text": "", "segments": [], "audio_duration_secs": 0.0}


async def transcribe_audio(gcs_uri: str) -> dict[str, Any]:
    """Transcribe audio from a GCS URI (used for consultation uploads, not voice chat)."""
    try:
        bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        audio_bytes = await asyncio.to_thread(
            _get_storage().bucket(bucket_name).blob(blob_name).download_as_bytes
        )
        filename = blob_name.split("/")[-1] or "audio.wav"
        return await transcribe_audio_bytes(audio_bytes, filename=filename)
    except Exception as e:
        logger.warning(f"GCS download for transcription failed: {e}")
        return {"full_text": "", "segments": []}


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
        logger.error(f"Document AI failed: {e}")
        raise


# ══════════════════════════════════════════════════════════════
#  4. Translation & Text-to-Speech
# ══════════════════════════════════════════════════════════════
VOICE_LOCALE_MAP = {
    "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN",
    "bn": "bn-IN", "mr": "mr-IN", "gu": "gu-IN",
    "kn": "kn-IN", "ml": "ml-IN", "pa": "pa-IN",
    "en": "en-IN",
}

# Maps ISO-639-1 language code → ElevenLabs voice ID.
# Used by synthesize_speech() to select the correct multilingual voice.
ELEVENLABS_VOICE_ID_MAP: dict[str, str] = {
    "en": "7wlfJf72PCt9FjPj0Beg",
    "hi": "zEvjs17jNQ2fH5FxAat2",
    "ta": "gJvkwI7wGFW2czmyfJhp",
    "te": "QKyvRuehpb8zB3cRkzIn",
    "bn": "iuABfyf7pRoBzuPqzUCt",
    "mr": "UN3inGyhBayPW0A8lscL",
    "gu": "v9ZPGDUUXnWEDCiJUQkk",
    "kn": "UeUC009F3NYPIArcZmq0",
    "ml": "OVkoEbwxsYHiSRMFV9t3",
    "pa": "ttyKbP9zTIRyRCN6b2Ye",
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
    """Text-to-Speech synthesis returning MP3 bytes.

    Selects the ElevenLabs voice based on the language prefix of `language_code`
    (e.g. 'hi-IN' → lang 'hi'). Falls back to GCP TTS when ElevenLabs is unavailable.
    """
    if settings.ELEVENLABS_API_KEY:
        try:
            lang = language_code.split("-")[0].lower()
            voice_id = ELEVENLABS_VOICE_ID_MAP.get(lang, settings.ELEVENLABS_VOICE_ID)
            logger.info(f"ElevenLabs TTS: lang={lang}, voice_id={voice_id}")
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
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


def generate_gemini_content_with_usage(
    prompt: str,
    json_response: bool = False,
    model: str | None = None,
) -> tuple[str, dict]:
    """Like generate_gemini_content but also returns token usage for eval logging.

    Returns (text, {"prompt_token_count": int, "candidates_token_count": int}).
    Gemini Flash pricing: $0.075/1M input tokens, $0.30/1M output tokens.
    """
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
    usage: dict = {}
    if response.usage_metadata:
        usage = {
            "prompt_token_count":     response.usage_metadata.prompt_token_count or 0,
            "candidates_token_count": response.usage_metadata.candidates_token_count or 0,
        }
    return response.text, usage


async def async_generate_gemini_content(prompt: str, json_response: bool = False, model: str | None = None) -> str:
    """Non-blocking Gemini call — wraps the sync call in a thread pool."""
    return await asyncio.to_thread(generate_gemini_content, prompt, json_response, model)


async def async_generate_gemini_content_with_usage(
    prompt: str,
    json_response: bool = False,
    model: str | None = None,
) -> tuple[str, dict]:
    """Non-blocking Gemini call that also returns token usage."""
    return await asyncio.to_thread(generate_gemini_content_with_usage, prompt, json_response, model)


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


def generate_embeddings(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Generates text embeddings via Gemini embedding model (sync).

    task_type should be "RETRIEVAL_DOCUMENT" when embedding content to store,
    and "RETRIEVAL_QUERY" when embedding a search query.
    """
    try:
        client = _get_genai()
        result = client.models.embed_content(
            model=settings.GEMINI_EMBEDDING_MODEL,
            contents=text,
            config=genai_types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=settings.EMBEDDING_OUTPUT_DIM,
            ),
        )
        return result.embeddings[0].values
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return [0.0] * settings.EMBEDDING_OUTPUT_DIM


async def async_generate_embeddings(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Non-blocking embedding call — wraps the sync call in a thread pool."""
    return await asyncio.to_thread(generate_embeddings, text, task_type)


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

