// 批量导入解析：CSV / BibTeX → {doi?|title?}[]，供现有导入 API 使用

export interface ImportItem {
  doi?: string;
  title?: string;
  pmid?: string;
}

const DOI_RE = /10\.\d{4,9}\/[^\s,，、;；]+/i;

function cleanDoi(s: string): string | null {
  const m = s.trim().match(DOI_RE);
  return m ? m[0].replace(/[,.;。;]+$/, "") : null;
}

/** 纯文本：每行一个 DOI 或标题，自动识别 */
export function parsePlain(text: string): ImportItem[] {
  const items: ImportItem[] = [];
  const seen = new Set<string>();
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    const cleaned = t.replace(/^https?:\/\/doi\.org\//i, "");
    const doi = cleanDoi(cleaned);
    const item = doi ? { doi } : { title: t };
    const key = doi ? `d:${doi.toLowerCase()}` : `t:${t.toLowerCase()}`;
    if (!seen.has(key)) {
      seen.add(key);
      items.push(item);
    }
  }
  return items;
}

/** CSV：期望含 DOI / Title / 标题 列的列头；逐行提取两列中任一 */
export function parseCsv(text: string): ImportItem[] {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) return [];
  const header = splitCsvLine(lines[0]).map(h => h.trim().toLowerCase());
  const doiIdx = header.findIndex(h => h.includes("doi"));
  const titleIdx = header.findIndex(h => /title|标题/.test(h));
  const items: ImportItem[] = [];
  const seen = new Set<string>();
  for (let i = 1; i < lines.length; i++) {
    const cells = splitCsvLine(lines[i]);
    const doi = doiIdx >= 0 && cells[doiIdx] ? cleanDoi(cells[doiIdx].replace(/^"|"$/g, "")) : null;
    const title = titleIdx >= 0 && cells[titleIdx] ? cells[titleIdx].replace(/^"|"$/g, "").trim() : "";
    if (!doi && !title) continue;
    const item: ImportItem = doi ? { doi } : { title };
    const key = doi ? `d:${doi.toLowerCase()}` : `t:${title.toLowerCase()}`;
    if (!seen.has(key)) {
      seen.add(key);
      items.push(item);
    }
  }
  return items;
}

/** 简易 CSV 行切分（处理带引号字段） */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuote) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') {
        inQuote = false;
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuote = true;
    } else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

/** BibTeX：解析 @type{key, field = {value}, ...} 条目，取 doi / title */
export function parseBibtex(text: string): ImportItem[] {
  const items: ImportItem[] = [];
  const seen = new Set<string>();
  // 匹配条目体：@article{...} 或 @article{...,...}（含嵌套大括号，取平衡）
  const re = /@[a-zA-Z]+\s*\{([\s\S]*?)\n\s*\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const body = m[1];
    const doi = field(body, "doi");
    const title = field(body, "title");
    if (doi) {
      const d = cleanDoi(doi);
      if (d) {
        const key = `d:${d.toLowerCase()}`;
        if (!seen.has(key)) {
          seen.add(key);
          items.push({ doi: d });
        }
      }
    } else if (title && !seen.has(`t:${title.toLowerCase()}`)) {
      seen.add(`t:${title.toLowerCase()}`);
      items.push({ title });
    }
  }
  return items;
}

/** 从 BibTeX 字段体提取字段值：key = { value } */
function field(body: string, key: string): string | null {
  const re = new RegExp(`(?:^|,\\s*)${key}\\s*=\\s*\\{([^}]*)\\}`.replace(/\\s/g, "\\s"), "i");
  const m = body.match(re);
  if (!m) return null;
  // 去嵌套大括号尾残留
  let v = m[1].trim().replace(/\{[^}]*\}/g, "").replace(/[{}]/g, "").trim();
  v = v.replace(/[,.]+$/, "");
  return v || null;
}

export function formatImportCount(items: ImportItem[]): string {
  const doiCount = items.filter(i => i.doi).length;
  const titleCount = items.length - doiCount;
  return `${items.length} 条（DOI ${doiCount} / 标题 ${titleCount}）`;
}
