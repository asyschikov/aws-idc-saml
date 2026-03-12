#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CDK_DIR="$SCRIPT_DIR/cdk"

if [[ ! -f "$SCRIPT_DIR/env.sh" ]]; then
  echo "Error: env.sh not found. Copy env-template.sh to env.sh and fill in your profiles."
  exit 1
fi
source "$SCRIPT_DIR/env.sh"

deploy_idc() {
  echo "=== Deploying IDC stack ==="
  cd "$CDK_DIR"
  npm install --silent

  if [[ -f "$SCRIPT_DIR/app-outputs.json" ]]; then
    echo "  Phase 2: configuring SP settings + group assignments"
  else
    echo "  Phase 1: creating IDC app skeleton"
  fi

  npx cdk deploy IdcSamlOrgIdc \
    --profile "$IDC_PROFILE" \
    --require-approval never \
    --outputs-file "$SCRIPT_DIR/idc-outputs.json" \
    -c group=idc
}

deploy_app() {
  echo "=== Deploying App stack ==="

  if [[ ! -f "$SCRIPT_DIR/idc-outputs.json" ]]; then
    echo "Error: idc-outputs.json not found. Run './deploy.sh idc' first."
    exit 1
  fi

  cd "$CDK_DIR"
  npm install --silent

  npx cdk deploy IdcSamlOrgApp \
    --profile "$APP_PROFILE" \
    --require-approval never \
    --outputs-file "$SCRIPT_DIR/app-outputs.json" \
    -c group=app
}

deploy_frontend() {
  echo "=== Deploying frontend ==="

  if [[ ! -f "$SCRIPT_DIR/app-outputs.json" ]]; then
    echo "Error: app-outputs.json not found. Run './deploy.sh app' first."
    exit 1
  fi

  echo "  Generating frontend config..."
  python3 "$SCRIPT_DIR/generate-config.py"

  local BUCKET
  BUCKET=$(jq -r '.IdcSamlOrgApp.BucketName' "$SCRIPT_DIR/app-outputs.json")
  local CF_URL
  CF_URL=$(jq -r '.IdcSamlOrgApp.CloudFrontUrl' "$SCRIPT_DIR/app-outputs.json")
  local CF_DIST_ID
  CF_DIST_ID=$(jq -r '.IdcSamlOrgApp.DistributionId' "$SCRIPT_DIR/app-outputs.json")

  echo "  Building frontend..."
  cd "$SCRIPT_DIR/frontend"
  npm install
  npm run build

  echo "  Uploading to S3..."
  aws s3 sync "$SCRIPT_DIR/frontend/dist/" "s3://$BUCKET/" --delete --profile "$APP_PROFILE"

  echo "  Invalidating CloudFront cache..."
  local INVALIDATION_ID
  INVALIDATION_ID=$(aws cloudfront create-invalidation \
    --distribution-id "$CF_DIST_ID" \
    --paths "/*" \
    --query "Invalidation.Id" \
    --output text \
    --profile "$APP_PROFILE")

  echo "  Waiting for invalidation $INVALIDATION_ID..."
  aws cloudfront wait invalidation-completed \
    --distribution-id "$CF_DIST_ID" \
    --id "$INVALIDATION_ID" \
    --profile "$APP_PROFILE"

  echo ""
  echo "=== Done! ==="
  echo "Open: $CF_URL"
}

case "${1:-}" in
  idc)
    deploy_idc
    ;;
  app)
    deploy_app
    ;;
  frontend)
    deploy_frontend
    ;;
  all)
    deploy_idc
    deploy_app
    deploy_idc
    deploy_frontend
    ;;
  *)
    echo "Usage: $0 {idc|app|frontend|all}"
    echo ""
    echo "  idc       Deploy IDC stack (Phase 1 or Phase 2)"
    echo "  app       Deploy App stack (requires idc-outputs.json)"
    echo "  frontend  Build and deploy frontend (requires app-outputs.json)"
    echo "  all       Run idc → app → idc → frontend"
    exit 1
    ;;
esac
