"""
CloudFormation Custom Resource Lambda for managing IAM Identity Center SAML applications.

Uses the idc-saml botocore model (service-2.json from this repo) for clean,
typed API calls instead of raw HTTP requests.

No external dependencies — only boto3/botocore from the Lambda runtime.

Packaging:
    Bundle the model files alongside this Lambda in the following structure:

    your-lambda-package/
      index.py                  (this file, renamed to index.py)
      models/
        idc-saml/
          2020-07-20/
            service-2.json          (from this repo)
            endpoint-rule-set-1.json (from this repo)

    In CDK:

        const idcAppFunction = new lambda.Function(this, 'IdcAppFunction', {
          code: lambda.Code.fromAsset('lambda/idc-app'),  // directory with the above structure
          runtime: lambda.Runtime.PYTHON_3_12,
          handler: 'index.handler',
          timeout: cdk.Duration.minutes(5),
        });

Required IAM permissions:
    - sso:* (on resource *)

Resource Properties:
    idcRegion       - IAM Identity Center home region (optional, defaults to Lambda's region)
    instanceArn     - IDC instance ARN (optional, defaults to first instance found)
    appName         - Display name for the SAML app (required)
    appDescription  - Description for the SAML app (optional, defaults to appName)
    acsUrl          - SAML Assertion Consumer Service URL (required)
    audience        - SAML audience URI (required)
    attributes      - Map of attribute name to source expression (optional)
                      e.g. {"Email": "${user:email}", "Name": "${user:displayName}"}
                      Defaults to {"Email": "${user:email}"}.

Return attributes (via Fn::GetAtt):
    instanceId      - IDC application instance ID (e.g. ins-XXXXXXXXXX)
    metadataUrl     - SAML metadata URL
    applicationArn  - Full application ARN
"""

import json
import os
import uuid
import urllib.request

import boto3
import botocore.session


# Template ID for Custom SAML 2.0 application
SAML_TEMPLATE_ID = "tpl-50e590700beb5208"

# Path to bundled models directory (relative to this file)
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def get_idc_client(region):
    """Create an idc-saml botocore client using the bundled model."""
    session = botocore.session.get_session()
    session.get_component("data_loader").search_paths.append(MODELS_DIR)
    return session.create_client(
        "idc-saml",
        region_name=region,
    )


def get_sso_admin_client(region):
    """Create a standard sso-admin client (for assignment operations)."""
    return boto3.Session(region_name=region).client("sso-admin")


# ---------------------------------------------------------------------------
# CloudFormation response helper
# ---------------------------------------------------------------------------

def send_cfn_response(event, context, status, data=None, reason=None, physical_resource_id=None):
    """Send response back to CloudFormation."""
    body = json.dumps({
        "Status": status,
        "Reason": reason or f"See CloudWatch Log Stream: {context.log_stream_name}",
        "PhysicalResourceId": physical_resource_id or context.log_stream_name,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }).encode("utf-8")

    req = urllib.request.Request(event["ResponseURL"], data=body, headers={"Content-Type": "application/json"}, method="PUT")
    urllib.request.urlopen(req)


# ---------------------------------------------------------------------------
# SSO instance helpers
# ---------------------------------------------------------------------------

def get_sso_instance(sso_client, instance_arn=None):
    """Get the SSO instance ARN, Identity Store ID, and owner account."""
    response = sso_client.list_instances()
    instances = response.get("Instances", [])
    if not instances:
        raise RuntimeError("No IAM Identity Center instance found")
    if instance_arn:
        inst = next((i for i in instances if i["InstanceArn"] == instance_arn), None)
        if not inst:
            raise RuntimeError(f"IDC instance {instance_arn} not found")
    else:
        inst = instances[0]
    return inst["InstanceArn"], inst["IdentityStoreId"], inst["OwnerAccountId"]


def build_app_arn(instance_id, owner_account_id, sso_instance_id):
    """Convert instance ID (ins-XXX) to a full application ARN."""
    app_id = instance_id.replace("ins-", "apl-")
    return f"arn:aws:sso::{owner_account_id}:application/{sso_instance_id}/{app_id}"


# ---------------------------------------------------------------------------
# Application CRUD
# ---------------------------------------------------------------------------

def find_existing_app(client, display_name):
    """Find an existing application by display name (for idempotency)."""
    response = client.list_application_instances()
    for app in response.get("applicationInstances", []):
        if app.get("display", {}).get("displayName") == display_name:
            return app
    return None


def create_saml_app(client, display_name, description, acs_url, audience, attributes):
    """Create a fully configured Custom SAML 2.0 application. Returns instance ID."""
    resp = client.create_application_instance(
        templateId=SAML_TEMPLATE_ID,
        name=str(uuid.uuid4()),
    )
    instance_id = resp["applicationInstance"]["instanceId"]

    client.update_application_instance_display_data(
        instanceId=instance_id,
        displayName=display_name,
        description=description,
    )

    configure_sp(client, instance_id, acs_url, audience)
    configure_attributes(client, instance_id, attributes)

    client.update_application_instance_status(
        instanceId=instance_id,
        status="ENABLED",
    )

    return instance_id


