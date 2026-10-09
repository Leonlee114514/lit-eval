import { useProjectStore } from "../store/useProjectStore";
import type { Project } from "../api/types";

export default function ProjectSelector({ projects }: { projects: Project[] }) {
  const { projectId, setProjectId } = useProjectStore();
  return (
    <select
      value={projectId ?? ""}
      onChange={e => setProjectId(e.target.value || null)}
      style={{ width: "100%", padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border)" }}
    >
      <option value="">请选择项目…</option>
      {projects.map(p => (
        <option key={p.id} value={p.id}>{p.name}</option>
      ))}
    </select>
  );
}
