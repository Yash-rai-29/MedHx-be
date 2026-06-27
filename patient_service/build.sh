#!/usr/bin/env bash
# Build and push patient-service image to Google Artifact Registry
set -eo pipefail

PROJECT_ID="medhx-care-ai"
REGION="asia-south1"
REPO_NAME="ai-health-repo"
IMAGE_NAME="patient-service"

echo "Building and pushing $IMAGE_NAME to Artifact Registry..."

# 1. Create Artifact Registry repository if it doesn't exist
if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" &>/dev/null; then
    echo "Creating Artifact Registry repository '$REPO_NAME'..."
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Docker repository for AI Health Companion"
fi

# 2. Configure Docker authentication for Artifact Registry
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# 3. Build docker image from project root context
IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"
echo "Building Docker image: $IMAGE_TAG..."
docker build --platform linux/amd64 -t "$IMAGE_TAG" -f patient_service/Dockerfile .

# 4. Push image
echo "Pushing image to Artifact Registry..."
docker push "$IMAGE_TAG"

echo "Artifact successfully pushed: $IMAGE_TAG"
