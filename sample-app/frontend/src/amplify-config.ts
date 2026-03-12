import { Amplify } from "aws-amplify";
import { config } from "./config";

export const configureAmplify = () => {
  const redirectUrl = `${window.location.origin}/`;

  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.userPoolClientId,
        loginWith: {
          oauth: {
            domain: config.cognitoDomain,
            scopes: ["openid", "email", "profile"],
            redirectSignIn: [redirectUrl],
            redirectSignOut: [redirectUrl],
            responseType: "code",
          },
        },
      },
    },
  });
};
