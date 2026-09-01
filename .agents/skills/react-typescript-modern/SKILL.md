---
name: react-typescript-modern
description: Use this for ANY React work in TypeScript — writing, reviewing, or refactoring components, hooks, forms, routing, or data-fetching code; scaffolding a new React app; or answering "how do I do X in React" questions. Ensures code follows React 19.2+ (React Compiler-aware), React Router v8, TanStack Query v5, and TypeScript 7 conventions instead of the React 16-18 / class-component / react-router-dom / callback-based patterns that dominate training data. Trigger this even when the user doesn't mention version numbers or say "modern" or "latest" — stale defaults are the default failure mode, so treat every .tsx file, every "add a component/hook/route/query," and every React code review as in-scope.
---

# React + TypeScript (current stack)

> **NOT a project-specific skill (verified 2026-08-29).** This skill
> is **generic React 19 advice** — it does NOT describe the
> Riwi Co. frontend's conventions. The project does **not** use
> React Router, TanStack Query, Vitest, or any of the tools this
> skill describes. The shipped frontend is a hand-rolled
> fetch + state + react-i18next single-page app:
>
> - Routing: none — three-pane layout in `frontend/src/App.tsx`
>   with `useState`-driven channel selection.
> - Data fetching: plain `fetch` + `useState` (see
>   `frontend/src/copilot/CopilotPanel.tsx`, `frontend/src/messages/Conversation.tsx`).
> - i18n: `react-i18next` 17.x with `frontend/src/i18n/{en,es}.json`.
> - Tests: none. The project has no `*.test.ts` / `*.test.tsx`
>   files; `package.json` has no Vitest / Testing Library.
>
> **Do not refactor the frontend onto the stack this skill describes
> without an issue that names the concrete benefit.** Per
> `/AGENTS.md`, skills should guard THIS project, not give generic
> upgrade advice — when the project adopts any of these tools, a
> scoped skill will replace this one.

## Why this skill exists

Training data has a long tail. Class components, `PropTypes`, `react-router-dom`, and `onSuccess` callbacks on `useQuery` were all correct answers for years, so they show up constantly in generated code even now that each one has been replaced or removed. This skill exists to counteract that gravity: it captures what changed, why, and what to write instead.

Software moves fast enough that even this skill will drift. Treat the version table below as a snapshot, not gospel — if something feels off or a package.json shows different majors, trust the project over this file.

## Ground rule: check the project before assuming "latest"

Before writing code, glance at `package.json` (or the lockfile) if one is available.

- **Existing project on older majors** (React 18, React Router 6, `react-query` v3/v4, no `@tanstack` scope) → follow *that* project's conventions. The goal of this skill is to stop drifting toward stale patterns by default, not to silently rewrite someone's app onto a stack they haven't opted into. Mixing API generations in one codebase (e.g. `useQuery` v5 syntax against a v4 install) breaks at runtime, not just in style.
- **New project, or the user says "modern"/"latest"/"current" and doesn't specify** → default to the baseline below.
- **Genuinely unsure and it matters** (e.g. scaffolding from scratch) → it's fine to ask once rather than guess a major version.

## Current baseline

| Package | Current major | What changed from the "textbook" version |
|---|---|---|
| `react` / `react-dom` | 19.2.x | Actions, `use()`, ref-as-prop, stable Server Components, React Compiler 1.0 |
| `react-router` | 8.x | `react-router-dom` is retired — everything ships from `react-router` (+ `react-router/dom` for browser entry points); ESM-only; middleware stable by default |
| `@tanstack/react-query` | 5.x | Package renamed from `react-query`; `queryOptions()`, `gcTime`, no more query-level `onSuccess`/`onError` |
| `typescript` | 7.x | Go-native compiler (`tsc` is fast again); `strict`, ESM, and `es2025` are now the defaults |
| `vite` | 7.x | The standard scaffold — Create React App is dead, don't suggest it |
| `node` | 22.22+ LTS | Floor required by React Router v8 |

