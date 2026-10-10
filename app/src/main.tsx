import "katex/dist/katex.min.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { DocWindow } from "./components/DocWindow";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {location.hash.startsWith("#/doc") ? <DocWindow /> : <App />}
  </StrictMode>,
);
