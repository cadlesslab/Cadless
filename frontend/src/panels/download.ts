/** Getting bytes from the server to where the reader wanted them.
 *
 * File plumbing rather than anything about exporting or printing: a panel that
 * happens to offer a download should not also be the place that knows how a
 * browser is made to save one.
 */
import { printHeaders } from "../api";

/** The status text as well as the number: this message is shown to someone, and
 * a toast body reading only "409" tells them nothing they can act on. */
export function httpError(res: { status: number; statusText?: string }): Error {
  return new Error(res.statusText ? `${res.status} ${res.statusText}` : `${res.status}`);
}

/** Fetch a URL and hand the result to the browser's downloader.
 *
 * The anchor is created, clicked and removed rather than pointed at the URL
 * directly, because the response needs headers — an artifact route wants none
 * but the G-code route wants the print header, and a plain link carries
 * neither. The object URL is revoked straight after: the blob is a copy of the
 * whole file, and a tab that exports a few models otherwise holds all of them.
 */
export async function fetchAndSave(
  url: string,
  filename: string,
  init?: RequestInit,
): Promise<void> {
  const res = await fetch(url, init);
  if (!res.ok) throw httpError(res);
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

/** The same bytes as the download, as text rather than as a file.
 *
 * The USB path needs the job in hand to send it line by line, so it reads the
 * body instead of handing it to the browser's downloader. Same URL, same
 * header, same server route — only the destination differs.
 */
export async function fetchGcode(url: string): Promise<string> {
  const res = await fetch(url, { headers: printHeaders() });
  if (!res.ok) throw httpError(res);
  return res.text();
}