> **Honest caveat about TypeScript 7 + Vite 7.** The combination here matches because the *linter* is the gating dependency, not Vite. `typescript-eslint@8.63.0` (Aug 2026) has a peer dep of `typescript >=4.8.4 <6.1.0` — `npm run build` works with TS 7 + Vite 8, but `npm run lint` crashes inside `@typescript-eslint/typescript-estree` ([typescript-eslint#12518](https://github.com/typescript-eslint/typescript-eslint/issues/12518)). If the project adopts ESLint with `typescript-eslint`, the actual pin becomes **TS 6.x + Vite 7.x or Vite 8.x** until `typescript-eslint` lifts its peer-dep cap. Before adding a CI lint job, double-check that pin against the current `typescript-eslint` release notes.

## The trap: things that look idiomatic but are gone or replaced

These aren't stylistic nitpicks — several of these will throw at build time or runtime on a current install, not just look old-fashioned.

| Instead of this | Write this | Why |
|---|---|---|
| `class Foo extends React.Component` for new code | Function component + hooks | Hooks have been the default for years; class lifecycle methods (`componentWillMount`, etc.) are the main thing still tripping up generated code |
| `Foo.propTypes = {...}` | A TypeScript `type`/`interface` for props | `propTypes` is silently ignored in React 19 — it does nothing anymore |
| `Foo.defaultProps = {...}` on a function component | ES6 default parameters: `function Foo({ size = 100 }: Props)` | Removed in React 19 for function components (class components still support it) |
| `ReactDOM.render(<App />, el)` | `createRoot(el).render(<App />)` | `ReactDOM.render`/`hydrate` were removed in React 19 |
| String refs (`ref="myRef"`) | `useRef()` or a callback ref | Removed in React 19 |
| `forwardRef((props, ref) => ...)` for new components | Accept `ref` as an ordinary prop | `forwardRef` still works but is on a deprecation path — see `references/react-fundamentals.md` |
| `import { BrowserRouter } from 'react-router-dom'` | `import { BrowserRouter } from 'react-router/dom'` (and everything else from `'react-router'`) | The `react-router-dom` package no longer exists in v8 |
| `<Switch>` / `useHistory()` | `<Routes>` / `useNavigate()` | Leftover React Router v5 muscle memory — v5 has been gone for years but keeps surfacing |
| `useQuery({ queryFn, onSuccess, onError })` | Handle the result where you call the hook, or use `QueryCache`/`MutationCache` global callbacks | Query-level callbacks were removed in TanStack Query v5 (mutations kept theirs) |
| `cacheTime` | `gcTime` | Renamed in v5 |
| Fetching data with a bare `useEffect` + `useState` | `useQuery`/`useSuspenseQuery`, or a route `loader` | Manual effect-fetching reintroduces race conditions and cache bugs that both libraries already solved |

## Where to go next

Pull in the reference file(s) that match the task — don't load all of them for a one-line fix.

- **`references/react-fundamentals.md`** — components, props, hooks, Actions/forms, `ref`-as-prop, the React Compiler, testing. Read this for any component or hook work.
- **`references/data-fetching-react-query.md`** — TanStack Query v5: queries, mutations, cache keys, optimistic updates, pairing with router loaders. Read this for anything touching server data.
- **`references/routing-react-router.md`** — React Router v8: data routers, loaders/actions, middleware, forms/fetchers. Read this for routing or navigation work.
- **`references/project-setup.md`** — scaffolding a new app, tsconfig, lint config, test setup. Read this when starting a project from scratch or auditing tooling.

## A note on tone

Don't turn every response into a lecture about deprecated APIs. If the user asks for a component, write a correct, current component — mention *why* only when it's genuinely useful (e.g. they asked for `forwardRef`, or their existing code uses a removed API and needs updating).
