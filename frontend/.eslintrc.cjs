/* eslint config for the Cadless frontend (TypeScript). */

// `no-restricted-imports` alone does not make the contract a check. Two things
// walk straight past it, both measured against the real binary rather than
// reasoned about:
//
//   1. It does not look at `import()`. Linted as a file under `src/plugins/`,
//      `export { req } from "../../api"` is reported and `import("../../api")`
//      on the line above it is not.
//   2. Its patterns are matched as written, so a different spelling of the same
//      path is a different pattern. `./../../api` resolves to exactly the file
//      `../../api` does, and was allowed by both the group patterns and by the
//      first version of the selector below.
//
// A rule that reports success without having asked its question is worse than
// no rule: it is a claim that lint checked something it never looked at.
//
// So the selectors below match on the *shape* of the specifier rather than on
// one spelling of it — any `../` segment, wherever it appears — and cover
// static imports, re-exports and dynamic imports alike. `../../plugin` is the
// single exception, spelled exactly; an odd spelling of the contract is refused
// too, which fails closed and says what to write instead.
//
// The second entry is the one that keeps this honest. A specifier that is not a
// plain string cannot be read statically at all — a template literal has no
// `source.value` and slipped through the first version — so rather than pretend
// otherwise, every dynamic import that is not a literal is refused. Both gated
// directories are small, and neither has a reason to compute an import path.
const REACH_AROUND_NODES =
  ":matches(ImportDeclaration, ExportNamedDeclaration, ExportAllDeclaration, ImportExpression)";

const REACH_AROUND = [
  {
    selector: `${REACH_AROUND_NODES}[source.value=/(^|\\/)\\.\\.\\//]:not([source.value='../../plugin'])`,
    message:
      "This reaches out of the directory and around the contract. Import from src/plugin.ts — spelled exactly `../../plugin` — or widen the contract on purpose.",
  },
  {
    selector: "ImportExpression:not([source.type='Literal'])",
    message:
      "A dynamic import whose specifier is not a plain string cannot be checked against the contract, so it is refused rather than assumed harmless. Write the path as a literal.",
  },
];

module.exports = {
  root: true,
  parser: "@typescript-eslint/parser",
  plugins: ["@typescript-eslint", "react-hooks"],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  env: { browser: true, es2022: true, node: true },
  parserOptions: { ecmaVersion: 2022, sourceType: "module" },
  ignorePatterns: ["dist", "node_modules"],
  rules: {
    "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
  },
  overrides: [
    {
      // A plugin lives inside `src/`, so nothing about the module system stops
      // it importing the tree directly — `../../viewport/viewportStore` type
      // checks, lints and builds. The contract in `src/plugin.ts` says not to,
      // and prose is not a check. This makes it one, in the only tree that can:
      // the build that gets broken by the reach-around is the one that has to
      // notice it.
      files: ["src/plugins/**"],
      rules: {
        "no-restricted-imports": [
          "error",
          {
            patterns: [
              {
                group: ["../../**", "!../../plugin"],
                message:
                  "A plugin imports from src/plugin.ts and nothing else — that file is the contract. What it withholds is internal and free to move, so reaching past it breaks on an upgrade that broke no promise.",
              },
            ],
          },
        ],
        "no-restricted-syntax": ["error", ...REACH_AROUND],
      },
    },
  ],
};
