import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "next-themes";
import App from "./App.tsx";
import "./index.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Missing #root element");
}

createRoot(root).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
