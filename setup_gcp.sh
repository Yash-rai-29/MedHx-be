#!/usr/bin/env bash
# setup_gcp.sh — GCP Infrastructure Bootstrap & Provisioning Script
set -eo pipefail

# Colors for log statements
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
PROJECT_ID="medhx-care-ai"
REGION="asia-south1"
BUCKET_NAME="medhx-care-media"
SERVICE_ACCOUNT_NAME="ai-health-app-sa"
EXPORT_SA_NAME="export-sa"
REPO_NAME="ai-health-repo"

echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}       GCP Bootstrap for AI Health Companion      ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo

# Confirm Project ID & Region
read -rp "Enter GCP Project ID [default: $PROJECT_ID]: " input_project
PROJECT_ID="${input_project:-$PROJECT_ID}"

read -rp "Enter GCP Region [default: $REGION]: " input_region
REGION="${input_region:-$REGION}"

read -rp "Enter GCS Bucket Name [default: $BUCKET_NAME]: " input_bucket
BUCKET_NAME="${input_bucket:-$BUCKET_NAME}"

echo
echo -e "${YELLOW}Provisioning infrastructure in project: $PROJECT_ID ($REGION)...${NC}"
echo

# 1. Set current project context
gcloud config set project "$PROJECT_ID"

# 2. Enable Required APIs
echo -e "${GREEN}[1/8] Enabling Google Cloud Service APIs...${NC}"
gcloud services enable \
    run.googleapis.com \
    firestore.googleapis.com \
    speech.googleapis.com \
    documentai.googleapis.com \
    translate.googleapis.com \
    texttospeech.googleapis.com \
    aiplatform.googleapis.com \
    pubsub.googleapis.com \
    storage.googleapis.com \
    iam.googleapis.com \
    secretmanager.googleapis.com \
    cloudtasks.googleapis.com \
    artifactregistry.googleapis.com

# 3. Create Artifact Registry Repository
echo -e "${GREEN}[2/8] Provisioning Artifact Registry Repository...${NC}"
if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" &>/dev/null; then
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Docker repository for AI Health Companion microservices & jobs"
    echo "Artifact Registry repository '$REPO_NAME' created."
else
    echo "Artifact Registry repository '$REPO_NAME' already exists."
fi

# 4. Provision Service Accounts
echo -e "${GREEN}[3/8] Provisioning Service Accounts...${NC}"
SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
EXPORT_SA_EMAIL="${EXPORT_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Main Application Service Account
if ! gcloud iam service-accounts describe "$SA_EMAIL" &>/dev/null; then
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --description="Service account for AI Health Companion Cloud Run microservices" \
        --display-name="AI Health Companion Service Account"
    echo "Service account created: $SA_EMAIL"
else
    echo "Service account already exists: $SA_EMAIL"
fi

# Export-SA for GCS Signed URLs via Impersonation
if ! gcloud iam service-accounts describe "$EXPORT_SA_EMAIL" &>/dev/null; then
    gcloud iam service-accounts create "$EXPORT_SA_NAME" \
        --description="Service account used for generating GCS signed URLs via token impersonation" \
        --display-name="AI Health Companion Export-SA"
    echo "Service account created: $EXPORT_SA_EMAIL"
else
    echo "Service account already exists: $EXPORT_SA_EMAIL"
fi

# Allow main app service account to token create / impersonate export-sa
echo "Granting Token Creator role to main app service account on export-sa..."
gcloud iam service-accounts add-iam-policy-binding "$EXPORT_SA_EMAIL" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/iam.serviceAccountTokenCreator" >/dev/null

# Allow main service account to act as service account user on itself for Cloud Run Jobs
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/iam.serviceAccountUser" >/dev/null

# Grant token creator to current developer user for local runs
DEVELOPER_USER=$(gcloud config get-value account 2>/dev/null || echo "")
if [[ -n "$DEVELOPER_USER" ]]; then
    echo "Granting token creator role to developer user ($DEVELOPER_USER)..."
    gcloud iam service-accounts add-iam-policy-binding "$EXPORT_SA_EMAIL" \
        --member="user:$DEVELOPER_USER" \
        --role="roles/iam.serviceAccountTokenCreator" >/dev/null
