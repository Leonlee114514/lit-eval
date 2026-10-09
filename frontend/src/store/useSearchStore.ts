import { create } from "zustand";
import { persist } from "zustand/middleware";

/** 主题搜索的"扩展同义词"开关（对应后端 SEARCH_QUERY_MODE）。
 *
 * 默认关（raw = 原样透传输入）。开启后会用同义词表做 OR 扩展，例如
 * `waterborne polyurethane coating`
 *   → `(waterborne OR aqueous OR "water-borne" OR waterbased) AND polyurethane AND coating`
 * 召回明显变多（实测化学/涂层课题 +270% ~ +390%），但也可能混进噪声，
 * 所以做成每个课题可自己开关，而不是写死在配置文件里。
 */
interface SearchStore {
  expandSynonyms: boolean;
  toggleExpandSynonyms: () => void;
}

export const useSearchStore = create<SearchStore>()(
  persist(
    set => ({
      expandSynonyms: false,
      toggleExpandSynonyms: () => set(s => ({ expandSynonyms: !s.expandSynonyms })),
    }),
    { name: "lit-eval:search-expand-v1" }
  )
);
