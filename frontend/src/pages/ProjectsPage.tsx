import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Project } from "../api/types";
import { useProjectStore } from "../store/useProjectStore";
import { usePageTitle } from "../hooks/usePageTitle";
import { FolderOpen, Pencil, Plus, Trash2 } from "lucide-react";

export default function ProjectsPage() {
  usePageTitle("项目 · 文献评估");
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { projectId, setProjectId } = useProjectStore();
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [keywords, setKeywords] = useState("");

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
  });

  const createMut = useMutation({
    mutationFn: () =>
      api.createProject({
        name,
        research_topic: topic,
        keywords: keywords.split(/[，,、\s]+/).filter(Boolean),
      }),
    onSuccess: p => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setProjectId(p.id);
      setShowForm(false);
      setName(""); setTopic(""); setKeywords("");
      navigate("/input");
    },
  });

  const editMut = useMutation({
    mutationFn: (id: string) =>
      api.updateProject(id, {
        name,
        research_topic: topic,
        keywords: keywords.split(/[，,、\s]+/).filter(Boolean),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setEditing(null);
      setShowForm(false);
      setName(""); setTopic(""); setKeywords("");
    },
  });

  const startEdit = (p: Project) => {
    setEditing(p);
    setName(p.name);
    setTopic(p.research_topic || "");
    setKeywords((p.keywords || []).join("、"));
    setShowForm(true);
  };

  const delMut = useMutation({
    mutationFn: (id: string) => api.deleteProject(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setProjectId(null);
    },
  });

  return (
    <div className="page-enter">
      <div className="toolbar">
        <h2 className="page-title" style={{ marginBottom: 0 }}>研究项目</h2>
        <button onClick={() => setShowForm(v => !v)}>{showForm ? "收起" : <><Plus size={15} strokeWidth={1.8} aria-hidden="true" /> 新建项目</>}</button>
      </div>

      {showForm && (
        <div className="card">
          <div className="grid grid-2" style={{ marginBottom: 12 }}>
            <div>
              <label className="small" htmlFor="project-name">项目名称 *</label>
              <input id="project-name" value={name} onChange={e => setName(e.target.value)} placeholder="如：水性聚氨酯涂层研究" />
            </div>
            <div>
              <label className="small" htmlFor="project-topic">研究主题（相关性计算锚点）</label>
              <input id="project-topic" value={topic} onChange={e => setTopic(e.target.value)} placeholder="如：waterborne polyurethane crosslinking" />
            </div>
          </div>
          <label className="small" htmlFor="project-keywords">关键词（逗号分隔）</label>
          <input id="project-keywords" value={keywords} onChange={e => setKeywords(e.target.value)} placeholder="涂层, 交联, mechanical property" />
          <div style={{ marginTop: 12 }}>
            <button
              onClick={() =>
                editing ? editMut.mutate(editing.id) : createMut.mutate()
              }
              disabled={!name.trim() || createMut.isPending || editMut.isPending}
            >
              {createMut.isPending || editMut.isPending
                ? "保存中…"
                : editing
                  ? "保存修改"
                  : "创建并去导入文献"}
            </button>
            {editing && (
              <button
                style={{ marginLeft: 8 }}
                onClick={() => {
                  setEditing(null);
                  setShowForm(false);
                  setName(""); setTopic(""); setKeywords("");
                }}
              >
                取消
              </button>
            )}
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="muted">加载中…</div>
      ) : projects.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "48px 16px" }}>
          <div className="empty-icon"><FolderOpen size={42} strokeWidth={1.4} aria-hidden="true" /></div>
          <h3 style={{ margin: "12px 0 6px" }}>还没有项目</h3>
          <p className="muted" style={{ margin: "0 0 16px" }}>
            创建项目后即可导入文献、运行评估。建议一个研究主题对应一个项目。
          </p>
          <button onClick={() => setShowForm(true)}><Plus size={15} strokeWidth={1.8} aria-hidden="true" /> 新建第一个项目</button>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table>
            <thead>
              <tr><th>名称</th><th>研究主题</th><th>关键词</th><th>文献数</th><th><span className="sr-only">操作</span></th></tr>
            </thead>
            <tbody>
              {projects.map(p => (
                <tr
                  key={p.id}
                  className="clickable"
                  onClick={() => setProjectId(p.id)}
                  style={{ background: p.id === projectId ? "var(--accent-tint)" : undefined }}
                >
                  <td><b>{p.name}</b></td>
                  <td className="muted">{p.research_topic || "—"}</td>
                  <td className="muted">{(p.keywords || []).join("、") || "—"}</td>
                  <td>{p.paper_count}</td>
                  <td>
                    <button className="small secondary" onClick={e => { e.stopPropagation(); startEdit(p); }}>
                      <Pencil size={13} strokeWidth={1.7} aria-hidden="true" /> 编辑
                    </button>
                    <button className="danger small" style={{ marginLeft: 8 }} onClick={e => {
                      e.stopPropagation();
                      if (window.confirm(`确定删除项目「${p.name}」？将同时删除该项目下的 ${p.paper_count} 篇文献与全部评估记录，此操作不可恢复。`)) delMut.mutate(p.id);
                    }}>
                      <Trash2 size={13} strokeWidth={1.7} aria-hidden="true" /> 删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
