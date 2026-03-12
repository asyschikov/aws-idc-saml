import json
import os

import boto3

_identity_store_id = None


def get_identity_store_id(region):
    """Look up the Identity Store ID from the SSO instance (cached across invocations)."""
    global _identity_store_id
    if _identity_store_id is None:
        sso = boto3.client("sso-admin", region_name=region)
        instances = sso.list_instances()["Instances"]
        _identity_store_id = instances[0]["IdentityStoreId"]
    return _identity_store_id


def handler(event, context):
    """Cognito Pre Token Generation trigger (V2).

    Looks up the user's email in the IDC Identity Store, finds their group
    memberships, and injects a 'groups' claim into the ID and access tokens.
    """
    print(f"Event: {json.dumps(event)}")

    region = os.environ.get("IDC_REGION") or os.environ["AWS_REGION"]
    identity_store_id = get_identity_store_id(region)

    email = event["request"]["userAttributes"].get("email")
    if not email:
        return event

    client = boto3.client("identitystore", region_name=region)

    # Find user in Identity Store by email
    users = client.list_users(
        IdentityStoreId=identity_store_id,
        Filters=[{"AttributePath": "UserName", "AttributeValue": email}],
    ).get("Users", [])

    # Fall back to searching all users by email if username lookup fails
    if not users:
        all_users = client.list_users(IdentityStoreId=identity_store_id).get("Users", [])
        users = [
            u for u in all_users
            if any(e.get("Value") == email for e in u.get("Emails", []))
        ]

    if not users:
        return event

    user_id = users[0]["UserId"]

    # Get group memberships
    memberships = client.list_group_memberships_for_member(
        IdentityStoreId=identity_store_id,
        MemberId={"UserId": user_id},
    ).get("GroupMemberships", [])

    group_ids = [m["GroupId"] for m in memberships]

    # Resolve group names
    groups = []
    for group_id in group_ids:
        group = client.describe_group(
            IdentityStoreId=identity_store_id,
            GroupId=group_id,
        )
        groups.append(group["DisplayName"])

    # Inject groups claim into tokens (V1 trigger format)
    event["response"] = {
        "claimsOverrideDetails": {
            "claimsToAddOrOverride": {
                "groups": ",".join(groups),
            },
            "groupOverrideDetails": {
                "groupsToOverride": groups,
            },
        },
    }

    print(f"Groups: {groups}")
    return event
