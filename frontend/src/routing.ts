/** URL deep-linking by project id.
 *
 * Each project — and each catalog item, which is loaded as a project — already
 * has a unique id; this reflects the active one in the address bar as
 * `<base><id>` (e.g. https://example.com/apps/cadless/2), so every project is
 * deep-linkable and shareable. The SPA server already falls back to index.html
 * for these paths (infra/nginx.conf `try_files … /apps/cadless/index.html`), so a
 * hard navigation to `/apps/cadless/2` boots the app, which then opens project 2.
 */

/** The build-time base path the SPA is served under — "/apps/cadless/" in prod,
 * "/" in dev — injected by Vite (mirrors vite.config `base`). */
export const BASE_URL: string = import.meta.env.BASE_URL || "/";

/** The id segment of a path like `<base><id>`, or null when there isn't one. */
export function projectIdFromPath(
  pathname: string = window.location.pathname,
  base: string = BASE_URL,
): number | null {
  const rest = pathname.startsWith(base)
    ? pathname.slice(base.length)
    : pathname.replace(/^\//, "");
  const seg = rest.split("/")[0].trim();
  return /^\d+$/.test(seg) ? Number(seg) : null;
}

/** The path for a project id, e.g. "/apps/cadless/2". */
export function projectPath(id: number, base: string = BASE_URL): string {
  return base.endsWith("/") ? `${base}${id}` : `${base}/${id}`;
}

/** Reflect the active project id in the address bar (replaceState — no history
 * spam, no reload). A no-op when the path already matches or the id is null. */
export function syncProjectUrl(id: number | null): void {
  if (id == null) return;
  const target = projectPath(id);
  if (window.location.pathname !== target) {
    window.history.replaceState(null, "", target);
  }
}
