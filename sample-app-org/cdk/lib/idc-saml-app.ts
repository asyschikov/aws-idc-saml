import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cr from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";

export interface IdcSamlAppProps {
  idcRegion?: string;
  instanceArn?: string;
  appName: string;
  appDescription?: string;
  userPoolId?: string;
  oauthDomain?: string;
  cognitoRegion?: string;
}

export class IdcSamlApp extends Construct {
  public readonly instanceId: string;
  public readonly metadataUrl: string;
  public readonly applicationArn: string;
  public readonly configResource: cdk.CustomResource | undefined;

  constructor(scope: Construct, id: string, props: IdcSamlAppProps) {
    super(scope, id);

    const fn = new lambda.Function(this, "Function", {
      code: lambda.Code.fromAsset("../lambda/idc-app"),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      timeout: cdk.Duration.minutes(5),
    });

    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["sso:*"],
        resources: ["*"],
      })
    );
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["identitystore:ListGroups"],
        resources: ["*"],
      })
    );

    const provider = new cr.Provider(this, "Provider", {
      onEventHandler: fn,
    });

    const crProps: Record<string, string | undefined> = {
      phase: "create",
      idcRegion: props.idcRegion,
      instanceArn: props.instanceArn,
      appName: props.appName,
      appDescription: props.appDescription ?? props.appName,
    };

    const app = new cdk.CustomResource(this, "App", {
      serviceToken: provider.serviceToken,
      properties: crProps,
    });

    this.instanceId = app.getAttString("instanceId");
    this.metadataUrl = app.getAttString("metadataUrl");
    this.applicationArn = app.getAttString("applicationArn");

    // Phase 2: configure SP settings (only when Cognito outputs are available)
    if (props.userPoolId && props.oauthDomain) {
      this.configResource = new cdk.CustomResource(this, "Config", {
        serviceToken: provider.serviceToken,
        properties: {
          phase: "configure",
          idcRegion: props.idcRegion,
          instanceArn: props.instanceArn,
          instanceId: this.instanceId,
          userPoolId: props.userPoolId,
          oauthDomain: props.oauthDomain,
          cognitoRegion: props.cognitoRegion,
        },
      });
    }
  }
}
