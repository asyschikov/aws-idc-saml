import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import { Construct } from "constructs";

export interface AppStackProps extends cdk.StackProps {
  idcRegion: string;
  metadataUrl: string;
  crossAccountRoleArn?: string;
}

export class AppStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AppStackProps) {
    super(scope, id, props);

    // ---------- S3 bucket for frontend ----------
    const bucket = new s3.Bucket(this, "FrontendBucket", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    // ---------- Pre-Token Generation Lambda (only with group claims) ----------
    let preTokenFunction: lambda.Function | undefined;
    if (props.crossAccountRoleArn) {
      preTokenFunction = new lambda.Function(this, "PreTokenFunction", {
        code: lambda.Code.fromAsset("../lambda/pre-token"),
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: "index.handler",
        timeout: cdk.Duration.seconds(10),
        environment: {
          IDC_REGION: props.idcRegion,
          CROSS_ACCOUNT_ROLE_ARN: props.crossAccountRoleArn,
        },
      });

      preTokenFunction.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["sts:AssumeRole"],
          resources: [props.crossAccountRoleArn],
        })
      );
    }

    // ---------- Cognito User Pool + domain ----------
    const oauthDomainPrefix = `idc-saml-org-${cdk.Aws.ACCOUNT_ID}`;

    const userPool = new cognito.UserPool(this, "UserPool", {
      userPoolName: "idc-saml-org",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      signInAliases: { email: true },
      ...(preTokenFunction && {
        lambdaTriggers: { preTokenGeneration: preTokenFunction },
      }),
    });

    const domain = userPool.addDomain("CognitoDomain", {
      cognitoDomain: { domainPrefix: oauthDomainPrefix },
    });

    // ---------- SAML Identity Provider ----------
    const samlProvider = new cognito.UserPoolIdentityProviderSaml(
      this,
      "IdcSamlProvider",
      {
        userPool,
        name: "IDC",
        metadata: cognito.UserPoolIdentityProviderSamlMetadata.url(
          props.metadataUrl
        ),
        attributeMapping: {
          email: cognito.ProviderAttribute.other("Email"),
        },
        identifiers: ["IDC"],
      }
    );

    // ---------- Whoami Lambda ----------
    const whoamiFunction = new lambda.Function(this, "WhoamiFunction", {
      code: lambda.Code.fromAsset("../lambda/whoami"),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      timeout: cdk.Duration.seconds(10),
    });

    // ---------- REST API with Cognito authorizer ----------
    const api = new apigateway.RestApi(this, "SampleApi", {
      restApiName: "idc-saml-org-api",
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: apigateway.Cors.DEFAULT_HEADERS,
      },
    });

    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(
      this,
      "CognitoAuthorizer",
      {
        cognitoUserPools: [userPool],
      }
    );

    const apiResource = api.root.addResource("api");
    const whoamiResource = apiResource.addResource("whoami");
    whoamiResource.addMethod(
      "GET",
      new apigateway.LambdaIntegration(whoamiFunction),
      {
        authorizer,
        authorizationType: apigateway.AuthorizationType.COGNITO,
      }
    );

    // ---------- CloudFront ----------
    const s3Origin = origins.S3BucketOrigin.withOriginAccessControl(bucket);

    const apiOrigin = new origins.RestApiOrigin(api);

    const distribution = new cloudfront.Distribution(this, "Distribution", {
      defaultBehavior: {
        origin: s3Origin,
        viewerProtocolPolicy:
          cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      additionalBehaviors: {
        "/api/*": {
          origin: apiOrigin,
          viewerProtocolPolicy:
            cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy:
            cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },
      defaultRootObject: "index.html",
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
        },
      ],
    });

    // ---------- User Pool Client (created last — uses CloudFront domain) ----------
    const cfDomain = `https://${distribution.distributionDomainName}`;

    const userPoolClient = userPool.addClient("AppClient", {
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.PROFILE,
        ],
        callbackUrls: [`${cfDomain}/`, "http://localhost:5173/"],
        logoutUrls: [`${cfDomain}/`, "http://localhost:5173/"],
      },
      supportedIdentityProviders: [
        cognito.UserPoolClientIdentityProvider.custom("IDC"),
      ],
    });
    userPoolClient.node.addDependency(samlProvider);

    // ---------- Outputs ----------
    new cdk.CfnOutput(this, "CloudFrontUrl", {
      value: cfDomain,
    });
    new cdk.CfnOutput(this, "UserPoolId", {
      value: userPool.userPoolId,
    });
    new cdk.CfnOutput(this, "UserPoolClientId", {
      value: userPoolClient.userPoolClientId,
    });
    new cdk.CfnOutput(this, "CognitoDomain", {
      value: `${oauthDomainPrefix}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
    });
    new cdk.CfnOutput(this, "OAuthDomainPrefix", {
      value: oauthDomainPrefix,
    });
    new cdk.CfnOutput(this, "BucketName", {
      value: bucket.bucketName,
    });
    new cdk.CfnOutput(this, "DistributionId", {
      value: distribution.distributionId,
    });
  }
}
