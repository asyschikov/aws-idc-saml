"""
CDK cr.Provider handler for assigning an IDC group to an SSO application.
"""

import json
import os

import boto3


def get_identity_store_id(sso_admin, instance_arn=None):
    """Resolve identity store ID, optionally filtering by instance ARN."""
    instances = sso_admin.list_instances().get("Instances", [])
    if not instances:
        raise RuntimeError("No IAM Identity Center instance found")
    if instance_arn:
        inst = next((i for i in instances if i["InstanceArn"] == instance_arn), None)
        if not inst:
            raise RuntimeError(f"IDC instance {instance_arn} not found")
    else:
        inst = instances[0]
    return inst["IdentityStoreId"]


def handler(event, context):
    print(f"Event: {json.dumps(event)}")

    request_type = event["RequestType"]
    props = event["ResourceProperties"]
    idc_region = props.get("idcRegion") or os.environ.get("AWS_REGION")
    app_arn = props["applicationArn"]
    group_name = props["groupName"]

    session = boto3.Session(region_name=idc_region)
    sso_admin = session.client("sso-admin")
    identity_store = session.client("identitystore")

    identity_store_id = get_identity_store_id(sso_admin, props.get("instanceArn"))

    groups = identity_store.list_groups(
        IdentityStoreId=identity_store_id,
        Filters=[{"AttributePath": "DisplayName", "AttributeValue": group_name}],
    ).get("Groups", [])
    if not groups:
        raise RuntimeError(f"Group '{group_name}' not found in Identity Store")
    group_id = groups[0]["GroupId"]

    physical_id = f"{app_arn}|{group_name}"

    if request_type in ("Create", "Update"):
        try:
            sso_admin.create_application_assignment(
                ApplicationArn=app_arn,
                PrincipalId=group_id,
                PrincipalType="GROUP",
            )
        except sso_admin.exceptions.ConflictException:
            pass
        return {"PhysicalResourceId": physical_id}

    elif request_type == "Delete":
        try:
            sso_admin.delete_application_assignment(
                ApplicationArn=app_arn,
                PrincipalId=group_id,
                PrincipalType="GROUP",
            )
        except Exception as e:
            print(f"Warning: Failed to unassign group '{group_name}': {e}")
        return {"PhysicalResourceId": physical_id}
