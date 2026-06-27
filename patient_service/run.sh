#!/usr/bin/env bash
# Deploy patient-service to Google Cloud Run
set -eo pipefail

PROJECT_ID="medhx-care-ai"
REGION="asia-south1"
REPO_NAME="ai-health-repo"
IMAGE_NAME="patient-service"
SERVICE_NAME="patient-service"
SERVICE_ACCOUNT_NAME="ai-health-app-sa"

IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"
SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Deploying $SERVICE_NAME to Cloud Run..."

gcloud run deploy "$SERVICE_NAME" \
    --image="$IMAGE_TAG" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --port=8001 \
    --allow-unauthenticated \
    --min-instances=0 \
    --max-instances=3 \
    --concurrency=80 \
    --timeout=300 \
    --cpu=1 \
    --memory=1Gi \
    --update-env-vars=ENVIRONMENT=production,GCP_PROJECT_ID=${PROJECT_ID},STORAGE_BUCKET_NAME=medhx-care-media,GCS_SIGNING_SERVICE_ACCOUNT=export-sa@${PROJECT_ID}.iam.gserviceaccount.com

echo "Deployment complete for $SERVICE_NAME!"
