from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Central configuration for all AI Health Companion services."""

    # ── GCP Core ──────────────────────────────────────────────
    GCP_PROJECT_ID: str = "medhx-care-ai"
    GCP_REGION: str = "asia-south1"

    # ── Firebase ──────────────────────────────────────────────
    FIREBASE_PROJECT_ID: str = "medhx-care-ai"

    # ── Cloud Storage ─────────────────────────────────────────
    STORAGE_BUCKET_NAME: str = "medhx-care-media"
    GCS_SIGNING_SERVICE_ACCOUNT: Optional[str] = None

    # ── Vertex AI / Gemini ────────────────────────────────────
    # gemini-2.5-flash is the recommended fast model available in us-central1.
    # NOTE: Flash models are NOT available in asia-south1 via Vertex AI.
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_OUTPUT_DIM: int = 1536   # balanced: 2× quality headroom over 768, half the cost of 3072
    # Gemini/Vertex region — must be us-central1 for flash models
    GEMINI_LOCATION: str = "us-central1"


    # ── Document AI ───────────────────────────────────────────
    DOCUMENT_AI_PROCESSOR_NAME: Optional[str] = None

    # ── Pub/Sub Topics ────────────────────────────────────────
    PUBSUB_TOPIC_AUDIO_UPLOADED: str = "consultation-audio-uploaded"
    PUBSUB_TOPIC_TRANSCRIBED: str = "consultation-transcribed"
    PUBSUB_TOPIC_PUBLISHED: str = "consultation-published"
    PUBSUB_TOPIC_DOCUMENT_UPLOADED: str = "document-uploaded"

    # ── Firestore Collections ─────────────────────────────────
    USERS_COLLECTION: str = "users"
    PATIENTS_COLLECTION: str = "patients"
    DOCTORS_COLLECTION: str = "doctors"
    CONSULTATIONS_COLLECTION: str = "consultations"
    AUDIO_CONSULTATIONS_COLLECTION: str = "audio_consultations"
    MEDICINES_COLLECTION: str = "medicines"
    REMINDERS_COLLECTION: str = "reminders"
    DOCUMENTS_COLLECTION: str = "documents"
    CONSENTS_COLLECTION: str = "consents"
    AUDIT_LOGS_COLLECTION: str = "audit_logs"
    DRUG_KNOWLEDGE_COLLECTION: str = "drug_knowledge"
    VITALS_COLLECTION: str = "vitals"
    RATINGS_COLLECTION: str = "ratings"
    REPORTS_COLLECTION: str = "reports"
    ANALYTICS_COLLECTION: str = "analytics_events"
    CHAT_SESSIONS_COLLECTION: str = "chatbot_sessions"
    LEGAL_COLLECTION: str = "legal_documents"
    NOTIFICATIONS_COLLECTION: str = "notifications"
    DOCUMENT_CHUNKS_COLLECTION: str = "document_chunks"


    # ── Runtime ───────────────────────────────────────────────
    ENVIRONMENT: str = "development"
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    CLOUD_TASKS_QUEUE_NAME: str = "notification-queue"
    CLOUD_TASKS_SECRET: Optional[str] = "local-tasks-secret"
    SERVICE_URL: Optional[str] = None

    # ── CORS ──────────────────────────────────────────────────
    # Space-separated list of allowed origins. Override via env var in Cloud Run.
    # Example: "https://medhx.web.app https://medhx.firebaseapp.com"
    ALLOWED_ORIGINS: str = "http://localhost:3000 http://localhost:8080"

    # ── ElevenLabs ────────────────────────────────────────────
    ELEVENLABS_API_KEY: Optional[str] = None
    # Fallback voice ID used when a language is not in ELEVENLABS_VOICE_ID_MAP (gcp_clients.py)
    ELEVENLABS_VOICE_ID: str = "zEvjs17jNQ2fH5FxAat2"
    ELEVENLABS_TTS_MODEL_ID: str = "eleven_turbo_v2_5"
    ELEVENLABS_STT_MODEL_ID: str = "scribe_v2"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )



settings = Settings()
