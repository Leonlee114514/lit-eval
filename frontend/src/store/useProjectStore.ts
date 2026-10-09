import { create } from "zustand";
import { persist } from "zustand/middleware";

interface ProjectStore {
  projectId: string | null;
  setProjectId: (id: string | null) => void;
}

export const useProjectStore = create<ProjectStore>()(
  persist(
    set => ({
      projectId: null,
      setProjectId: id => set({ projectId: id }),
    }),
    { name: "lit-eval:project" }
  )
);
