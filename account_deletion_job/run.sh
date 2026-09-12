#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="medhx-care-ai"
REGION="asia-south1"
REPO_NAME="ai-health-repo"
IMAGE_NAME="account-deletion-job"
JOB_NAME="account-deletion-job"
SERVICE_ACCOUNT_NAME="ai-health-app-sa"

IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"
SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Deploying Cloud Run Job: $JOB_NAME in region $REGION..."

gcloud run jobs deploy "$JOB_NAME" \
    --image="$IMAGE_TAG" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --max-retries=1 \
    --task-timeout=600s \
    --cpu=1 \
    --memory=1Gi \
    --update-secrets="PUBSUB_VERIFICATION_SECRET=PUBSUB_VERIFICATION_SECRET:latest,\
CLOUD_TASKS_SECRET=CLOUD_TASKS_SECRET:latest" \
    --update-env-vars="ENVIRONMENT=production,\
GCP_PROJECT_ID=${PROJECT_ID},\
GCP_REGION=${REGION},\
FIREBASE_PROJECT_ID=${PROJECT_ID},\
STORAGE_BUCKET_NAME=medhx-care-media"

echo "Cloud Run Job '$JOB_NAME' deployed successfully!"
