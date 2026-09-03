import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./tokens.css"
import "./app.css"
import App from "./App"

const container = document.getElementById("root")
if (!container) throw new Error("缺少 #root 挂载点")
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
