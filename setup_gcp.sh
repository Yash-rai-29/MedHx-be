#!/usr/bin/env bash
# setup_gcp.sh — GCP Infrastructure Setup Script
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

echo -e "${BLUE}==================================================${NC}"
echo -e "${BLUE}       GCP Bootstrap for AI Health Companion      ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo

# Confirm Project ID
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
echo -e "${GREEN}[1/7] Enabling Google Cloud Service APIs...${NC}"
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
    cloudtasks.googleapis.com


# 3. Create Service Account for Cloud Run services
echo -e "${GREEN}[2/7] Provisioning Service Account...${NC}"
SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$SA_EMAIL" &>/dev/null; then
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --description="Service account for AI Health Companion Cloud Run microservices" \
        --display-name="AI Health Companion Service Account"
    echo "Service account created: $SA_EMAIL"
else
    echo "Service account already exists: $SA_EMAIL"
fi

# Provision export-sa Service Account for GCS signed URL generation via impersonation
EXPORT_SA_NAME="export-sa"
EXPORT_SA_EMAIL="${EXPORT_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo -e "${GREEN}Provisioning export-sa Service Account...${NC}"
if ! gcloud iam service-accounts describe "$EXPORT_SA_EMAIL" &>/dev/null; then
    gcloud iam service-accounts create "$EXPORT_SA_NAME" \
        --description="Service account used for generating GCS signed URLs via impersonation" \
        --display-name="AI Health Companion Export-SA"
    echo "Service account created: $EXPORT_SA_EMAIL"
else
    echo "Service account already exists: $EXPORT_SA_EMAIL"
fi

# Allow main app service account to token create / impersonate export-sa:
echo "Granting Token Creator role to main app service account..."
gcloud iam service-accounts add-iam-policy-binding "$EXPORT_SA_EMAIL" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/iam.serviceAccountTokenCreator" >/dev/null

# Also grant roles/iam.serviceAccountTokenCreator to current developer user for local runs if authenticated:
DEVELOPER_USER=$(gcloud config get-value account 2>/dev/null || echo "")
if [[ -n "$DEVELOPER_USER" ]]; then
    echo "Granting token creator role to developer user: $DEVELOPER_USER"
    gcloud iam service-accounts add-iam-policy-binding "$EXPORT_SA_EMAIL" \
        --member="user:$DEVELOPER_USER" \
        --role="roles/iam.serviceAccountTokenCreator" >/dev/null
fi

# 4. Grant IAM Roles to the Service Account
echo -e "${GREEN}[3/7] Configuring IAM Roles...${NC}"
ROLES=(
    "roles/datastore.user"        # Firestore Read/Write/Query
    "roles/storage.objectAdmin"   # GCS Bucket read/write
    "roles/pubsub.publisher"      # Pub/Sub publish rights
    "roles/pubsub.subscriber"     # Pub/Sub subscription rights
    "roles/aiplatform.user"       # Vertex AI Gemini inference
    "roles/documentai.apiUser"    # Document AI processor processing
    "roles/speech.client"         # Speech-to-text Chirp client
    "roles/cloudtranslate.user"   # Google Cloud Translation User
    "roles/secretmanager.secretAccessor" # Access secrets in Secret Manager
    "roles/cloudtasks.enqueuer"          # Schedule Cloud Tasks
)

for role in "${ROLES[@]}"; do
    echo "Binding role $role to service account..."
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" >/dev/null
done

# 5. Create GCS Storage Bucket
echo -e "${GREEN}[4/7] Provisioning GCS Bucket...${NC}"
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

# Grant export-sa access to the bucket
echo "Granting roles/storage.objectAdmin role to export-sa on bucket gs://$BUCKET_NAME..."
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
    --member="serviceAccount:$EXPORT_SA_EMAIL" \
    --role="roles/storage.objectAdmin" >/dev/null


# 6. Create Pub/Sub Topics
echo -e "${GREEN}[5/7] Creating Pub/Sub Topics...${NC}"
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

# 7. Create Cloud Tasks Queue
echo -e "${GREEN}[6/7] Creating Cloud Tasks Queue...${NC}"
if ! gcloud tasks queues describe "notification-queue" --location="$REGION" &>/dev/null; then
    gcloud tasks queues create "notification-queue" --location="$REGION"
    echo "Cloud Tasks Queue 'notification-queue' created."
else
    echo "Cloud Tasks Queue 'notification-queue' already exists."
fi

# 8. Complete bootstrap
echo -e "${BLUE}==================================================${NC}"
echo -e "${GREEN}      GCP Setup Completed Successfully!          ${NC}"
echo -e "${BLUE}==================================================${NC}"
echo
echo "Your GCP environment is ready for AI Health Companion backend."
echo "Service Account Email: $SA_EMAIL"
echo "Deploy both services using run.sh inside patient_service/ and doctor_service/ directories."
