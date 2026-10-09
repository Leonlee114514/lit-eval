import { lazy, Suspense, useEffect } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { BarChart3, BookOpen, FileUp, Moon, Sun } from "lucide-react";
import { useThemeStore } from "./store/useThemeStore";

// 路由级代码分割：4 个页面独立 chunk，首包只含外壳
const ProjectsPage = lazy(() => import("./pages/ProjectsPage"));
const InputPage = lazy(() => import("./pages/InputPage"));
const ResultsPage = lazy(() => import("./pages/ResultsPage"));
const PaperDetailPage = lazy(() => import("./pages/PaperDetailPage"));

function PageFallback() {
  return (
    <div aria-busy="true">
      <div className="skeleton" style={{ height: 26, width: 200, marginBottom: 16 }} />
      <div className="skeleton" style={{ height: 220 }} />
    </div>
  );
}

export default function App() {
  const { theme, toggle } = useThemeStore();

  // 主题变化同步到 <html data-theme>（首帧值已在 main.tsx 预置）
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const dark = theme === "dark";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <h1 className="brand">
          <BookOpen size={17} strokeWidth={1.7} aria-hidden="true" />
          <span>文献评估</span>
        </h1>
        <nav>
          <NavLink to="/"><BookOpen size={15} strokeWidth={1.7} aria-hidden="true" /> 项目</NavLink>
          <NavLink to="/input"><FileUp size={15} strokeWidth={1.7} aria-hidden="true" /> 导入文献</NavLink>
          <NavLink to="/results"><BarChart3 size={15} strokeWidth={1.7} aria-hidden="true" /> 评估结果</NavLink>
        </nav>
      </aside>
      <main className="main">
        <div className="topbar">
          <span className="topbar-label">LitEval · Glass Workspace</span>
          <button
            className="theme-toggle"
            onClick={toggle}
            title={dark ? "切换到浅色模式" : "切换到暗色模式"}
            aria-label={dark ? "切换到浅色模式" : "切换到暗色模式"}
          >
            <span className="theme-icon-wrap" aria-hidden="true">
              <Sun size={15} strokeWidth={1.7} className={`theme-icon ${dark ? "is-hidden" : ""}`} />
              <Moon size={15} strokeWidth={1.7} className={`theme-icon ${dark ? "" : "is-hidden"}`} />
            </span>
            <span>{dark ? "浅色" : "暗色"}</span>
          </button>
        </div>
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/" element={<ProjectsPage />} />
            <Route path="/input" element={<InputPage />} />
            <Route path="/results" element={<ResultsPage />} />
            <Route path="/papers/:paperId" element={<PaperDetailPage />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}
