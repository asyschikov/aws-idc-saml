#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STACK_NAME="IdcSamlSampleApp"

echo "=== Installing CDK dependencies ==="
cd "$SCRIPT_DIR/cdk"
npm install

echo "=== Deploying CDK stack ==="
npx cdk deploy "$STACK_NAME" \
  --require-approval never \
  --outputs-file "$SCRIPT_DIR/cdk-outputs.json"

echo "=== Generating frontend config ==="
python3 "$SCRIPT_DIR/generate-config.py"

BUCKET=$(jq -r ".[\"$STACK_NAME\"].BucketName" "$SCRIPT_DIR/cdk-outputs.json")
CF_URL=$(jq -r ".[\"$STACK_NAME\"].CloudFrontUrl" "$SCRIPT_DIR/cdk-outputs.json")
CF_DIST_ID=$(jq -r ".[\"$STACK_NAME\"].DistributionId" "$SCRIPT_DIR/cdk-outputs.json")

echo "=== Building frontend ==="
cd "$SCRIPT_DIR/frontend"
npm install
npm run build

echo "=== Uploading to S3 ==="
aws s3 sync "$SCRIPT_DIR/frontend/dist/" "s3://$BUCKET/" --delete

echo "=== Invalidating CloudFront cache ==="
INVALIDATION_ID=$(aws cloudfront create-invalidation \
  --distribution-id "$CF_DIST_ID" \
  --paths "/*" \
  --query "Invalidation.Id" \
  --output text)

echo "Waiting for invalidation $INVALIDATION_ID..."
aws cloudfront wait invalidation-completed \
  --distribution-id "$CF_DIST_ID" \
  --id "$INVALIDATION_ID"

echo ""
echo "=== Done! ==="
echo "Open: $CF_URL"
