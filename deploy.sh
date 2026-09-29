#!/bin/bash
# Deploys the ge-qualify-agent service to Cloud Run from source.
#
# Enforces --max-instances=1 to mitigate L13 (in-memory session state)
# until Phase 3 GCS persistence is deployed.
# Grants roles/run.invoker to the Gemini Enterprise Discovery Engine service agent.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
PROJECT_NUMBER="${PROJECT_NUMBER:-369594916120}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"
GENAI_LOCATION="${GENAI_LOCATION:-global}"
MODEL_NAME="${MODEL_NAME:-gemini-3.8-flash}"
MEMORY="${MEMORY:-1Gi}"
MAX_INSTANCES="${MAX_INSTANCES:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env if present
if [ -f "${SCRIPT_DIR}/.env" ]; then
  echo "Loading environment variables from .env ..."
  set -a
  source "${SCRIPT_DIR}/.env"
  set +a
fi

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
PROJECT_NUMBER="${PROJECT_NUMBER:-369594916120}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"
GENAI_LOCATION="${GENAI_LOCATION:-global}"
MODEL_NAME="${MODEL_NAME:-gemini-3.8-flash}"
MEMORY="${MEMORY:-1Gi}"
MAX_INSTANCES="${MAX_INSTANCES:-1}"

echo "============================================================"
echo "Deploying '$SERVICE_NAME' to '$PROJECT_ID' / '$REGION'"
echo "Model: $MODEL_NAME ($GENAI_LOCATION) | Max Instances: $MAX_INSTANCES (L13 guard)"
echo "============================================================"

# WEB_OAUTH_CALLBACK gates one-click browser sign-in. It requires
# "${SERVICE_URL}/auth/callback" to be registered under Authentication -> Web
# on the Azure app, otherwise Microsoft answers AADSTS50011. With 0 the /auth
# page only explains which redirect URI still needs registering.
WEB_OAUTH_CALLBACK="${WEB_OAUTH_CALLBACK:-1}"

# Durable record and session storage.
#
# Without this the service runs InMemorySessionStore: every deploy signs all
# users out and discards in-progress interviews, and the Phase 1 -> Phase 2
# handover cannot work at all, because a technical reviewer in a new
# conversation has no way to reach a record written by an earlier one.
#
# Set QUALIFY_GCS_BUCKET=0 to deliberately run without persistence.
QUALIFY_GCS_BUCKET="${QUALIFY_GCS_BUCKET:-${PROJECT_ID}-qualify-records}"

if [ "$QUALIFY_GCS_BUCKET" = "0" ]; then
  echo "QUALIFY_GCS_BUCKET=0: deploying WITHOUT persistence (sessions die on deploy)."
  QUALIFY_GCS_BUCKET=""
else
  # Idempotent: succeeds whether or not the bucket already exists.
  if ! gcloud storage buckets describe "gs://${QUALIFY_GCS_BUCKET}" \
      --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Creating gs://${QUALIFY_GCS_BUCKET} ..."
    gcloud storage buckets create "gs://${QUALIFY_GCS_BUCKET}" \
      --project="$PROJECT_ID" \
      --location="$REGION" \
      --uniform-bucket-level-access
  fi

  # The Cloud Run runtime identity. Compute default unless overridden.
  RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"
  echo "Granting roles/storage.objectAdmin on the bucket to $RUNTIME_SA ..."
  gcloud storage buckets add-iam-policy-binding "gs://${QUALIFY_GCS_BUCKET}" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/storage.objectAdmin >/dev/null
fi

INGRESS_MODE="${INGRESS_MODE:-internal-and-cloud-load-balancing}"
RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

# ---------------------------------------------------------------------------
# Secrets live in Secret Manager, never in plain env vars.
#
# Plain `--set-env-vars` values are readable by anyone with run.services.get.
#   - ms-graph-client-secret: seeded once from MS_GRAPH_CLIENT_SECRET in .env.
#   - oauth-state-secret:     HMAC key that signs per-conversation sign-in
#                             links; generated once if missing.
# There is deliberately no MS_GRAPH_REFRESH_TOKEN: user tokens are held in
# memory per conversation only.
# ---------------------------------------------------------------------------
gcloud services enable secretmanager.googleapis.com --project="$PROJECT_ID" >/dev/null

