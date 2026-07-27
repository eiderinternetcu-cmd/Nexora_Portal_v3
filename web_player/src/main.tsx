import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyBrand, brand } from "./brand";
import "./styles/app.css";

// Antes del primer render: fija data-brand, <title>, theme-color y favicon.
applyBrand(brand);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
