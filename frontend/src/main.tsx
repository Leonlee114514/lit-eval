import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./index.css";

// 首帧前应用持久化主题，避免暗色模式白屏闪烁
try {
  const saved = localStorage.getItem("lit-eval:theme-glass-v1");
  const theme = saved ? JSON.parse(saved).state?.theme : null;
  document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
} catch {
  /* 本地存储不可用/损坏时使用暗色玻璃默认主题 */
  document.documentElement.dataset.theme = "dark";
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
