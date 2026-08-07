/** Runtime config. VITE_API_BASE is injected at build time (see vite.config.ts). */
export const API_BASE: string =
  (import.meta.env?.VITE_API_BASE as string | undefined) ?? "";
