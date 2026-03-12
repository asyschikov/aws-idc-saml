import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import { IdcSamlApp } from "./idc-saml-app";
import { IdcGroupAssignment } from "./idc-group-assignment";

export interface IdcStackProps extends cdk.StackProps {
  idcRegion: string;
  appAccount: string;
  appRegion: string;
  appName: string;
  appDescription: string;
  enableGroupClaims: boolean;
  accessGroups: string[];
  /** Provided in Phase 2 (from app-outputs.json) */
  userPoolId?: string;
  /** Provided in Phase 2 (from app-outputs.json) */
  oauthDomain?: string;
}

export class IdcStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: IdcStackProps) {
    super(scope, id, props);

    // ---------- IDC SAML App (Phase 1 always, Phase 2 conditional) ----------
    const idcApp = new IdcSamlApp(this, "IdcApp", {
      idcRegion: props.idcRegion,
      appName: props.appName,
      appDescription: props.appDescription,
      userPoolId: props.userPoolId,
      oauthDomain: props.oauthDomain,
      cognitoRegion: props.appRegion,
    });

    // ---------- Outputs ----------
    new cdk.CfnOutput(this, "MetadataUrl", {
      value: idcApp.metadataUrl,
    });
    new cdk.CfnOutput(this, "ApplicationArn", {
      value: idcApp.applicationArn,
    });

    // ---------- Cross-account IAM role for pre-token Lambda ----------
    if (props.enableGroupClaims) {
      const crossAccountRole = new iam.Role(this, "PreTokenRole", {
        roleName: "IdcSamlOrgPreTokenRole",
        assumedBy: new iam.AccountPrincipal(props.appAccount),
      });

      crossAccountRole.addToPolicy(
        new iam.PolicyStatement({
          actions: [
            "sso:ListInstances",
            "identitystore:ListUsers",
            "identitystore:ListGroupMembershipsForMember",
            "identitystore:DescribeGroup",
          ],
          resources: ["*"],
        })
      );

      new cdk.CfnOutput(this, "CrossAccountRoleArn", {
        value: crossAccountRole.roleArn,
      });

      // ---------- Group assignments (Phase 2 only) ----------
      if (idcApp.configResource) {
        for (const groupName of props.accessGroups) {
          const sanitized = groupName.replace(/[^a-zA-Z0-9]/g, "");
          const assignment = new IdcGroupAssignment(
            this,
            `GroupAssign${sanitized}`,
            {
              idcRegion: props.idcRegion,
              applicationArn: idcApp.applicationArn,
              groupName,
            }
          );
          assignment.node.addDependency(idcApp.configResource);
        }
      }
    }
  }
}
