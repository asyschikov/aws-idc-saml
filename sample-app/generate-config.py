#!/usr/bin/env python3
"""Generate frontend/src/config.ts from CDK stack outputs.

Usage:
    python generate-config.py   # uses cdk-outputs.json if present, else fetches from CloudFormation
"""

import json
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STACK_NAME = "IdcSamlSampleApp"
OUTPUTS_FILE = SCRIPT_DIR / "cdk-outputs.json"
CONFIG_FILE = SCRIPT_DIR / "frontend" / "src" / "config.ts"


def fetch_outputs_from_cfn() -> dict:
    """Fetch stack outputs from CloudFormation."""
    cmd = [
        "aws", "cloudformation", "describe-stacks",
        "--stack-name", STACK_NAME,
        "--query", "Stacks[0].Outputs",
        "--output", "json",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    outputs_list = json.loads(result.stdout)
    outputs = {item["OutputKey"]: item["OutputValue"] for item in outputs_list}

    # Save in the same format as cdk deploy --outputs-file
    data = {STACK_NAME: outputs}
    OUTPUTS_FILE.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Fetched outputs from CloudFormation and saved to {OUTPUTS_FILE}")
    return outputs


def load_outputs() -> dict:
    """Load outputs from cdk-outputs.json or fetch from CloudFormation."""
    if OUTPUTS_FILE.exists():
        data = json.loads(OUTPUTS_FILE.read_text())
        print(f"Using existing {OUTPUTS_FILE}")
        return data[STACK_NAME]
    return fetch_outputs_from_cfn()


def main():
    outputs = load_outputs()

    user_pool_id = outputs["UserPoolId"]
    client_id = outputs["UserPoolClientId"]
    cognito_domain = outputs["CognitoDomain"]
    cloud_front_url = outputs["CloudFrontUrl"]

    config_ts = f'''export const config = {{
  userPoolId: "{user_pool_id}",
  userPoolClientId: "{client_id}",
  cognitoDomain: "{cognito_domain}",
  cloudFrontUrl: "{cloud_front_url}",
}};
'''

    CONFIG_FILE.write_text(config_ts)
    print(f"Generated {CONFIG_FILE}")
    print(f"  userPoolId:       {user_pool_id}")
    print(f"  userPoolClientId: {client_id}")
    print(f"  cognitoDomain:    {cognito_domain}")
    print(f"  cloudFrontUrl:    {cloud_front_url}")


if __name__ == "__main__":
    main()
