/** The one file under `src/plugins/` this repository owns, so the gate has a subject.
 *
 * `.eslintrc.cjs` holds everything under `src/plugins/**` to importing
 * `src/plugin.ts` and nothing else — that rule is what decides whether a panel
 * can still ship from another repository, and `.github/workflows/ci.yml` runs
 * it for exactly that reason. But the directory is git-ignored, so on a fresh
 * checkout the rule matched no file at all and CI was answering a question
 * about an empty set. A gate that reports success without having asked its
 * question is the failure mode `.eslintrc.cjs` opens by naming.
 *
 * **Deliberately not called `register.ts`.** `src/panels/plugins.ts` globs
 * `../plugins/{asterisk}/register.{ts,tsx}`, so nothing imports this file: it is never
 * bundled, registers no panel, and adds no rail entry. ESLint's `src/plugins/**`
 * still reads it, which is the whole point — lint sees it, the app does not.
 *
 * Keep the imports below reaching only through the contract. Adding
 * `../../viewport/viewportStore` here should turn `npm run lint` red; if it
 * does not, the gate has stopped deciding and this file is the place that says
 * so first.
 */
import { API_BASE, domainIcon, errMessage, request, showPreview } from "../../plugin";
import type { Preview } from "../../plugin";

/** Exercised, not merely imported: an unused import is a different lint error. */
export function contractIsReachable(): boolean {
  const preview: Preview = { url: null, title: "contract canary" };
  return (
    typeof API_BASE === "string" &&
    typeof request === "function" &&
    typeof errMessage === "function" &&
    typeof showPreview === "function" &&
    domainIcon(undefined) !== undefined &&
    preview.title.length > 0
  );
}
