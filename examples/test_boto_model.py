"""Quick smoke test: can botocore load the idc-saml model and make a call?

Uses the models/ directory from this repo (not ~/.aws/models).
Pass region as first argument, defaults to eu-west-1.
"""

import os
import sys
import botocore.session

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")
region = sys.argv[1] if len(sys.argv) > 1 else "eu-west-1"

session = botocore.session.get_session()
session.get_component("data_loader").search_paths.append(MODELS_DIR)

client = session.create_client(
    "idc-saml",
    region_name=region,
)

print(f"Client created for region {region}")
print(f"Operations: {client._service_model.operation_names}")

response = client.list_application_instances()
apps = response.get("applicationInstances", [])
print(f"\n{len(apps)} application(s):")
for app in apps:
    print(f"  {app['instanceId']}  {app.get('display', {}).get('displayName', '(no name)')}")
