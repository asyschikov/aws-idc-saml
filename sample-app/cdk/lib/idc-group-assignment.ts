import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cr from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";

export interface IdcGroupAssignmentProps {
  idcRegion?: string;
  instanceArn?: string;
  applicationArn: string;
  groupName: string;
}

export class IdcGroupAssignment extends Construct {
  constructor(scope: Construct, id: string, props: IdcGroupAssignmentProps) {
    super(scope, id);

    const provider = IdcGroupAssignment.getOrCreateProvider(this);

    new cdk.CustomResource(this, "Resource", {
      serviceToken: provider.serviceToken,
      properties: {
        idcRegion: props.idcRegion,
        instanceArn: props.instanceArn,
        applicationArn: props.applicationArn,
        groupName: props.groupName,
      },
    });
  }

  private static getOrCreateProvider(scope: Construct): cr.Provider {
    const stack = cdk.Stack.of(scope);
    const providerId = "IdcGroupAssignmentProvider";
    const existing = stack.node.tryFindChild(providerId) as cr.Provider;
    if (existing) return existing;

    const fn = new lambda.Function(stack, "IdcGroupAssignmentFunction", {
      code: lambda.Code.fromAsset("../lambda/idc-group-assignment"),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      timeout: cdk.Duration.minutes(2),
    });

    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "sso:CreateApplicationAssignment",
          "sso:DeleteApplicationAssignment",
          "sso:ListInstances",
        ],
        resources: ["*"],
      })
    );
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["identitystore:ListGroups"],
        resources: ["*"],
      })
    );

    return new cr.Provider(stack, providerId, {
      onEventHandler: fn,
    });
  }
}
