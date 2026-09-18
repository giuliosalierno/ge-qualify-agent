#!/usr/bin/env bash
# ==============================================================================
# Provision Global External Application Load Balancer (Option A) for ge-qualify-agent
#
# Creates:
#   1. Global Static IPv4 Address (ge-qualify-agent-ip) -> <IP>.nip.io
#   2. Serverless NEG in us-central1 (ge-qualify-agent-neg-us-central1)
#   3. Global Backend Service (ge-qualify-agent-backends)
#   4. URL Map (ge-qualify-agent-lb)
#   5. Google-Managed SSL Certificate (ge-qualify-agent-cert-nip)
#   6. Target HTTPS Proxy (ge-qualify-agent-lb-target-proxy)
#   7. Global Forwarding Rule on TCP 443 (ge-qualify-agent-frontend)
#   8. Updates Cloud Run ingress to internal-and-cloud-load-balancing
# ==============================================================================
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-vais-c-exp}"
REGION="${CLOUD_RUN_REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"

echo "============================================================"
echo "Provisioning Load Balancer for '${SERVICE_NAME}' in '${PROJECT_ID}'"
echo "============================================================"

# 1. Global Static IP
if ! gcloud compute addresses describe "${SERVICE_NAME}-ip" --global --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "1. Creating global static IP ${SERVICE_NAME}-ip..."
  gcloud compute addresses create "${SERVICE_NAME}-ip" --global --project="${PROJECT_ID}"
fi
LB_IP=$(gcloud compute addresses describe "${SERVICE_NAME}-ip" --global --project="${PROJECT_ID}" --format="value(address)")
DOMAIN="${LB_IP}.nip.io"
echo "   Static IP: ${LB_IP}"
echo "   Domain:    https://${DOMAIN}"

# 2. Serverless NEG
NEG_NAME="${SERVICE_NAME}-neg-${REGION}"
if ! gcloud compute network-endpoint-groups describe "${NEG_NAME}" --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "2. Creating Serverless NEG ${NEG_NAME}..."
  gcloud compute network-endpoint-groups create "${NEG_NAME}" \
    --region="${REGION}" \
    --network-endpoint-type=serverless \
    --cloud-run-service="${SERVICE_NAME}" \
    --project="${PROJECT_ID}"
fi

# 3. Backend Service
BACKEND_NAME="${SERVICE_NAME}-backends"
if ! gcloud compute backend-services describe "${BACKEND_NAME}" --global --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "3. Creating Backend Service ${BACKEND_NAME}..."
  gcloud compute backend-services create "${BACKEND_NAME}" \
    --load-balancing-scheme=EXTERNAL_MANAGED \
    --protocol=HTTPS \
    --port-name=http \
    --global \
    --project="${PROJECT_ID}"
  gcloud compute backend-services add-backend "${BACKEND_NAME}" \
    --global \
    --network-endpoint-group="${NEG_NAME}" \
    --network-endpoint-group-region="${REGION}" \
    --project="${PROJECT_ID}"
fi

# 4. URL Map
URL_MAP_NAME="${SERVICE_NAME}-lb"
if ! gcloud compute url-maps describe "${URL_MAP_NAME}" --global --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "4. Creating URL Map ${URL_MAP_NAME}..."
  gcloud compute url-maps create "${URL_MAP_NAME}" \
    --default-service="${BACKEND_NAME}" \
    --global \
    --project="${PROJECT_ID}"
fi

# 5. Google-Managed SSL Certificate
CERT_NAME="${SERVICE_NAME}-cert-nip"
if ! gcloud compute ssl-certificates describe "${CERT_NAME}" --global --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "5. Creating Google-Managed SSL Certificate ${CERT_NAME} for ${DOMAIN}..."
  gcloud compute ssl-certificates create "${CERT_NAME}" \
    --domains="${DOMAIN}" \
    --global \
    --project="${PROJECT_ID}"
fi

# 6. Target HTTPS Proxy
PROXY_NAME="${SERVICE_NAME}-lb-target-proxy"
if ! gcloud compute target-https-proxies describe "${PROXY_NAME}" --global --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "6. Creating Target HTTPS Proxy ${PROXY_NAME}..."
  gcloud compute target-https-proxies create "${PROXY_NAME}" \
    --ssl-certificates="${CERT_NAME}" \
    --url-map="${URL_MAP_NAME}" \
    --global \
    --project="${PROJECT_ID}"
fi

# 7. Global Forwarding Rule (TCP 443)
FWD_RULE_NAME="${SERVICE_NAME}-frontend"
if ! gcloud compute forwarding-rules describe "${FWD_RULE_NAME}" --global --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "7. Creating Global Forwarding Rule ${FWD_RULE_NAME} on TCP 443..."
  gcloud compute forwarding-rules create "${FWD_RULE_NAME}" \
    --load-balancing-scheme=EXTERNAL_MANAGED \
    --network-tier=PREMIUM \
    --address="${SERVICE_NAME}-ip" \
    --target-https-proxy="${PROXY_NAME}" \
    --global \
    --ports=443 \
    --project="${PROJECT_ID}"
fi

# 8. Restrict Cloud Run ingress to Internal + Cloud Load Balancing
echo "8. Updating Cloud Run '${SERVICE_NAME}' ingress to internal-and-cloud-load-balancing..."
gcloud run services update "${SERVICE_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --ingress=internal-and-cloud-load-balancing \
  --update-env-vars="AGENT_URL=https://${DOMAIN}"

CERT_STATUS=$(gcloud compute ssl-certificates describe "${CERT_NAME}" --global --project="${PROJECT_ID}" --format="value(managed.status)")
echo "============================================================"
echo "Load Balancer & Cloud Run Ingress Configured!"
echo "  Static IP:     ${LB_IP}"
echo "  LB Endpoint:   https://${DOMAIN}"
echo "  Agent Card:    https://${DOMAIN}/.well-known/agent-card.json"
echo "  OAuth Callback: https://${DOMAIN}/auth/callback"
echo "  SSL Status:    ${CERT_STATUS} (Google-Managed SSL takes ~10-15m to become ACTIVE)"
echo "============================================================"
