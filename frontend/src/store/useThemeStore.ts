import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "light" | "dark";

interface ThemeStore {
  theme: Theme;
  toggle: () => void;
}

export const useThemeStore = create<ThemeStore>()(
  persist(
    set => ({
      theme: "dark",
      toggle: () => set(s => ({ theme: s.theme === "light" ? "dark" : "light" })),
    }),
    { name: "lit-eval:theme-glass-v1" }
  )
);
