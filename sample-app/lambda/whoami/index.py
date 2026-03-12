import json


def handler(event, context):
    """Return the authenticated user's email and groups from Cognito JWT claims."""
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    email = claims.get("email", "unknown")
    groups_raw = claims.get("groups", "")
    groups = [g for g in groups_raw.split(",") if g] if groups_raw else []

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"email": email, "groups": groups}),
    }
