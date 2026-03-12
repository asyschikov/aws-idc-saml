"""
Create and manage SAML applications in IAM Identity Center using boto3.

Prerequisites:
    1. Install the custom CLI model (see README.md)
    2. pip install boto3

Usage:
    python boto3_example.py --region us-east-1 --action create \
        --display-name "My App" \
        --description "My SAML App" \
        --acs-url "https://example.auth.us-east-1.amazoncognito.com/saml2/idpresponse" \
        --audience "urn:amazon:cognito:sp:us-east-1_XXXXXXXX"

    python boto3_example.py --region us-east-1 --action list

    python boto3_example.py --region us-east-1 --action get --instance-id ins-XXXXXXXXXX

    python boto3_example.py --region us-east-1 --action delete --instance-id ins-XXXXXXXXXX
"""

import argparse
import uuid

import botocore.session


SAML_TEMPLATE_ID = "tpl-50e590700beb5208"


def create_client(region: str):
    session = botocore.session.get_session()
    return session.create_client(
        "idc-saml",
        region_name=region,
        endpoint_url=f"https://sso.{region}.amazonaws.com/control/",
    )


def list_applications(client):
    response = client.list_application_instances()
    for app in response.get("applicationInstances", []):
        display = app.get("display", {})
        protocol = app.get("template", {}).get("sSOProtocol", "?")
        print(f"  {app['instanceId']}  {protocol:5s}  {app['status']:8s}  {display.get('displayName', '')}")


def get_application(client, instance_id: str):
    response = client.get_application_instance(instanceId=instance_id)
    app = response["applicationInstance"]
    display = app.get("display", {})
    idp = app.get("identityProviderConfig", {})
    sp = app.get("serviceProviderConfig", {})

    print(f"Instance ID:   {app['instanceId']}")
    print(f"Display Name:  {display.get('displayName', '')}")
    print(f"Description:   {display.get('description', '')}")
    print(f"Status:        {app.get('status', '')}")
    print(f"Protocol:      {app.get('template', {}).get('sSOProtocol', '')}")
    if idp.get("metadataUrl"):
        print(f"Metadata URL:  {idp['metadataUrl']}")
    if sp.get("audience"):
        print(f"Audience:      {sp['audience']}")
    if sp.get("consumers"):
        print(f"ACS URL:       {sp['consumers'][0]['location']}")


def create_application(client, display_name: str, description: str, acs_url: str, audience: str) -> str:
    # Step 1: Create the application instance
    response = client.create_application_instance(
        templateId=SAML_TEMPLATE_ID,
        name=str(uuid.uuid4()),
    )
    instance_id = response["applicationInstance"]["instanceId"]
    print(f"Created instance: {instance_id}")

    # Step 2: Set display name and description
    client.update_application_instance_display_data(
        instanceId=instance_id,
        displayName=display_name,
        description=description,
    )

    # Step 3: Configure service provider (ACS URL and audience)
    client.update_application_instance_service_provider_configuration(
        instanceId=instance_id,
        serviceProviderConfig={
            "audience": audience,
            "consumers": [
                {
                    "location": acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                    "defaultValue": False,
                }
            ],
            "requireRequestSignature": False,
        },
    )

    # Step 4: Configure SAML attribute values
    client.update_application_instance_response_configuration(
        instanceId=instance_id,
        responseConfig={
            "subject": {"source": ["${user:email}"]},
            "properties": {"Email": {"source": ["${user:email}"]}},
            "ttl": "PT1H",
        },
    )

    # Step 5: Configure SAML attribute schema
    client.update_application_instance_response_schema_configuration(
        instanceId=instance_id,
        responseSchemaConfig={
            "subject": {
                "include": "REQUIRED",
                "nameIdFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            },
            "properties": {
                "Email": {
                    "attrNameFormat": "urn:oasis:names:tc:SAML:2.0:attrname-format:unspecified",
                    "include": "YES",
                }
            },
        },
    )

    # Step 6: Enable the application
    client.update_application_instance_status(
        instanceId=instance_id,
        status="ENABLED",
    )

    # Step 7: Get the metadata URL
    app = client.get_application_instance(instanceId=instance_id)["applicationInstance"]
    metadata_url = app["identityProviderConfig"]["metadataUrl"]
    print(f"Metadata URL:  {metadata_url}")

    return instance_id


def delete_application(client, instance_id: str):
    # Delete all profiles first
    profiles = client.list_profiles(instanceId=instance_id)
    for profile in profiles.get("applicationProfiles", []):
        print(f"Deleting profile: {profile['profileId']}")
        client.delete_profile(
            instanceId=instance_id,
            profileId=profile["profileId"],
        )

    # Delete the application
    client.delete_application_instance(instanceId=instance_id)
    print(f"Deleted: {instance_id}")


def main():
    parser = argparse.ArgumentParser(description="Manage IAM Identity Center SAML applications")
    parser.add_argument("--region", required=True, help="AWS region (must be your IDC home region)")
    parser.add_argument("--action", required=True, choices=["list", "get", "create", "delete"])
    parser.add_argument("--instance-id", help="Application instance ID (for get/delete)")
    parser.add_argument("--display-name", help="Display name (for create)")
    parser.add_argument("--description", help="Description (for create)")
    parser.add_argument("--acs-url", help="SAML ACS URL (for create)")
    parser.add_argument("--audience", help="SAML audience URI (for create)")
    args = parser.parse_args()

    client = create_client(args.region)

    if args.action == "list":
        list_applications(client)
    elif args.action == "get":
        if not args.instance_id:
            parser.error("--instance-id is required for get")
        get_application(client, args.instance_id)
    elif args.action == "create":
        for field in ("display_name", "description", "acs_url", "audience"):
            if not getattr(args, field):
                parser.error(f"--{field.replace('_', '-')} is required for create")
        create_application(client, args.display_name, args.description, args.acs_url, args.audience)
    elif args.action == "delete":
        if not args.instance_id:
            parser.error("--instance-id is required for delete")
        delete_application(client, args.instance_id)


if __name__ == "__main__":
    main()
