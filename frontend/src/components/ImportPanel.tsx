import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useProjectStore } from "../store/useProjectStore";
import {
  formatImportCount,
  parseBibtex,
  parseCsv,
  parsePlain,
  type ImportItem,
} from "../utils/importParsers";

/** 导入方式。`search` 由 SearchPanel 承担，本面板只在其余四种模式下挂载。 */
export type ImportMode = "plain" | "csv" | "bibtex" | "pmid" | "search";

const PLACEHOLDER: Record<ImportMode, string> = {
  plain: "10.1038/nature14539\nDeep learning (Nature 2015)\n10.1016/j.cell.2016.07.008",
  csv: "doi,title\n10.1038/nature14539,Deep learning\n10.1016/j.cell.2016.07.008,Phase transition",
  bibtex: "@article{lecun2015,\n  title = {Deep learning},\n  doi = {10.1038/nature14539},\n  journal = {Nature}\n}",
  pmid: "26017442\n28818941\n（每行一个 PMID，PubMed 直接导入）",
  search: "",
};

const LABEL: Record<string, string> = {
  plain: "粘贴 DOI 或论文标题（每行一个，自动识别）",
  csv: "粘贴 CSV（需含 doi 或 title 列，支持带引号字段）",
  bibtex: "粘贴 BibTeX 条目",
  pmid: "粘贴 PMID（PubMed 编号，每行一个）",
};

const HINT: Record<string, string> = {
  plain: "DOI 行走三源抓取，标题行走 OpenAlex 搜索。抓取失败的字段会自动标记，可在详情页人工补全。",
  pmid: "PMID 直接通过 NCBI PubMed 元数据导入；有 DOI 时自动叠加常规三源抓取，无 DOI 的 PMID 也能入库。",
  csv: "解析出的 DOI/标题走常规抓取；无法解析的条目自动跳过。",
  bibtex: "解析出的 DOI/标题走常规抓取；无法解析的条目自动跳过。",
};

/**
 * 粘贴 / 上传导入面板（DOI、标题、CSV、BibTeX、PMID）。
 *
 * 父组件用 ``key={mode}`` 挂载本面板：换 tab 即整棵重建，raw / file / message 随卸载清空 ——
 * 这正是原来 ``switchMode()`` 里手工 ``setRaw("")`` / ``setFile(null)`` / ``setMessage(null)``
 * 的等价写法（那种"切 tab 时顺手清空另一个面板的状态"是跨面板耦合，抽组件后由 React 承担）。
 */
export default function ImportPanel({ mode }: { mode: ImportMode }) {
  const { projectId } = useProjectStore();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [raw, setRaw] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const readFile = (f: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result ?? ""));
      r.onerror = () => reject(r.error);
      r.readAsText(f, "utf-8");
    });

  const resolveItems = async (): Promise<ImportItem[]> => {
    switch (mode) {
      case "plain":
        return parsePlain(raw);
      case "csv": {
        const text = file ? await readFile(file) : raw;
        return parseCsv(text);
      }
      case "bibtex": {
        const text = file ? await readFile(file) : raw;
        return parseBibtex(text);
      }
      case "pmid":
        // PMID 直接源：后端用 PubMed 元数据入库（无 DOI 的 PMID 也能导入）
        return raw
          .split(/\r?\n/)
          .map(l => l.trim())
          .filter(l => /^\d+$/.test(l))
          .map(pmid => ({ pmid }));
      case "search":
        return []; // 不会走到：父组件只在非 search 模式挂载本面板
    }
  };

  const importPapers = async () => {
    if (!projectId) { setMessage("请先在「项目」页选择或新建项目"); return; }
    setImporting(true);
    setMessage(null);
    try {
      const items = await resolveItems();
      if (!items.length) {
        setMessage("未解析出有效条目（PMID 模式：请确认每行都是 PubMed PMID）");
        return;
      }
      setMessage(`解析到 ${formatImportCount(items)}，开始导入…`);
      const res = await api.importPapers(projectId, items);
      const errText = res.errors?.length ? `；失败 ${res.errors.length} 条` : "";
      setMessage(`导入完成：${res.papers.length} 篇，跳过 ${res.skipped.length} 条${errText}`);
      qc.invalidateQueries({ queryKey: ["papers", projectId] });
      navigate("/results");
    } catch (e: any) {
      setMessage(`导入失败：${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setImporting(false);
    }
  };

  const pickFile = () => fileRef.current?.click();
  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      setFile(f);
      setRaw("");
      setMessage(`已选择 ${f.name}`);
    }
    e.target.value = "";
  };

  return (
    <>
      {mode === "csv" || mode === "bibtex" ? (
        <div style={{ marginBottom: 12 }}>
          <button className="secondary" onClick={pickFile}>
            {file ? `已选择：${file.name}` : "选择文件"}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept={mode === "csv" ? ".csv,.txt" : ".bib,.bibtex,.txt"}
            style={{ display: "none" }}
            onChange={onFile}
          />
          <span className="small muted" style={{ marginLeft: 8 }}>或直接粘贴内容到下方</span>
        </div>
      ) : null}

      <label className="small">{LABEL[mode]}</label>
      <textarea
        rows={8}
        style={{ marginTop: 6, fontFamily: mode === "bibtex" ? "monospace" : undefined }}
        value={raw}
        onChange={e => setRaw(e.target.value)}
        placeholder={PLACEHOLDER[mode]}
        disabled={mode === "csv" || mode === "bibtex" ? !!file : false}
      />

      <div style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center" }}>
        <button onClick={importPapers} disabled={importing || (!raw.trim() && !file)}>
          {importing ? "导入中…" : "开始导入"}
        </button>
        {message && <span className="muted">{message}</span>}
      </div>
      <p className="small muted" style={{ marginTop: 8 }}>{HINT[mode]}</p>
    </>
  );
}