fi

# 5. Grant IAM Roles
echo -e "${GREEN}[4/8] Configuring Project IAM Roles...${NC}"
APP_ROLES=(
    "roles/datastore.user"               # Firestore Read/Write/Query
    "roles/storage.objectAdmin"          # GCS Bucket read/write
    "roles/pubsub.publisher"             # Pub/Sub publish rights
    "roles/pubsub.subscriber"            # Pub/Sub subscription rights
    "roles/aiplatform.user"              # Vertex AI Gemini inference
    "roles/documentai.apiUser"           # Document AI OCR parsing
    "roles/speech.client"                # Speech-to-text Chirp client
    "roles/cloudtranslate.user"          # Cloud Translation API
    "roles/secretmanager.secretAccessor" # Access secrets in Secret Manager
    "roles/cloudtasks.enqueuer"          # Enqueue Cloud Tasks
    "roles/run.developer"                # Trigger Cloud Run Jobs (export-job, account-deletion-job)
    "roles/firebase.admin"               # Firebase Auth user administration & claim management
)

for role in "${APP_ROLES[@]}"; do
    echo "Binding role $role to $SA_EMAIL..."
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" >/dev/null
done

# Grant storage.objectAdmin to export-sa for direct archive reads/writes
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$EXPORT_SA_EMAIL" \
    --role="roles/storage.objectAdmin" >/dev/null

# 6. Create GCS Storage Bucket
echo -e "${GREEN}[5/8] Provisioning GCS Bucket...${NC}"
if ! gsutil ls -b "gs://$BUCKET_NAME" &>/dev/null; then
    gsutil mb -l "$REGION" "gs://$BUCKET_NAME"
    # Configure CORS on the bucket for direct-to-GCS uploads
    CORS_JSON=$(mktemp)
    cat <<EOF > "$CORS_JSON"
[
  {
    "origin": ["*"],
    "responseHeader": ["Content-Type", "Content-Length", "Date"],
    "method": ["GET", "PUT", "POST", "OPTIONS"],
    "maxAgeSeconds": 3600
  }
]
EOF
    gsutil cors set "$CORS_JSON" "gs://$BUCKET_NAME"
    rm -f "$CORS_JSON"
    echo "GCS Bucket gs://$BUCKET_NAME created and CORS policy configured."
else
    echo "GCS Bucket gs://$BUCKET_NAME already exists."
fi

# 7. Create Pub/Sub Topics
echo -e "${GREEN}[6/8] Creating Pub/Sub Topics...${NC}"
TOPICS=(
    "consultation-audio-uploaded"
    "consultation-transcribed"
    "consultation-published"
    "document-uploaded"
)

for topic in "${TOPICS[@]}"; do
    if ! gcloud pubsub topics describe "$topic" &>/dev/null; then
        gcloud pubsub topics create "$topic"
        echo "Pub/Sub Topic '$topic' created."
    else
        echo "Pub/Sub Topic '$topic' already exists."
    fi
done

# 8. Create Cloud Tasks Queues
echo -e "${GREEN}[7/8] Creating Cloud Tasks Queues...${NC}"
QUEUES=(
    "notification-queue"
    "account-deletion-queue"
)

for q in "${QUEUES[@]}"; do
    if ! gcloud tasks queues describe "$q" --location="$REGION" &>/dev/null; then
        gcloud tasks queues create "$q" --location="$REGION"
        echo "Cloud Tasks Queue '$q' created."
    else
        echo "Cloud Tasks Queue '$q' already exists."
    fi
done

# 9. Complete bootstrap
echo -e "${BLUE}==================================================${NC}"
echo -e "${GREEN}      GCP Setup Completed Successfully!          ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo
echo "Your GCP environment is ready for AI Health Companion backend."
echo "Main Service Account: $SA_EMAIL"
echo "Export Service Account: $EXPORT_SA_EMAIL"
echo "Queues Provisioned: notification-queue, account-deletion-queue"
echo "Deploy services using build.sh and run.sh inside respective service directories."
