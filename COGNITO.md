# Using IDC SAML Applications with Amazon Cognito

This guide describes how to use `idc-saml` to federate IAM Identity Center users into a custom web application through Amazon Cognito. This is the pattern the author uses in production.

## The Problem

You have a custom web application hosted in an AWS member account. Your users are managed in IAM Identity Center (with its own directory or federated from an external IdP). You want those users to log in to your app using their existing IDC credentials.

Amazon Cognito supports SAML identity providers, and IAM Identity Center can act as one — but only through a Custom SAML 2.0 application, which AWS provides no public API to create. This means every deployment requires a manual trip to the IAM Identity Center console to create the app, copy the metadata URL, and paste it into your infrastructure configuration, then deploy, extract Cognito outputs and paste them back to the IDC console.

With `idc-saml`, the entire flow can be automated.

## Architecture

```
User
 │
 │  1. Opens app, clicks "Sign in with IDC"
 ▼
Cognito Hosted UI
 │
 │  2. Redirects to IDC (SAML AuthnRequest)
 ▼
IAM Identity Center
 │  3. Authenticates user against its directory
 │  4. Checks group membership
 │  5. Posts SAML response with email attribute
 │     back to Cognito ACS URL
 ▼
Cognito
 │  6. Validates SAML response
 │  7. Creates/updates user in user pool
 │  8. Issues JWT tokens (ID + Access)
 ▼
Your Application
    9. Uses JWT to call your API (API Gateway + Lambda)
```

## How the Pieces Fit Together

There are two resources that reference each other, creating a circular dependency:

- **Cognito User Pool** needs the IDC SAML app's **metadata URL** to create a SAML identity provider
- **IDC SAML app** needs the Cognito User Pool's **ACS URL** and **audience URI** for its service provider configuration

The ACS URL and audience depend on the User Pool ID (which is randomly generated at creation time), so neither resource can be fully configured without the other existing first.

```
ACS URL:  https://{cognito-domain}.auth.{region}.amazoncognito.com/saml2/idpresponse
Audience: urn:amazon:cognito:sp:{user-pool-id}
```

The solution is to break the circular dependency by creating the IDC app in two phases:

1. Create an empty IDC SAML app (get the **metadata URL** immediately)
2. Deploy Cognito with the metadata URL (creates the User Pool, get the **pool ID** and **OAuth domain**)
3. Update the IDC SAML app with the Cognito ACS URL and audience
4. Create the Cognito User Pool Client with the SAML provider enabled

## Deployment Script Approach

The simplest way to integrate `idc-saml` into your deployment is a shell script that orchestrates the two phases. The idea is to wrap your existing deployment tool (CDK, Terraform, SAM, etc.) with the IDC app creation and configuration steps.

### Phase 1: Create the IDC app and pass metadata URL to your deployment

```bash
#!/bin/bash
set -euo pipefail

# Create the IDC SAML app (empty — no SP config yet)
INSTANCE_ID=$(aws idc-saml create-application-instance \
  --template-id tpl-50e590700beb5208 \
  --name "$(uuidgen)" \
  --query 'applicationInstance.instanceId' --output text)

aws idc-saml update-application-instance-display-data \
  --instance-id "$INSTANCE_ID" \
  --display-name "My App" \
  --description "My application"

METADATA_URL=$(aws idc-saml get-application-instance \
  --instance-id "$INSTANCE_ID" \
  --query 'applicationInstance.identityProviderConfig.metadataUrl' --output text)

# Pass the metadata URL into your deployment tool
# CDK:
cdk deploy --context samlMetadataUrl="$METADATA_URL"

# Terraform:
# terraform apply -var="saml_metadata_url=$METADATA_URL"

# SAM:
# sam deploy --parameter-overrides SamlMetadataUrl="$METADATA_URL"

# CloudFormation:
# aws cloudformation deploy ... --parameter-overrides SamlMetadataUrl="$METADATA_URL"
```

Your infrastructure code receives the metadata URL as a parameter and uses it to create the Cognito SAML identity provider. After deployment, it outputs the User Pool ID and OAuth domain.

### Phase 2: Update the IDC app with Cognito outputs

```bash
# Extract outputs from your deployment
# CDK:
USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name MyStack \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)
OAUTH_DOMAIN=$(aws cloudformation describe-stacks --stack-name MyStack \
  --query 'Stacks[0].Outputs[?OutputKey==`OAuthDomain`].OutputValue' --output text)
COGNITO_REGION="us-east-1"

# Terraform:
# USER_POOL_ID=$(terraform output -raw user_pool_id)
# OAUTH_DOMAIN=$(terraform output -raw oauth_domain)

ACS_URL="https://${OAUTH_DOMAIN}.auth.${COGNITO_REGION}.amazoncognito.com/saml2/idpresponse"
AUDIENCE="urn:amazon:cognito:sp:${USER_POOL_ID}"

# Configure the IDC app with Cognito's ACS URL and audience
aws idc-saml update-application-instance-service-provider-configuration \
  --instance-id "$INSTANCE_ID" \
  --service-provider-config "{
    \"audience\": \"${AUDIENCE}\",
    \"consumers\": [{
      \"location\": \"${ACS_URL}\",
      \"binding\": \"urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST\",
      \"defaultValue\": false
    }],
    \"requireRequestSignature\": false
  }"

aws idc-saml update-application-instance-response-configuration \
  --instance-id "$INSTANCE_ID" \
  --response-config '{
    "subject": {"source": ["${user:email}"]},
    "properties": {"Email": {"source": ["${user:email}"]}},
    "ttl": "PT1H"
  }'

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

aws idc-saml update-application-instance-status \
  --instance-id "$INSTANCE_ID" --status ENABLED

echo "Done. SAML federation is fully configured."
```

