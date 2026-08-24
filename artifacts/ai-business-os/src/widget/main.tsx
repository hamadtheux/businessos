import { createRoot } from "react-dom/client";
import { StrictMode } from "react";
import { WidgetApp } from "./widget-app.tsx";
import "./widget.css";

createRoot(document.getElementById("widget-root")!).render(<StrictMode><WidgetApp /></StrictMode>);
