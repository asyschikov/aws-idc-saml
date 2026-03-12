"""
CloudFormation Custom Resource Lambda for managing IAM Identity Center SAML applications.

A general-purpose Custom Resource that creates, updates, and deletes IDC SAML
applications. Not tied to any specific SAML consumer (Cognito, etc.) — you provide
the ACS URL, audience, and attribute mappings as resource properties.

Uses undocumented SWBService APIs (the same APIs the AWS Console uses).
No external dependencies — only boto3/botocore from the Lambda runtime.

Deploy this as a Lambda function (Python 3.12+) and use it with a CloudFormation
Custom Resource or CDK cr.Provider.

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
                      The subject (NameID) is always set to the first attribute source,
                      or ${user:email} if no attributes are provided.

Return attributes (via Fn::GetAtt):
    instanceId      - IDC application instance ID (e.g. ins-XXXXXXXXXX)
    metadataUrl     - SAML metadata URL
    applicationArn  - Full application ARN

Example CDK usage:

    const idcApp = new cdk.CustomResource(this, 'IdcSamlApp', {
      serviceToken: provider.serviceToken,
      properties: {
        appName: 'My SAML App',
        appDescription: 'My application',
        acsUrl: 'https://myapp.example.com/saml/acs',
        audience: 'urn:myapp:example',
        attributes: { Email: '${user:email}' },
      },
    });

    const metadataUrl = idcApp.getAttString('metadataUrl');
"""

import json
import os
import uuid
import urllib.request

import boto3
import botocore.auth
import botocore.awsrequest

# Template ID for Custom SAML 2.0 application
SAML_TEMPLATE_ID = "tpl-50e590700beb5208"


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
# SWBService API helpers
# ---------------------------------------------------------------------------

def call_swb_api(session, region, action, payload, endpoint_suffix="control/"):
    """Call the SWBService / SWBExternalService API with SigV4 signing."""
    service_prefix = "SWBService" if endpoint_suffix == "control/" else "SWBExternalService"
    endpoint = f"https://sso.{region}.amazonaws.com/{endpoint_suffix}"

    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": f"{service_prefix}.{action}",
    }
    body = json.dumps(payload).encode("utf-8")

    request = botocore.awsrequest.AWSRequest(method="POST", url=endpoint, headers=headers, data=body)
    credentials = session.get_credentials().get_frozen_credentials()
    botocore.auth.SigV4Auth(credentials, "sso", region).add_auth(request)

    req = urllib.request.Request(endpoint, data=body, headers=dict(request.headers), method="POST")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{service_prefix}.{action} failed: {e.read().decode('utf-8')}")


# ---------------------------------------------------------------------------
# SSO instance helpers
# ---------------------------------------------------------------------------

def get_sso_instance(session, region, instance_arn=None):
    """Get the SSO instance ARN, Identity Store ID, and owner account."""
    response = call_swb_api(session, region, "ListInstances", {}, endpoint_suffix="")
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

def find_existing_app(session, region, display_name):
    """Find an existing application by display name (for idempotency)."""
    response = call_swb_api(session, region, "ListApplicationInstances", {})
    for app in response.get("applicationInstances", []):
        if app.get("display", {}).get("displayName") == display_name:
            return app
    return None


def create_saml_app(session, region, display_name, description, acs_url, audience, attributes):
    """Create a fully configured Custom SAML 2.0 application. Returns instance ID."""
    # Create
    resp = call_swb_api(session, region, "CreateApplicationInstance", {
        "templateId": SAML_TEMPLATE_ID,
        "name": str(uuid.uuid4()),
    })
    instance_id = resp["applicationInstance"]["instanceId"]

    # Display data
    call_swb_api(session, region, "UpdateApplicationInstanceDisplayData", {
        "instanceId": instance_id,
        "displayName": display_name,
        "description": description,
    })

    # Service provider config
    configure_sp(session, region, instance_id, acs_url, audience)

    # Attribute mappings
    configure_attributes(session, region, instance_id, attributes)

    # Enable
    call_swb_api(session, region, "UpdateApplicationInstanceStatus", {
        "instanceId": instance_id,
        "status": "ENABLED",
    })

    return instance_id


def configure_sp(session, region, instance_id, acs_url, audience):
    """Update the service provider configuration (ACS URL and audience)."""
    call_swb_api(session, region, "UpdateApplicationInstanceServiceProviderConfiguration", {
        "instanceId": instance_id,
        "serviceProviderConfig": {
            "audience": audience,
            "consumers": [{
                "location": acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                "defaultValue": False,
            }],
            "requireRequestSignature": False,
        },
    })