On subsequent deployments you can skip the creation step and just update the existing app if Cognito outputs change. Store the `INSTANCE_ID` somewhere persistent (e.g. SSM Parameter Store, a file, or your deployment tool's state).

## Automated Deployment with CDK

The key insight is using **two CloudFormation Custom Resources** backed by Lambda functions. The first creates an empty IDC SAML app and returns the metadata URL. Cognito is then configured with that URL. The second Custom Resource updates the IDC app with the now-known Cognito ACS URL and audience.

### CDK Stack (simplified)

```typescript
// 1. Lambda that manages IDC SAML apps
const idcAppFunction = new lambda.Function(this, 'IdcAppFunction', {
  code: lambda.Code.fromAsset('lambda/idc-app'),
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'index.handler',
  timeout: cdk.Duration.minutes(5),
});

idcAppFunction.addToRolePolicy(new iam.PolicyStatement({
  actions: ['sso:*', 'identitystore:ListGroups'],
  resources: ['*'],
}));

const idcAppProvider = new cr.Provider(this, 'IdcAppProvider', {
  onEventHandler: idcAppFunction,
});

// 2. Phase 1: Create empty IDC SAML app (gets metadata URL)
//    idcRegion is optional — defaults to the Lambda's region.
//    Set it explicitly if IDC lives in a different region than this stack.
const idcApp = new cdk.CustomResource(this, 'IdcApp', {
  serviceToken: idcAppProvider.serviceToken,
  properties: {
    phase: 'create',
    appName: 'My App',
    appDescription: 'My application',
  },
});

const samlMetadataUrl = idcApp.getAttString('metadataUrl');

// 3. Create Cognito User Pool
const userPool = new cognito.UserPool(this, 'UserPool', {
  signInAliases: { email: true },
  selfSignUpEnabled: false,
});

const oauthDomainPrefix = `myapp-${this.account}`;
userPool.addDomain('Domain', {
  cognitoDomain: { domainPrefix: oauthDomainPrefix },
});

// 4. Create SAML Identity Provider in Cognito using the metadata URL
const samlProvider = new cognito.UserPoolIdentityProviderSaml(this, 'SamlProvider', {
  userPool,
  name: 'IDC',
  metadata: cognito.UserPoolIdentityProviderSamlMetadata.url(samlMetadataUrl),
  attributeMapping: {
    email: cognito.ProviderAttribute.other('Email'),
  },
});

// 5. Phase 2: Update IDC app with Cognito ACS URL and audience
//    The Lambda assumes Cognito is in the same region it runs in.
//    If IDC is in a different region, set idcRegion explicitly.
const idcAppConfig = new cdk.CustomResource(this, 'IdcAppConfig', {
  serviceToken: idcAppProvider.serviceToken,
  properties: {
    phase: 'configure',
    instanceId: idcApp.getAttString('instanceId'),
    userPoolId: userPool.userPoolId,
    oauthDomain: oauthDomainPrefix,
    accessGroup: 'AppUsers',
  },
});

// 6. Create the User Pool Client with both Cognito and SAML login
const client = new cognito.UserPoolClient(this, 'Client', {
  userPool,
  supportedIdentityProviders: [
    cognito.UserPoolClientIdentityProvider.COGNITO,
    cognito.UserPoolClientIdentityProvider.custom(samlProvider.providerName),
  ],
  oAuth: {
    flows: { authorizationCodeGrant: true },
    scopes: [cognito.OAuthScope.EMAIL, cognito.OAuthScope.OPENID, cognito.OAuthScope.PROFILE],
    callbackUrls: ['https://myapp.example.com/'],
  },
});
```

### Custom Resource Lambda

A complete, ready-to-use Lambda is available at [examples/cr_idc_saml_cognito.py](examples/cr_idc_saml_cognito.py). It is a single file with no external dependencies (uses only `boto3`/`botocore` from the Lambda runtime). It handles Create, Update, and Delete lifecycle events, with idempotency (reuses existing apps by name), group assignment, and full cleanup on delete.

## SAML Attribute Mapping

Cognito expects specific attributes in the SAML response. The IDC app must be configured to send the user's email:

| SAML Attribute | Source | Purpose |
|---|---|---|
| Subject (NameID) | `${user:email}` | Unique user identifier |
| `Email` | `${user:email}` | Mapped to Cognito `email` attribute |

The attribute name must be `Email` (capital E, singular). Using `emails` (lowercase, plural) causes Cognito to reject the SAML response.

The NameID format should be `urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress`.

## What This Eliminates

Without `idc-saml`, the deployment of this architecture requires:

1. Deploy CDK stack (creates Cognito, but no SAML provider yet)
2. **Manual step**: Log into IAM Identity Center console
3. **Manual step**: Create a Custom SAML 2.0 application
4. **Manual step**: Configure ACS URL and audience
5. **Manual step**: Set up attribute mappings
6. **Manual step**: Enable the application and assign groups
7. **Manual step**: Copy the SAML metadata URL
8. Redeploy CDK stack with the metadata URL as a parameter

With `idc-saml`, it's a single `cdk deploy` — the Custom Resource Lambda handles steps 2-7 automatically and passes the metadata URL back to CloudFormation.
