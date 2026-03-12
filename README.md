# aws-idc-saml

Custom AWS CLI service model for managing SAML applications in AWS IAM Identity Center (formerly AWS SSO — and still `sso` in every API namespace, because AWS doesn't rename public APIs). Consider this the author's humble contribution to the effort: at least the CLI command namespace is called `idc-saml` now.

## Table of Contents

- [Why](#why)
- [Notes](#notes)
- [Security](#security)
- [Installation](#installation)
- [Available Commands](#available-commands)
- [Common Scenarios using AWS CLI](#common-scenarios-using-aws-cli)
- [Using with boto3](#using-with-boto3)
- [Using with Amazon Cognito](#using-with-amazon-cognito)

## Why

The public `sso-admin` APIs cannot create proper SAML 2.0 applications, retrieve SAML metadata URLs, or configure SAML-specific settings (ACS URL, audience, attribute mappings). The only way to do this through the official console is manually.

This project provides a custom AWS CLI model that exposes the public but undocumented APIs the IAM Identity Center console uses, so you can automate SAML application management from the AWS CLI or with boto3.


## Notes

The author gathered the information in this repository by trial and error, not all parameters, values, actions and their behavior are entirely clear. The author did his best effort to document the finding but you use this at your own risk. Since this is undocumented API, AWS has no obligation to keep it stable or even available in future. The author hope that one day AWS will make a proper public version of this API and ship the CLI/SDK.

- All APIs use the same SigV4 signing as the standard `sso-admin` service (signing name: `sso`).
- The `--region` must match your IAM Identity Center home region.
- The Custom SAML 2.0 template ID is `tpl-50e590700beb5208`.
- `name` of the application is mandatory during creation but seems not to be visible or usable after.
- Profiles appear to be created automatically by IAM Identity Center when users or groups are assigned to an application. There is no `create-profile` API — only `list-profiles` and `delete-profile`, which are needed for cleanup before deleting an application. User/group assignments themselves are managed through the standard `sso-admin` API (`create-application-assignment`, etc.).


## Security

Since these actions are undocumented, it is not entirely clear what is the best way to configure IAM permissions for them. The author used an admin role to create and manage applications. That said, the API actions appear to match the ones listed in the official [IAM Identity Center service authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_awsiamidentitycenter.html), so standard IAM policies referencing those actions should work. For example, the [AWSSSOMemberAccountAdministrator](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AWSSSOMemberAccountAdministrator.html) managed policy includes the relevant `sso:*` actions.

## Installation

Clone this repository and install the model:

```bash
git clone https://github.com/asyschikov/aws-idc-saml.git
cd aws-idc-saml
aws configure add-model --service-model file://service-2.json --service-name idc-saml
cp endpoint-rule-set-1.json ~/.aws/models/idc-saml/2020-07-20/endpoint-rule-set-1.json
```

Verify:

```bash
aws idc-saml help
```

## Available Commands

| Command | Description |
|---------|-------------|
| `create-application-instance` | Create a new SAML application |
| `get-application-instance` | Get application details (including SAML metadata URL) |
| `update-application-instance-display-data` | Set display name and description |
| `update-application-instance-service-provider-configuration` | Configure ACS URL and audience |
| `update-application-instance-response-configuration` | Set SAML attribute values |
| `update-application-instance-response-schema-configuration` | Set SAML attribute schema/format |
| `update-application-instance-status` | Enable or disable an application |
| `delete-application-instance` | Delete an application |
| `list-application-instances` | List all application instances |
| `list-profiles` | List profiles for an application |
| `delete-profile` | Delete a profile |
| `list-application-instance-certificates` | List SAML certificates |
| `describe-registered-regions` | List registered IDC regions |
| `list-directory-associations` | List directory associations |

## Common Scenarios using AWS CLI

Note: make sure to use the region where your AWS IDC instance is creates.

### List all applications

```bash
aws idc-saml list-application-instances
```

### Get SAML metadata URL for an application

```bash
aws idc-saml get-application-instance \
  --instance-id ins-XXXXXXXXXX \
  --query 'applicationInstance.identityProviderConfig.metadataUrl' \
  --output text
```

### Create a complete SAML application

This replicates what the IAM Identity Center console does when you create a Custom SAML 2.0 application.

**Step 1: Create the application instance**

```bash
INSTANCE_ID=$(aws idc-saml create-application-instance \
  --template-id tpl-50e590700beb5208 \
  --name "$(uuidgen)" \
  --query 'applicationInstance.instanceId' \
  --output text)

echo "Created: $INSTANCE_ID"
```

`tpl-50e590700beb5208` is the template ID for Custom SAML 2.0 applications.

**Step 2: Set display name and description**

```bash
aws idc-saml update-application-instance-display-data \
  --instance-id "$INSTANCE_ID" \
  --display-name "My SAML App" \
  --description "My application description"
```

**Step 3: Configure service provider (ACS URL and audience)**

```bash
aws idc-saml update-application-instance-service-provider-configuration \
  --instance-id "$INSTANCE_ID" \
  --service-provider-config '{
    "audience": "urn:amazon:cognito:sp:us-east-1_XXXXXXXX",
    "consumers": [{
      "location": "https://your-domain.auth.us-east-1.amazoncognito.com/saml2/idpresponse",
      "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
      "defaultValue": false
    }],
    "requireRequestSignature": false
  }'
```

**Step 4: Configure SAML attribute values**

```bash
aws idc-saml update-application-instance-response-configuration \
  --instance-id "$INSTANCE_ID" \
  --response-config '{
    "subject": {"source": ["${user:email}"]},
    "properties": {
      "Email": {"source": ["${user:email}"]}
    },
    "ttl": "PT1H"
  }'
```

**Step 5: Configure SAML attribute schema**

```bash
aws idc-saml update-application-instance-response-schema-configuration \
  --instance-id "$INSTANCE_ID" \
  --response-schema-config '{
    "subject": {
      "include": "REQUIRED",
      "nameIdFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    },
    "properties": {
      "Email": {
        "attrNameFormat": "urn:oasis:names:tc:SAML:2.0:attrname-format:unspecified",
        "include": "YES"
      }
    }
  }'
```

**Step 6: Enable the application**

```bash
aws idc-saml update-application-instance-status \
  --instance-id "$INSTANCE_ID" \
  --status ENABLED
```

**Step 7: Get the SAML metadata URL**

```bash
aws idc-saml get-application-instance \
  --instance-id "$INSTANCE_ID" \
  --query 'applicationInstance.identityProviderConfig.metadataUrl' \
  --output text
```

### Delete a SAML application

Before deleting an application, you must remove all profiles first.

```bash
INSTANCE_ID="ins-XXXXXXXXXX"

# List and delete all profiles
aws idc-saml list-profiles \
  --instance-id "$INSTANCE_ID" \
  --query 'applicationProfiles[].profileId' \
  --output text | tr '\t' '\n' | while read -r PROFILE_ID; do
    echo "Deleting profile: $PROFILE_ID"
    aws idc-saml delete-profile \
      --instance-id "$INSTANCE_ID" \
      --profile-id "$PROFILE_ID"
done

# Delete the application
aws idc-saml delete-application-instance \
  --instance-id "$INSTANCE_ID"
```

If the application also has user/group assignments, delete those first using the standard `sso-admin` CLI:

```bash
APP_ARN="arn:aws:sso::123456789012:application/ssoins-XXXXX/apl-XXXXX"

aws sso-admin list-application-assignments \
  --application-arn "$APP_ARN" \
  --query 'ApplicationAssignments[]' \
  --output json | jq -c '.[]' | while read -r ASSIGNMENT; do
    PRINCIPAL_ID=$(echo "$ASSIGNMENT" | jq -r '.PrincipalId')
    PRINCIPAL_TYPE=$(echo "$ASSIGNMENT" | jq -r '.PrincipalType')
    echo "Deleting assignment: $PRINCIPAL_TYPE $PRINCIPAL_ID"
    aws sso-admin delete-application-assignment \
      --application-arn "$APP_ARN" \
      --principal-id "$PRINCIPAL_ID" \
      --principal-type "$PRINCIPAL_TYPE"
done
```

### Instance ID conversion

The `instanceId` used by this CLI relates to the application ARN from `sso-admin`:

```
Application ARN: arn:aws:sso::123456789012:application/ssoins-XXXXX/apl-68044c9cb4b51402
Instance ID:     ins-68044c9cb4b51402
```

Replace the `apl-` prefix with `ins-`.

## Using with boto3

See [examples/boto3_example.py](examples/boto3_example.py) for a complete Python example.

CloudFormation Custom Resource Lambdas are also available in two flavors — raw HTTP (signs requests manually, no model dependency) and boto (uses the botocore model from this repo for cleaner API calls):

| Lambda | Style | Use case |
|--------|-------|----------|
| [cr_idc_saml_app.py](examples/cr_idc_saml_app.py) | raw HTTP | General-purpose, works with any SAML consumer |
| [cr_idc_saml_cognito.py](examples/cr_idc_saml_cognito.py) | raw HTTP | Cognito-specific with two-phase deployment |
| [cr_idc_saml_boto_app.py](examples/cr_idc_saml_boto_app.py) | botocore model | General-purpose, works with any SAML consumer |
| [cr_idc_saml_boto_cognito.py](examples/cr_idc_saml_boto_cognito.py) | botocore model | Cognito-specific with two-phase deployment |

The raw HTTP variants are self-contained — no model files needed. The boto variants require bundling `service-2.json` and `endpoint-rule-set-1.json` alongside the Lambda (see docstrings for packaging instructions).

The Cognito variants solve the circular dependency between IDC and Cognito using a two-phase Custom Resource approach. See [COGNITO.md](COGNITO.md) for a detailed guide on federating IAM Identity Center users into a custom web application through Amazon Cognito.

The custom model must be installed first (see [Installation](#installation)). Then you can load it in boto3:

```python
import botocore.session

session = botocore.session.get_session()
region = 'us-east-1'  # your IDC home region
client = session.create_client(
    'idc-saml',
    region_name=region,
)

# List all applications
response = client.list_application_instances()
for app in response['applicationInstances']:
    print(app['instanceId'], app['display'].get('displayName'))
```

## Using with Amazon Cognito

A common use case is federating IAM Identity Center users into a custom web application through Amazon Cognito. The IDC SAML app provides the identity, Cognito handles token issuance, and your app gets standard JWTs.

The challenge is a circular dependency: the IDC app needs Cognito's ACS URL, and Cognito needs the IDC app's metadata URL. The Cognito Custom Resource variants solve this with a two-phase deployment.

See [COGNITO.md](COGNITO.md) for the full guide, including architecture, deployment scripts, and CDK examples.
