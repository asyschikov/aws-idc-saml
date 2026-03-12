# sample-app-org — Cross-Account IDC SAML Sample

A cross-account variant of `sample-app/` for AWS Organizations environments where IAM Identity Center (IDC) runs in the management account and the application deploys to a member account.

## Architecture

```
IDC Account                          App Account
+--------------------------+         +----------------------------------+
| IDC SAML Application     |         | CloudFront → S3 (React SPA)     |
|   Phase 1: create app    |         |            → API GW → Lambda    |
|   Phase 2: configure SP  |         |                                  |
|                          |         | Cognito User Pool                |
| Group Assignments        |         |   SAML Provider (IDC metadata)   |
|   Admins → App           |         |   Pre-Token Lambda ──────────┐  |
|                          |         |                               │  |
| Cross-Account IAM Role ←─┼─────────┼── STS AssumeRole ────────────┘  |
|   (identitystore read)   |         |                                  |
+--------------------------+         +----------------------------------+
```

The Pre-Token Lambda in the app account assumes a cross-account IAM role in the IDC account to read Identity Store group memberships, then injects a `groups` claim into Cognito tokens.

Set `enableGroupClaims: false` in config to skip the cross-account role, pre-token Lambda, and group assignments (SSO login still works, just without group claims).

## Prerequisites

- Two AWS accounts with configured CLI profiles
- IAM Identity Center enabled in the management account
- Node.js 18+, Python 3.12+, AWS CDK CLI (`npm i -g aws-cdk`)
- Both accounts bootstrapped for CDK: `cdk bootstrap aws://ACCOUNT/REGION --profile PROFILE`

## Setup

1. Copy the config templates:

```bash
cp cdk/lib/config-template.ts cdk/lib/config.ts
cp env-template.sh env.sh
```

2. Edit `cdk/lib/config.ts` with your account IDs and regions:

```typescript
export const config = {
  idcAccount: "111111111111",    // Management account with IDC
  idcRegion: "eu-west-1",        // IDC home region
  appAccount: "222222222222",    // Member account for the app
  appRegion: "eu-central-1",     // App deployment region
  appName: "IDC SAML Sample Org",
  appDescription: "Cross-account IDC SAML Sample",
  enableGroupClaims: true,       // Set false to skip cross-account role + group claims
  accessGroups: ["Admins"],      // IDC groups to grant app access
};
```

3. Edit `env.sh` with your AWS CLI profile names:

```bash
IDC_PROFILE="mgmt-profile"
APP_PROFILE="app-profile"
```

## Deployment

### Automatic (recommended)

```bash
./deploy.sh all
```

This runs all 4 steps in order.

### Manual (step by step)

```bash
# 1. Phase 1: Create IDC SAML app skeleton
./deploy.sh idc

# 2. Deploy app stack (Cognito, CloudFront, API Gateway, Lambdas)
./deploy.sh app

# 3. Phase 2: Configure IDC SP settings + assign groups
./deploy.sh idc

# 4. Build and deploy frontend
./deploy.sh frontend
```

The circular dependency between IDC and Cognito is resolved by the 3-step CDK deployment:
- Step 1 creates the IDC app and outputs its SAML metadata URL to `idc-outputs.json`
- Step 2 reads that file and uses the metadata URL to create Cognito's SAML provider, outputs to `app-outputs.json`
- Step 3 reads `app-outputs.json` and feeds Cognito's user pool ID and OAuth domain back to configure IDC's service provider settings

## Cleanup

Destroy both stacks (order matters — app first, then IDC):

```bash
cd cdk

# Destroy app stack
npx cdk destroy IdcSamlOrgApp --profile APP_PROFILE -c group=app

# Destroy IDC stack
npx cdk destroy IdcSamlOrgIdc --profile IDC_PROFILE -c group=idc
```
