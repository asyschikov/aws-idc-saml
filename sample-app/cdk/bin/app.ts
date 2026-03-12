#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { SampleAppStack } from "../lib/sample-app-stack";

const app = new cdk.App();
new SampleAppStack(app, "IdcSamlSampleApp", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});
