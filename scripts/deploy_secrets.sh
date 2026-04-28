#!/usr/bin/env bash
# deploy_secrets.sh
# This script reads secrets from the .env file and deploys them to Kubernetes.

set -euo pipefail

ENV_FILE=".env"
NAMESPACE="resume-agent"
SECRET_NAME="resume-app-secrets"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: Could not find $ENV_FILE file in the current directory." >&2
    exit 1
fi

echo "Loading secrets from $ENV_FILE..."

# Parse .env file: strip comments, blank lines, and surrounding quotes
parse_env() {
    local file="$1"
    local key="$2"
    grep -E "^${key}=" "$file" | head -1 | cut -d'=' -f2- | sed "s/^['\"]//;s/['\"]$//"
}

GOOGLE_API_KEY=$(parse_env "$ENV_FILE" "GOOGLE_API_KEY")
NEXTAUTH_SECRET=$(parse_env "$ENV_FILE" "NEXTAUTH_SECRET")
INTERNAL_API_KEY=$(parse_env "$ENV_FILE" "INTERNAL_API_KEY")

if [ -z "$GOOGLE_API_KEY" ] || [ -z "$NEXTAUTH_SECRET" ] || [ -z "$INTERNAL_API_KEY" ]; then
    echo "Error: Missing required keys in $ENV_FILE (GOOGLE_API_KEY, NEXTAUTH_SECRET, or INTERNAL_API_KEY)." >&2
    exit 1
fi

echo "Deploying secret '$SECRET_NAME' to namespace '$NAMESPACE'..."

kubectl create secret generic "$SECRET_NAME" \
    --namespace="$NAMESPACE" \
    --from-literal="GOOGLE_API_KEY=$GOOGLE_API_KEY" \
    --from-literal="NEXTAUTH_SECRET=$NEXTAUTH_SECRET" \
    --from-literal="INTERNAL_API_KEY=$INTERNAL_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "Successfully deployed secrets!"
