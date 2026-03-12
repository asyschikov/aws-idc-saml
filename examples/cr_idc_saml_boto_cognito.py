"""
CloudFormation Custom Resource Lambda for managing IAM Identity Center SAML applications.

Uses the idc-saml botocore model (service-2.json from this repo) for clean,
typed API calls instead of raw HTTP requests.

Supports a two-phase deployment to break the circular dependency between IDC and Cognito:

  Phase 1 ("create"): Creates the IDC SAML app and returns the metadata URL.
      Cognito doesn't exist yet, so no SP configuration is done.
  Phase 2 ("configure"): After Cognito is deployed, updates the IDC app with
      the ACS URL and audience derived from Cognito outputs.

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

Phase 1 ("create") properties:
    phase           - "create" (required)
    idcRegion       - IAM Identity Center home region (optional, defaults to Lambda's region)
    instanceArn     - IDC instance ARN (optional, defaults to first instance found)
    appName         - Display name for the SAML app (required)
    appDescription  - Description for the SAML app (optional)

Phase 1 return attributes (via Fn::GetAtt):
    instanceId      - IDC application instance ID (e.g. ins-XXXXXXXXXX)
    metadataUrl     - SAML metadata URL for configuring Cognito
    applicationArn  - Full application ARN

Phase 2 ("configure") properties:
    phase           - "configure" (required)
    idcRegion       - IAM Identity Center home region (optional, defaults to Lambda's region)
    instanceArn     - IDC instance ARN (optional, defaults to first instance found)
    instanceId      - Instance ID from phase 1 (required)
    userPoolId      - Cognito User Pool ID (required)
    oauthDomain     - Cognito OAuth domain prefix (required)

Note: This Lambda assumes Cognito lives in the same region it runs in.
If your IDC home region differs, deploy the Lambda in the Cognito region
and set idcRegion explicitly.

Example CDK usage:

    // Phase 1: create the app, get metadata URL
    // idcRegion is optional — omit it if the Lambda runs in the IDC home region
    const idcApp = new cdk.CustomResource(this, 'IdcApp', {
      serviceToken: provider.serviceToken,
      properties: {
        phase: 'create',
        appName: 'My App',
        appDescription: 'My SAML application',
      },
    });
    const metadataUrl = idcApp.getAttString('metadataUrl');

    // ... create Cognito User Pool and SAML provider using metadataUrl ...

    // Phase 2: configure SP settings with Cognito outputs
    const idcAppConfig = new cdk.CustomResource(this, 'IdcAppConfig', {
      serviceToken: provider.serviceToken,
      properties: {
        phase: 'configure',
        instanceId: idcApp.getAttString('instanceId'),
        userPoolId: userPool.userPoolId,
        oauthDomain: oauthDomainPrefix,
      },
    });
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


def create_saml_app_skeleton(client, display_name, description):
    """Create a SAML app with display data only (no SP config). Returns instance ID.

    This is phase 1: the app exists and has a metadata URL, but is not yet
    configured with Cognito ACS URL/audience. Phase 2 (configure) does that.
    """
    resp = client.create_application_instance(
        templateId=SAML_TEMPLATE_ID,
        name=str(uuid.uuid4()),
    )
    instance_id = resp["applicationInstance"]["instanceId"]

    client.update_application_instance_display_data(
        instanceId=instance_id,
        displayName=display_name,
        description=description or display_name,
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


def configure_cognito_attributes(client, instance_id):
    """Configure SAML attribute mappings for Cognito (Email only)."""
    client.update_application_instance_response_configuration(
        instanceId=instance_id,
        responseConfig={
            "subject": {"source": ["${user:email}"]},
            "properties": {"Email": {"source": ["${user:email}"]}},
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
                "Email": {
                    "attrNameFormat": "urn:oasis:names:tc:SAML:2.0:attrname-format:unspecified",
                    "include": "YES",
                },
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
        phase = props.get("phase", "create")
        idc_region = props.get("idcRegion") or os.environ.get("AWS_REGION")

        client = get_idc_client(idc_region)
        sso_admin = get_sso_admin_client(idc_region)

        instance_arn, identity_store_id, owner_account_id = get_sso_instance(
            sso_admin, props.get("instanceArn")
        )
        sso_instance_id = instance_arn.split("/")[-1]

        # ----- Phase 1: Create the SAML app (no Cognito outputs needed) -----
        if phase == "create":
            app_name = props["appName"]
            app_description = props.get("appDescription", app_name)

            if request_type == "Create":
                # Idempotent: reuse existing app if one with the same name exists
                existing = find_existing_app(client, app_name)
                if existing:
                    instance_id = existing["instanceId"]
                else:
                    instance_id = create_saml_app_skeleton(client, app_name, app_description)

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

        # ----- Phase 2: Configure SP settings with Cognito outputs -----
        elif phase == "configure":
            instance_id = props["instanceId"]
            user_pool_id = props["userPoolId"]
            oauth_domain = props["oauthDomain"]

            # Cognito region is always the region this Lambda runs in
            cognito_region = os.environ.get("AWS_REGION")
            acs_url = f"https://{oauth_domain}.auth.{cognito_region}.amazoncognito.com/saml2/idpresponse"
            audience = f"urn:amazon:cognito:sp:{user_pool_id}"

            if request_type in ("Create", "Update"):
                configure_sp(client, instance_id, acs_url, audience)
                configure_cognito_attributes(client, instance_id)

                client.update_application_instance_status(
                    instanceId=instance_id,
                    status="ENABLED",
                )

                send_cfn_response(event, context, "SUCCESS", physical_resource_id=instance_id)

            elif request_type == "Delete":
                # Phase 1 handles actual deletion; phase 2 is a no-op on delete
                send_cfn_response(event, context, "SUCCESS", physical_resource_id=instance_id)

    except Exception as e:
        print(f"Error: {e}")
        send_cfn_response(
            event, context, "FAILED",
            reason=str(e),
            physical_resource_id=event.get("PhysicalResourceId", context.log_stream_name),
        )
