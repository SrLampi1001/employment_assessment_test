# Project setup

## Scaffolding

Vite is the standard scaffold for a new React app — Create React App is dead and shouldn't be suggested for new projects.

```bash
# With the React Compiler enabled (recommended default for a new project)
npm create vite@latest my-app -- --template react-compiler-ts

# Without the compiler
npm create vite@latest my-app -- --template react-ts
```

Check which template a project used (or whether the compiler plugin is present in `vite.config.ts`) before assuming manual memoization is or isn't needed — see the Compiler section in `references/react-fundamentals.md`.

## Core dependencies for the stack this skill covers

```bash
npm install react@^19 react-dom@^19 react-router@^8 @tanstack/react-query@^5
npm install -D typescript@^7 @tanstack/react-query-devtools vitest @testing-library/react @testing-library/user-event
```

Don't add `prop-types` (obsolete under TypeScript) or `react-router-dom` (folded into `react-router` in v8) to a new project.

## `tsconfig.json` baseline

TypeScript 7 defaults to `strict`, ESM, and a modern target already, but pin the settings explicitly in a shared/team project rather than relying on version defaults that could shift:

```jsonc
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "verbatimModuleSyntax": true,
    "noUncheckedIndexedAccess": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "skipLibCheck": true
  }
}
```

`moduleResolution: "bundler"` matches how Vite actually resolves modules; `verbatimModuleSyntax` keeps type-only imports explicit (`import type { Foo } from ...`), which matters once `react-router`/`@tanstack/react-query` re-export both types and runtime values from the same entry point.

## ESLint

Use flat config (`eslint.config.js`), not a legacy `.eslintrc.*` — ESLint 9+ defaults to flat config and most current plugin docs assume it.

```js
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
    },
  },
);
```

If the project has the React Compiler enabled, also add `eslint-plugin-react-hooks`'s compiler-aware rules (or `eslint-plugin-react-compiler` if the project pulls it in separately) — it catches violations of the rules the compiler depends on (no conditional hooks, no mutating props/state directly, etc.) at lint time instead of as a silent compiler bail-out.

## Testing

Vitest pairs naturally with Vite (shares config, no separate transform setup) and React Testing Library for component tests — see the Testing section of `references/react-fundamentals.md` for the actual test-writing conventions.

```bash
npm install -D vitest @testing-library/react @testing-library/user-event jsdom
```

```ts
// vite.config.ts
export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
  },
});
```
