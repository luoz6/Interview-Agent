import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/base.css";
import "./styles/components/app-shell.css";
import "./styles/components/navigation.css";
import "./styles/components/dialog.css";
import "./styles/components/async-state.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