def configure_attributes(session, region, instance_id, attributes):
    """Configure SAML attribute mappings.

    attributes: dict mapping attribute name to source expression,
                e.g. {"Email": "${user:email}"}
    """
    if not attributes:
        attributes = {"Email": "${user:email}"}

    # Use the first attribute as the subject source
    first_source = next(iter(attributes.values()))

    # Response config (what data to send)
    response_properties = {name: {"source": [source]} for name, source in attributes.items()}
    call_swb_api(session, region, "UpdateApplicationInstanceResponseConfiguration", {
        "instanceId": instance_id,
        "responseConfig": {
            "subject": {"source": [first_source]},
            "properties": response_properties,
            "ttl": "PT1H",
        },
    })

    # Response schema config (format and inclusion)
    schema_properties = {
        name: {
            "attrNameFormat": "urn:oasis:names:tc:SAML:2.0:attrname-format:unspecified",
            "include": "YES",
        }
        for name in attributes
    }
    call_swb_api(session, region, "UpdateApplicationInstanceResponseSchemaConfiguration", {
        "instanceId": instance_id,
        "responseSchemaConfig": {
            "subject": {
                "include": "REQUIRED",
                "nameIdFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            },
            "properties": schema_properties,
        },
    })


def get_metadata_url(session, region, instance_id):
    """Get the SAML metadata URL for an application."""
    resp = call_swb_api(session, region, "GetApplicationInstance", {"instanceId": instance_id})
    return resp["applicationInstance"]["identityProviderConfig"]["metadataUrl"]


def delete_saml_app(session, region, instance_id, app_arn):
    """Delete an application and all its assignments and profiles."""
    # Delete assignments
    try:
        resp = call_swb_api(session, region, "ListApplicationAssignments", {"ApplicationArn": app_arn}, endpoint_suffix="")
        for a in resp.get("ApplicationAssignments", []):
            call_swb_api(session, region, "DeleteApplicationAssignment", {
                "ApplicationArn": app_arn,
                "PrincipalId": a["PrincipalId"],
                "PrincipalType": a["PrincipalType"],
            }, endpoint_suffix="")
    except RuntimeError:
        pass

    # Delete profiles
    try:
        resp = call_swb_api(session, region, "ListProfiles", {"instanceId": instance_id})
        for p in resp.get("applicationProfiles", []):
            call_swb_api(session, region, "DeleteProfile", {"instanceId": instance_id, "profileId": p["profileId"]})
    except RuntimeError:
        pass

    # Delete the application
    call_swb_api(session, region, "DeleteApplicationInstance", {"instanceId": instance_id})


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

        session = boto3.Session(region_name=idc_region)
        instance_arn, identity_store_id, owner_account_id = get_sso_instance(
            session, idc_region, props.get("instanceArn")
        )
        sso_instance_id = instance_arn.split("/")[-1]

        if request_type == "Create":
            # Idempotent: reuse existing app if one with the same name exists
            existing = find_existing_app(session, idc_region, app_name)
            if existing:
                instance_id = existing["instanceId"]
                if acs_url and audience:
                    configure_sp(session, idc_region, instance_id, acs_url, audience)
                    configure_attributes(session, idc_region, instance_id, attributes)
            else:
                instance_id = create_saml_app(
                    session, idc_region, app_name, app_description, acs_url, audience, attributes,
                )

            app_arn = build_app_arn(instance_id, owner_account_id, sso_instance_id)

            metadata_url = get_metadata_url(session, idc_region, instance_id)

            send_cfn_response(event, context, "SUCCESS", {
                "instanceId": instance_id,
                "metadataUrl": metadata_url,
                "applicationArn": app_arn,
            }, physical_resource_id=instance_id)

        elif request_type == "Update":
            instance_id = event["PhysicalResourceId"]

            if acs_url and audience:
                configure_sp(session, idc_region, instance_id, acs_url, audience)
                configure_attributes(session, idc_region, instance_id, attributes)

            app_arn = build_app_arn(instance_id, owner_account_id, sso_instance_id)

            metadata_url = get_metadata_url(session, idc_region, instance_id)

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
                    delete_saml_app(session, idc_region, instance_id, app_arn)
                except RuntimeError as e:
                    print(f"Warning: Failed to delete IDC app: {e}")

            send_cfn_response(event, context, "SUCCESS", physical_resource_id=instance_id)

    except Exception as e:
        print(f"Error: {e}")
        send_cfn_response(
            event, context, "FAILED",
            reason=str(e),
            physical_resource_id=event.get("PhysicalResourceId", context.log_stream_name),
        )
