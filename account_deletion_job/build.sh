#!/usr/bin/env bash
# Build and push account-deletion-job image to Google Artifact Registry
set -eo pipefail

PROJECT_ID="medhx-care-ai"
REGION="asia-south1"
REPO_NAME="ai-health-repo"
IMAGE_NAME="account-deletion-job"

echo "Building and pushing $IMAGE_NAME to Artifact Registry..."

# 1. Configure Docker authentication for Artifact Registry
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# 2. Build docker image from project root context
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"
echo "Building Docker image: $IMAGE_TAG..."
docker build --platform linux/amd64 -t "$IMAGE_TAG" -f account_deletion_job/Dockerfile .

# 3. Push image
echo "Pushing image to Artifact Registry..."
docker push "$IMAGE_TAG"

echo "Artifact successfully pushed: $IMAGE_TAG"
