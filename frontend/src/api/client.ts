import axios from "axios";
import type {
  ApiEnvelope,
  CitationNetworkData,
  ProjectNetworkData,
  EvaluationDetail,
  Paper,
  Project,
  Report,
  SearchResponse,
  TaskStatus,
} from "./types";

const http = axios.create({ baseURL: "/api", timeout: 120_000 });

export const api = {
  // meta
  rules: () => http.get<ApiEnvelope<unknown>>("/meta/rules").then(r => r.data.data),

  // projects
  listProjects: () => http.get<ApiEnvelope<Project[]>>("/projects").then(r => r.data.data),
  createProject: (body: { name: string; description?: string; research_topic?: string; keywords?: string[] }) =>
    http.post<ApiEnvelope<Project>>("/projects", body).then(r => r.data.data),
  deleteProject: (id: string) => http.delete(`/projects/${id}`).then(r => r.data),
  updateProject: (id: string, body: { name?: string; description?: string; research_topic?: string; keywords?: string[] }) =>
    http.patch<ApiEnvelope<Project>>(`/projects/${id}`, body).then(r => r.data.data),

  // papers
  importPapers: (projectId: string, inputs: Array<{ doi?: string; title?: string; pmid?: string }>) =>
    http
      .post<ApiEnvelope<{ papers: Paper[]; skipped: string[]; errors?: string[] }>>(`/projects/${projectId}/papers`, {
        inputs,
      })
      .then(r => r.data.data),
  searchWorks: (q: string, opts?: { project_id?: string; year_from?: number; year_to?: number; per_page?: number; work_type?: string; query_mode?: "raw" | "expanded" }) =>
    http
      .get<ApiEnvelope<SearchResponse>>("/search/works", { params: { q, ...opts } })
      .then(r => r.data.data),
  listPapers: (projectId: string) =>
    http.get<ApiEnvelope<Paper[]>>(`/projects/${projectId}/papers`).then(r => r.data.data),
  getPaper: (paperId: string) => http.get<ApiEnvelope<Paper>>(`/papers/${paperId}`).then(r => r.data.data),
  patchPaper: (paperId: string, body: Record<string, unknown>) =>
    http.patch<ApiEnvelope<Paper>>(`/papers/${paperId}`, body).then(r => r.data.data),
  evaluatePaper: (paperId: string) => http.post(`/papers/${paperId}/evaluate`).then(r => r.data),
  uploadFulltext: (paperId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.post(`/papers/${paperId}/fulltext`, form).then(r => r.data);
  },
  evaluateAll: (projectId: string, llm = true) =>
    http
      .post<ApiEnvelope<{ task_id: string }>>(`/projects/${projectId}/evaluate-all`, { llm })
      .then(r => r.data.data),
  evaluateSelected: (projectId: string, paperIds: string[], llm = true) =>
    http
      .post<ApiEnvelope<{ task_id: string }>>(`/projects/${projectId}/evaluate-selected`, { paper_ids: paperIds, llm })
      .then(r => r.data.data),
  downloadPdfs: (projectId: string, paperIds: string[]) =>
    http
      .post<ApiEnvelope<{ task_id: string }>>(`/projects/${projectId}/download-pdfs`, { paper_ids: paperIds })
      .then(r => r.data.data),
  pmidLookup: (projectId: string, pmids: string[]) =>
    http
      .post<ApiEnvelope<Record<string, string>>>(`/projects/${projectId}/pmid-lookup`, { pmids })
      .then(r => r.data.data),
  getEvaluation: (paperId: string) =>
    http.get<ApiEnvelope<EvaluationDetail | null>>(`/papers/${paperId}/evaluation`).then(r => r.data.data),

  // reports
  getReport: (paperId: string) => http.get<ApiEnvelope<Report>>(`/papers/${paperId}/report`).then(r => r.data.data),
  getCitationGraph: (paperId: string) =>
    http.get<ApiEnvelope<unknown>>(`/papers/${paperId}/citation-graph`).then(r => r.data.data),
  getCitationNetwork: (paperId: string) =>
    http.get<ApiEnvelope<CitationNetworkData>>(`/papers/${paperId}/citation-network`).then(r => r.data.data),
  getProjectCitationNetwork: (projectId: string) =>
    http.get<ApiEnvelope<ProjectNetworkData>>(`/projects/${projectId}/citation-network`).then(r => r.data.data),
  exportMarkdown: async (paperId: string) => {
    const resp = await http.get(`/papers/${paperId}/report/export?format=md`, { responseType: "blob" });
    const url = URL.createObjectURL(resp.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `report_${paperId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  },

  // tasks
  taskStatus: (taskId: string) => http.get<ApiEnvelope<TaskStatus>>(`/tasks/${taskId}`).then(r => r.data.data),
};

export default http;
