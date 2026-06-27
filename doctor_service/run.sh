#!/usr/bin/env bash
# Deploy doctor-service to Google Cloud Run
set -eo pipefail

PROJECT_ID="medhx-care-ai"
REGION="asia-south1"
REPO_NAME="ai-health-repo"
IMAGE_NAME="doctor-service"
SERVICE_NAME="doctor-service"
SERVICE_ACCOUNT_NAME="ai-health-app-sa"

IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"
SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Deploying $SERVICE_NAME to Cloud Run..."

gcloud run deploy "$SERVICE_NAME" \
    --image="$IMAGE_TAG" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --port=8002 \
    --allow-unauthenticated \
    --min-instances=0 \
    --max-instances=10 \
    --concurrency=80 \
    --timeout=300 \
    --cpu=1 \
    --memory=1Gi \
    --update-secrets="ELEVENLABS_API_KEY=ELEVENLABS_API_KEY:latest" \
    --update-env-vars="ENVIRONMENT=production,\
GCP_PROJECT_ID=${PROJECT_ID},\
GCP_REGION=${REGION},\
FIREBASE_PROJECT_ID=${PROJECT_ID},\
STORAGE_BUCKET_NAME=medhx-care-media,\
GCS_SIGNING_SERVICE_ACCOUNT=export-sa@${PROJECT_ID}.iam.gserviceaccount.com,\
GEMINI_MODEL=gemini-2.5-flash,\
GEMINI_EMBEDDING_MODEL=gemini-embedding-001,\
GEMINI_LOCATION=us-central1,\
ELEVENLABS_VOICE_ID=zEvjs17jNQ2fH5FxAat2,\
ELEVENLABS_TTS_MODEL_ID=eleven_turbo_v2_5,\
ELEVENLABS_STT_MODEL_ID=scribe_v2"

echo "Deployment complete for $SERVICE_NAME!"