def configure_sp(client, instance_id, acs_url, audience):
    """Update the service provider configuration (ACS URL and audience)."""
    client.update_application_instance_service_provider_configuration(
        instanceId=instance_id,
        serviceProviderConfig={
            "audience": audience,
            "consumers": [{
                "location": acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                "defaultValue": False,
            }],
            "requireRequestSignature": False,
        },
    )


def configure_attributes(client, instance_id, attributes):
    """Configure SAML attribute mappings."""
    if not attributes:
        attributes = {"Email": "${user:email}"}

    first_source = next(iter(attributes.values()))

    client.update_application_instance_response_configuration(
        instanceId=instance_id,
        responseConfig={
            "subject": {"source": [first_source]},
            "properties": {name: {"source": [source]} for name, source in attributes.items()},
            "ttl": "PT1H",
        },
    )

    client.update_application_instance_response_schema_configuration(
        instanceId=instance_id,
        responseSchemaConfig={
            "subject": {
                "include": "REQUIRED",
                "nameIdFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            },
            "properties": {
                name: {
                    "attrNameFormat": "urn:oasis:names:tc:SAML:2.0:attrname-format:unspecified",
                    "include": "YES",
                }
                for name in attributes
            },
        },
    )


def delete_saml_app(client, region, instance_id, app_arn):
    """Delete an application and all its assignments and profiles."""
    # Delete assignments via sso-admin
    sso_admin = get_sso_admin_client(region)
    try:
        resp = sso_admin.list_application_assignments(ApplicationArn=app_arn)
        for a in resp.get("ApplicationAssignments", []):
            sso_admin.delete_application_assignment(
                ApplicationArn=app_arn,
                PrincipalId=a["PrincipalId"],
                PrincipalType=a["PrincipalType"],
            )
    except Exception:
        pass

    # Delete profiles via idc-saml
    try:
        resp = client.list_profiles(instanceId=instance_id)
        for p in resp.get("applicationProfiles", []):
            client.delete_profile(instanceId=instance_id, profileId=p["profileId"])
    except Exception:
        pass

    client.delete_application_instance(instanceId=instance_id)


# ---------------------------------------------------------------------------
# CloudFormation handler
# ---------------------------------------------------------------------------

def handler(event, context):
    """CloudFormation Custom Resource handler."""
    print(f"Event: {json.dumps(event)}")

    try:
        request_type = event["RequestType"]
        props = event["ResourceProperties"]

        idc_region = props.get("idcRegion") or os.environ.get("AWS_REGION")
        app_name = props["appName"]
        app_description = props.get("appDescription", app_name)
        acs_url = props.get("acsUrl")
        audience = props.get("audience")
        attributes = props.get("attributes")

        client = get_idc_client(idc_region)
        sso_admin = get_sso_admin_client(idc_region)

        instance_arn, identity_store_id, owner_account_id = get_sso_instance(
            sso_admin, props.get("instanceArn")
        )
        sso_instance_id = instance_arn.split("/")[-1]

        if request_type == "Create":
            existing = find_existing_app(client, app_name)
            if existing:
                instance_id = existing["instanceId"]
                if acs_url and audience:
                    configure_sp(client, instance_id, acs_url, audience)
                    configure_attributes(client, instance_id, attributes)
            else:
                instance_id = create_saml_app(client, app_name, app_description, acs_url, audience, attributes)

            app_arn = build_app_arn(instance_id, owner_account_id, sso_instance_id)

            resp = client.get_application_instance(instanceId=instance_id)
            metadata_url = resp["applicationInstance"]["identityProviderConfig"]["metadataUrl"]

            send_cfn_response(event, context, "SUCCESS", {
                "instanceId": instance_id,
                "metadataUrl": metadata_url,
                "applicationArn": app_arn,
            }, physical_resource_id=instance_id)

        elif request_type == "Update":
            instance_id = event["PhysicalResourceId"]

            if acs_url and audience:
                configure_sp(client, instance_id, acs_url, audience)
                configure_attributes(client, instance_id, attributes)

            app_arn = build_app_arn(instance_id, owner_account_id, sso_instance_id)

            resp = client.get_application_instance(instanceId=instance_id)
            metadata_url = resp["applicationInstance"]["identityProviderConfig"]["metadataUrl"]

            send_cfn_response(event, context, "SUCCESS", {
                "instanceId": instance_id,
                "metadataUrl": metadata_url,
                "applicationArn": app_arn,
            }, physical_resource_id=instance_id)

        elif request_type == "Delete":
            instance_id = event["PhysicalResourceId"]
            if instance_id and instance_id.startswith("ins-"):
                app_arn = build_app_arn(instance_id, owner_account_id, sso_instance_id)
                try:
                    delete_saml_app(client, idc_region, instance_id, app_arn)
                except Exception as e:
                    print(f"Warning: Failed to delete IDC app: {e}")

            send_cfn_response(event, context, "SUCCESS", physical_resource_id=instance_id)

    except Exception as e:
        print(f"Error: {e}")
        send_cfn_response(
            event, context, "FAILED",
            reason=str(e),
            physical_resource_id=event.get("PhysicalResourceId", context.log_stream_name),
        )
