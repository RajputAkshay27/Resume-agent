#!/usr/bin/env bash
# deploy_storage_secrets.sh
# This script reads storage secrets from storage/.env and the root .env, then deploys them to Kubernetes.

set -euo pipefail

ROOT_ENV_FILE=".env"
STORAGE_ENV_FILE="storage/.env"
NAMESPACE="resume-agent"
SECRET_NAME="storage-s3-secret"

# Parse a specific key from an env file; strips surrounding quotes
parse_env() {
    local file="$1"
    local key="$2"
    if [ ! -f "$file" ]; then
        echo ""
        return
    fi
    grep -E "^${key}=" "$file" | head -1 | cut -d'=' -f2- | sed "s/^['\"]//;s/['\"]$//"
}

echo "Loading secrets..."

S3_ACCESS_KEY=$(parse_env "$STORAGE_ENV_FILE" "S3_ACCESS_KEY")
S3_SECRET_KEY=$(parse_env "$STORAGE_ENV_FILE" "S3_SECRET_KEY")
INTERNAL_API_KEY=$(parse_env "$ROOT_ENV_FILE" "INTERNAL_API_KEY")

if [ -z "$S3_ACCESS_KEY" ] || [ -z "$S3_SECRET_KEY" ] || [ -z "$INTERNAL_API_KEY" ]; then
    echo "Error: Missing required keys in .env files (S3_ACCESS_KEY, S3_SECRET_KEY, or INTERNAL_API_KEY)." >&2
    exit 1
fi

echo "Deploying secret '$SECRET_NAME' to namespace '$NAMESPACE'..."

kubectl create secret generic "$SECRET_NAME" \
    --namespace="$NAMESPACE" \
    --from-literal="S3_ACCESS_KEY=$S3_ACCESS_KEY" \
    --from-literal="S3_SECRET_KEY=$S3_SECRET_KEY" \
    --from-literal="INTERNAL_API_KEY=$INTERNAL_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "Successfully deployed storage secrets!"
