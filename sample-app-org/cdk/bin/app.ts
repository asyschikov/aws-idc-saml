#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import * as fs from "fs";
import * as path from "path";
import { config } from "../lib/config";
import { IdcStack } from "../lib/idc-stack";
import { AppStack } from "../lib/app-stack";

const app = new cdk.App();
const group = app.node.tryGetContext("group");

const outputsDir = path.resolve(__dirname, "../..");

function readOutputs(file: string): Record<string, string> | undefined {
  const filePath = path.join(outputsDir, file);
  if (!fs.existsSync(filePath)) return undefined;
  const data = JSON.parse(fs.readFileSync(filePath, "utf-8"));
  const stackName = Object.keys(data)[0];
  return data[stackName];
}

if (group === "idc") {
  const appOutputs = readOutputs("app-outputs.json");

  new IdcStack(app, "IdcSamlOrgIdc", {
    env: {
      account: config.idcAccount,
      region: config.idcRegion,
    },
    idcRegion: config.idcRegion,
    appAccount: config.appAccount,
    appRegion: config.appRegion,
    appName: config.appName,
    appDescription: config.appDescription,
    enableGroupClaims: config.enableGroupClaims,
    accessGroups: config.accessGroups,
    // Phase 2: available once app-outputs.json exists
    userPoolId: appOutputs?.UserPoolId,
    oauthDomain: appOutputs?.OAuthDomainPrefix,
  });
} else if (group === "app") {
  const idcOutputs = readOutputs("idc-outputs.json");

  if (!idcOutputs?.MetadataUrl) {
    throw new Error(
      "idc-outputs.json not found or missing MetadataUrl. Run './deploy.sh idc' first."
    );
  }

  new AppStack(app, "IdcSamlOrgApp", {
    env: {
      account: config.appAccount,
      region: config.appRegion,
    },
    idcRegion: config.idcRegion,
    metadataUrl: idcOutputs.MetadataUrl,
    crossAccountRoleArn: idcOutputs.CrossAccountRoleArn,
  });
} else {
  throw new Error(
    'Specify -c group=idc or -c group=app (e.g. npx cdk synth -c group=idc)'
  );
}
