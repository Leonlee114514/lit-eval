import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useProjectStore } from "../store/useProjectStore";
import ProjectSelector from "../components/ProjectSelector";
import ImportPanel, { type ImportMode } from "../components/ImportPanel";
import SearchPanel from "../components/SearchPanel";
import { usePageTitle } from "../hooks/usePageTitle";

const MODES: Array<{ key: ImportMode; label: string }> = [
  { key: "plain", label: "DOI / 标题" },
  { key: "csv", label: "CSV" },
  { key: "bibtex", label: "BibTeX" },
  { key: "pmid", label: "PMID" },
  { key: "search", label: "主题搜索" },
];

/**
 * 导入页：项目选择 + 导入方式 tab + 面板。
 *
 * 面板拆成 ImportPanel（粘贴/上传）与 SearchPanel（主题搜索）两个组件 ——
 * 原先 525 行里 18 个 useState 同时管两条互不相干的流程，任何一侧改动都要重读整页。
 *
 * 面板用 ``key`` 挂载（``key={mode}`` / ``key="search"``）：换 tab 即重建，面板内部状态
 * 随卸载清空。原实现靠 switchMode() 跨面板手工清空（setRaw/setFile/message/searchMsg），
 * 现在由 React 的生命周期承担，两个面板之间不再互相知道对方的存在。
 */
export default function InputPage() {
  usePageTitle("导入文献 · 文献评估");
  const { projectId } = useProjectStore();
  const [mode, setMode] = useState<ImportMode>("plain");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const { data: projects = [] } = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });
  const currentProject = projects.find(p => p.id === projectId);

  // 语义化 tab：方向键/Home/End 切换导入方式，焦点跟随（WAI-ARIA Tabs 模式）
  const onTablistKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const keys = MODES.map(m => m.key);
    let idx = keys.indexOf(mode);
    if (e.key === "ArrowRight") idx = (idx + 1) % keys.length;
    else if (e.key === "ArrowLeft") idx = (idx - 1 + keys.length) % keys.length;
    else if (e.key === "Home") idx = 0;
    else if (e.key === "End") idx = keys.length - 1;
    else return;
    e.preventDefault();
    setMode(keys[idx]);
    tabRefs.current[idx]?.focus();
  };

  return (
    <div className="page-enter">
      <h2 className="page-title">导入文献</h2>
      <div className="card">
        <div style={{ marginBottom: 12 }}>
          <label className="small">目标项目</label>
          <ProjectSelector projects={projects} />
        </div>

        <div
          role="tablist"
          aria-label="导入方式"
          onKeyDown={onTablistKeyDown}
          style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}
        >
          {MODES.map((m, i) => (
            <button
              key={m.key}
              ref={el => { tabRefs.current[i] = el; }}
              role="tab"
              id={`import-tab-${m.key}`}
              aria-selected={mode === m.key}
              aria-controls="import-tabpanel"
              tabIndex={mode === m.key ? 0 : -1}
              className={mode === m.key ? "" : "secondary"}
              onClick={() => setMode(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>

        <div id="import-tabpanel" role="tabpanel" aria-labelledby={`import-tab-${mode}`}>
          {mode === "search" ? (
            <SearchPanel key="search" initialQuery={currentProject?.research_topic ?? ""} />
          ) : (
            <ImportPanel key={mode} mode={mode} />
          )}
        </div>
      </div>
    </div>
  );
}
