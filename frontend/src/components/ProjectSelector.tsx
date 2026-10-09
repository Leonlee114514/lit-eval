import { useProjectStore } from "../store/useProjectStore";
import type { Project } from "../api/types";

export default function ProjectSelector({ projects }: { projects: Project[] }) {
  const { projectId, setProjectId } = useProjectStore();
  return (
    <select
      value={projectId ?? ""}
      onChange={e => setProjectId(e.target.value || null)}
      /* 无可见 label（靠上下文"目标项目"说明），所以给它一个可访问名称 ——
         否则屏幕阅读器只会念"组合框"，axe 也会报 select-name */
      aria-label="选择项目"
      style={{ width: "100%", padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border)" }}
    >
      <option value="">请选择项目…</option>
      {projects.map(p => (
        <option key={p.id} value={p.id}>{p.name}</option>
      ))}
    </select>
  );
}
