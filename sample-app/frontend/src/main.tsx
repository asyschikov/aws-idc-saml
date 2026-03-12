import React from "react";
import ReactDOM from "react-dom/client";
import { configureAmplify } from "./amplify-config";
import App from "./App";

configureAmplify();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
