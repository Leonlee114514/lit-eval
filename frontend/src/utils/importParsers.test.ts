import { describe, expect, it } from "vitest";
import { formatImportCount, parseBibtex, parseCsv, parsePlain } from "./importParsers";

// 这些函数的错法是静默的：少解析一条 DOI = 少导入一篇文献，界面上只会显示
// "N 条（DOI x / 标题 y）"小一号的数字，没人会发现。

describe("parsePlain", () => {
  it("每行一个 DOI，带 https://doi.org/ 前缀也认", () => {
    const items = parsePlain("10.1038/ng.123\nhttps://doi.org/10.1000/xyz\n");
    expect(items).toEqual([{ doi: "10.1038/ng.123" }, { doi: "10.1000/xyz" }]);
  });

  it("认不出 DOI 的行按标题处理", () => {
    expect(parsePlain("Waterborne polyurethane coatings")).toEqual([
      { title: "Waterborne polyurethane coatings" },
    ]);
  });

  it("去重大小写不敏感，保留首次出现的写法", () => {
    expect(parsePlain("10.1000/AbC\n10.1000/abc\n10.1000/abc")).toEqual([{ doi: "10.1000/AbC" }]);
  });

  it("忽略空行与首尾空白", () => {
    expect(parsePlain("\n   10.1000/x   \n\n")).toEqual([{ doi: "10.1000/x" }]);
  });

  it("剥掉 DOI 结尾的标点（从 PDF 里复制常见）", () => {
    expect(parsePlain("10.1000/x.")).toEqual([{ doi: "10.1000/x" }]);
  });

  it("空输入返回空数组", () => {
    expect(parsePlain("")).toEqual([]);
    expect(parsePlain("\n\n")).toEqual([]);
  });
});

describe("parseCsv", () => {
  it("按列头找 DOI / Title，一行里两者都有时 DOI 优先", () => {
    const csv = "Title,DOI\n涂料,10.1000/x\n无DOI的标题,\n";
    expect(parseCsv(csv)).toEqual([{ doi: "10.1000/x" }, { title: "无DOI的标题" }]);
  });

  it("中文列头「标题」也认", () => {
    const csv = "标题,DOI\n某综述,\n";
    expect(parseCsv(csv)).toEqual([{ title: "某综述" }]);
  });

  it("处理带引号与转义引号的字段", () => {
    const csv = 'DOI,Title\n10.1000/y,"A ""quoted"" title"\n';
    expect(parseCsv(csv)).toEqual([{ doi: "10.1000/y" }]);
  });

  it("没有可用列头时返回空（不猜列）", () => {
    expect(parseCsv("a,b\n1,2\n")).toEqual([]);
  });

  it("空输入返回空数组", () => {
    expect(parseCsv("")).toEqual([]);
  });
});

describe("parseBibtex", () => {
  it("取 doi 字段", () => {
    const bib = "@article{key1,\n  title = {Some Title},\n  doi = {10.1000/abc},\n}";
    expect(parseBibtex(bib)).toEqual([{ doi: "10.1000/abc" }]);
  });

  it("没有 doi 时退回 title", () => {
    const bib = "@article{key2,\n  title = {Only A Title},\n}";
    expect(parseBibtex(bib)).toEqual([{ title: "Only A Title" }]);
  });

  it("多条条目里 DOI 重复只保留一条", () => {
    const bib = "@article{a,\n  doi = {10.1000/dup},\n}\n@article{b,\n  doi = {10.1000/dup},\n}";
    expect(parseBibtex(bib)).toHaveLength(1);
  });

  it("非 BibTeX 文本返回空数组", () => {
    expect(parseBibtex("just some plain text")).toEqual([]);
  });
});

describe("formatImportCount", () => {
  it("按 DOI / 标题分别计数", () => {
    expect(formatImportCount([{ doi: "10.1/a" }, { title: "t" }, { title: "u" }])).toBe(
      "3 条（DOI 1 / 标题 2）",
    );
  });

  it("空列表", () => {
    expect(formatImportCount([])).toBe("0 条（DOI 0 / 标题 0）");
  });
});