ensure_secret() {  # name, value-if-creating
  local name="$1" value="$2"
  if ! gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    if [ -z "$value" ]; then
      echo "ERROR: secret '$name' does not exist and no value was provided to create it." >&2
      exit 1
    fi
    echo "Creating secret $name ..."
    printf '%s' "$value" | gcloud secrets create "$name" \
      --project="$PROJECT_ID" --replication-policy=automatic --data-file=- >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$name" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
}

ensure_secret ms-graph-client-secret "${MS_GRAPH_CLIENT_SECRET:-}"
ensure_secret oauth-state-secret "$(openssl rand -base64 48 | tr -d '\n')"

# One-time migration: an existing revision may still carry these as plain env
# vars (including a leaked user refresh token). Cloud Run refuses to turn an
# env var into a secret in place, so strip them first.
if gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$REGION" \
    --format='value(spec.template.spec.containers[0].env[].name)' 2>/dev/null \
    | tr ';' '\n' | grep -qE '^(MS_GRAPH_CLIENT_SECRET|MS_GRAPH_REFRESH_TOKEN)$'; then
  echo "Removing plain-text secret env vars from the existing service ..."
  gcloud run services update "$SERVICE_NAME" --project="$PROJECT_ID" --region="$REGION" \
    --remove-env-vars=MS_GRAPH_CLIENT_SECRET,MS_GRAPH_REFRESH_TOKEN >/dev/null
fi

# Initial deployment from source (builds Dockerfile)
gcloud run deploy "$SERVICE_NAME" \
  --source "$SCRIPT_DIR" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --memory "$MEMORY" \
  --min-instances 1 \
  --max-instances "$MAX_INSTANCES" \
  --ingress "$INGRESS_MODE" \
  --clear-base-image \
  --allow-unauthenticated \
  --set-secrets="MS_GRAPH_CLIENT_SECRET=ms-graph-client-secret:latest,OAUTH_STATE_SECRET=oauth-state-secret:latest" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${GENAI_LOCATION},GOOGLE_GENAI_USE_VERTEXAI=TRUE,MODEL=${MODEL_NAME},MS_GRAPH_TENANT_ID=${MS_GRAPH_TENANT_ID:-},MS_GRAPH_CLIENT_ID=${MS_GRAPH_CLIENT_ID:-},SHAREPOINT_INSTANCE_URL=${SHAREPOINT_INSTANCE_URL:-},WEB_OAUTH_CALLBACK=${WEB_OAUTH_CALLBACK},QUALIFY_GCS_BUCKET=${QUALIFY_GCS_BUCKET}"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='value(status.url)')

# Prefer the Global Load Balancer nip.io domain if provisioned
LB_IP=$(gcloud compute addresses describe "${SERVICE_NAME}-ip" --global --project="$PROJECT_ID" --format="value(address)" 2>/dev/null || true)
if [ -n "$LB_IP" ]; then
  PUBLIC_URL="https://${LB_IP}.nip.io"
else
  PUBLIC_URL="$SERVICE_URL"
fi

echo "Service deployed at: $SERVICE_URL (LB URL: $PUBLIC_URL)"

# Second pass: set AGENT_URL so the agent card and OAuth links advertise the Load Balancer endpoint
echo "Updating AGENT_URL=$PUBLIC_URL ..."
gcloud run services update "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --update-env-vars=AGENT_URL="$PUBLIC_URL"

# Grant roles/run.invoker to the Discovery Engine service agent
GE_SA="service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com"
echo "Granting roles/run.invoker to $GE_SA ..."
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --member="serviceAccount:${GE_SA}" \
  --role=roles/run.invoker >/dev/null

echo "============================================================"
echo "Deployment & IAM Setup Complete!"
echo "Cloud Run URL:     $SERVICE_URL (Ingress: $INGRESS_MODE)"
echo "Load Balancer URL: $PUBLIC_URL"
echo "Agent Card:        ${PUBLIC_URL}/.well-known/agent-card.json"
echo "============================================================"
