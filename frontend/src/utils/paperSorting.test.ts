import { describe, expect, it } from "vitest";
import type { Paper } from "../api/types";
import {
  countDownloadable,
  filterPapers,
  sortPapers,
  tierRank,
  toggleSort,
} from "./paperSorting";

/** 造一篇最小可用的论文：只带排序 / 筛选真正会读的字段 */
function paper(over: Partial<Paper> & { id: string }): Paper {
  return {
    project_id: "p1",
    status: "done",
    missing_fields: [],
    is_classic: false,
    created_at: "",
    updated_at: "",
    ...over,
  } as Paper;
}

const scored = (id: string, score: number | null, tier: string | null) =>
  paper({
    id,
    title: id,
    evaluation: { composite_score: score, decision: null, tier, radar_scores: null },
  });

describe("tierRank", () => {
  it("按档位高低排序，未评估最低", () => {
    expect(tierRank("high_priority")).toBeGreaterThan(tierRank("recommended"));
    expect(tierRank("recommended")).toBeGreaterThan(tierRank("conditional"));
    expect(tierRank("conditional")).toBeGreaterThan(tierRank("not_recommended"));
    expect(tierRank("not_recommended")).toBeGreaterThan(tierRank(null));
    expect(tierRank(undefined)).toBe(0);
  });
});

describe("filterPapers", () => {
  const list = [
    scored("a", 90, "high_priority"),
    scored("b", 70, "recommended"),
    scored("c", null, null),
  ];

  it("all 原样返回", () => {
    expect(filterPapers(list, "all")).toHaveLength(3);
  });

  it("pending 只留还没评出档位的", () => {
    expect(filterPapers(list, "pending").map(p => p.id)).toEqual(["c"]);
  });

  it("具体档位精确匹配", () => {
    expect(filterPapers(list, "high_priority").map(p => p.id)).toEqual(["a"]);
  });

  it("没有匹配项时返回空数组", () => {
    expect(filterPapers(list, "not_recommended")).toEqual([]);
  });
});

describe("sortPapers", () => {
  it("综合分降序：未评估排最后（缺失记 -1，不是 0）", () => {
    const list = [scored("c", null, null), scored("a", 90, "high_priority"), scored("b", 70, "recommended")];
    expect(sortPapers(list, "composite", "desc").map(p => p.id)).toEqual(["a", "b", "c"]);
  });

  it("综合分升序：未评估排最前（它确实是最小值）", () => {
    const list = [scored("a", 90, "high_priority"), scored("c", null, null)];
    expect(sortPapers(list, "composite", "asc").map(p => p.id)).toEqual(["c", "a"]);
  });

  it("不改动入参数组", () => {
    const list = [scored("b", 1, null), scored("a", 2, null)];
    const before = list.map(p => p.id);
    sortPapers(list, "composite", "desc");
    expect(list.map(p => p.id)).toEqual(before);
  });

  it("被引次数降序：缺失记 -1 排最后，真实的 0 仍大于缺失", () => {
    const list = [
      paper({ id: "a", cited_by_count: null }),
      paper({ id: "b", cited_by_count: 0 }),
      paper({ id: "c", cited_by_count: 5 }),
    ];
    expect(sortPapers(list, "citations", "desc").map(p => p.id)).toEqual(["c", "b", "a"]);
  });

  it("标题缺失去掉时退化到 DOI", () => {
    const list = [
      paper({ id: "x", title: null, doi: "10.1/z" }),
      paper({ id: "y", title: null, doi: null }),
    ];
    expect(sortPapers(list, "title", "asc").map(p => p.id)[0]).toBe("y");
  });

  it("tier 降序：未评估排最后", () => {
    const list = [scored("c", 50, null), scored("a", 50, "high_priority"), scored("b", 50, "conditional")];
    expect(sortPapers(list, "tier", "desc").map(p => p.id)).toEqual(["a", "b", "c"]);
  });

  it("期刊百分位缺失同样排在最后", () => {
    const list = [
      paper({ id: "a", journal_percentile: null }),
      paper({ id: "b", journal_percentile: 82.5 }),
    ];
    expect(sortPapers(list, "journal_percentile", "desc").map(p => p.id)).toEqual(["b", "a"]);
  });
});

describe("toggleSort", () => {
  it("同列再点一次翻转方向", () => {
    expect(toggleSort({ key: "composite", dir: "desc" }, "composite")).toEqual({
      key: "composite", dir: "asc",
    });
  });

  it("换列用该列默认方向：文本升序、数值降序", () => {
    expect(toggleSort({ key: "composite", dir: "desc" }, "title")).toEqual({
      key: "title", dir: "asc",
    });
    expect(toggleSort({ key: "title", dir: "asc" }, "composite")).toEqual({
      key: "composite", dir: "desc",
    });
  });
});

describe("countDownloadable", () => {
  it("只数选中项里还没有全文的", () => {
    const list = [
      paper({ id: "a", has_fulltext: true }),
      paper({ id: "b", has_fulltext: false }),
      paper({ id: "c" }),
    ];
    expect(countDownloadable(list, new Set(["a", "b", "c"]))).toBe(2);
  });

  it("选中里有已不存在的 id 时也计入（它显然需要下载）", () => {
    expect(countDownloadable([paper({ id: "a" })], new Set(["ghost"]))).toBe(1);
  });
});
