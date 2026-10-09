import { useEffect } from "react";

// 设置当前页面 <title>（后缀统一为「· 文献评估」）
export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
