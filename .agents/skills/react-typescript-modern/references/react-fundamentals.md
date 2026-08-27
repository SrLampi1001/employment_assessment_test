# React fundamentals (19.2+)

## Components and props

Write function components with an explicit props type. Don't reach for `React.FC` — it implicitly adds `children` to every component whether or not it accepts them, and it makes generic components awkward to type. A plain typed function is clearer:

```tsx
type UserCardProps = {
  name: string;
  role: string;
  onSelect?: (name: string) => void;
};

function UserCard({ name, role, onSelect }: UserCardProps) {
  return (
    <button onClick={() => onSelect?.(name)}>
      {name} — {role}
    </button>
  );
}
```

If a project already uses `React.FC` consistently, match its style rather than fighting it — this is a preference, not a correctness issue.

## APIs that were removed, not just discouraged

These will actually fail (silently ignored, or a thrown error) on a current install — worth knowing the difference between "old-fashioned but works" and "removed."

**`propTypes`** — silently ignored now. Migrate to TypeScript types.

```tsx
// Before
Heading.propTypes = { text: PropTypes.string };
Heading.defaultProps = { text: "Hello" };

// After
type HeadingProps = { text?: string };
function Heading({ text = "Hello" }: HeadingProps) {
  return <h1>{text}</h1>;
}
```

**Legacy Context** (`contextTypes` / `getChildContext` on class components) — removed. Use `createContext` + `useContext` (or `<Context value={...}>` — the `.Provider` wrapper is no longer required as of React 19, `<Context>` itself works as the provider).

**String refs** (`ref="thing"`) — removed. Use `useRef()` or a callback ref.

**`ReactDOM.render` / `ReactDOM.hydrate`** — removed. Use `createRoot(container).render(...)` / `hydrateRoot(container, ...)`.

**`ReactDOM.unmountComponentAtNode`** — removed. Use `root.unmount()`.

**`ReactDOM.findDOMNode`** — removed. Use a ref on the element you actually need.

**`react-test-renderer`** — deprecated, logs warnings. Use React Testing Library instead (see Testing below).

## `ref` as a prop

React 19 made `ref` an ordinary prop, so `forwardRef` is no longer needed for the common case of "let a parent attach a ref to my root element":

```tsx
type InputProps = { label: string; ref?: React.Ref<HTMLInputElement> };

function TextInput({ label, ref }: InputProps) {
  return (
    <label>
      {label}
      <input ref={ref} />
    </label>
  );
}
```

`forwardRef` still works and nothing breaks if you see it in an existing codebase — it's on a deprecation path, not removed. Don't reach for it in new code, and don't bother rewriting working `forwardRef` components just to modernize them unless you're already touching that file for another reason.

## Data fetching: don't reach for `useEffect` first

A `useEffect` + `useState` fetch is the most common stale pattern in generated React code, and it reintroduces problems (races between fast unmount and slow response, no caching, no dedup, no retry) that are solved by tools written for this. If the component needs server data, that's TanStack Query's job — see `references/data-fetching-react-query.md`. Reserve `useEffect` for genuinely client-side concerns: subscriptions, syncing with a non-React widget, `document.title`, etc.

## Actions and forms

React 19's Actions are the current way to handle a pending mutation with built-in pending/error state, replacing the old "local `isSubmitting` state + manual `preventDefault`" pattern for anything beyond the most trivial form:

```tsx
import { useActionState } from "react";

function ProfileForm({ userId }: { userId: string }) {
  const [error, submitAction, isPending] = useActionState(
    async (_prevState: string | null, formData: FormData) => {
      const res = await fetch(`/api/users/${userId}`, {
        method: "PATCH",
        body: formData,
      });
      if (!res.ok) return "Could not save your profile.";
      return null;
    },
    null,
  );

  return (
    <form action={submitAction}>
      <input name="displayName" />
      <button disabled={isPending}>{isPending ? "Saving…" : "Save"}</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
```

Related hooks, all part of the same mechanism:
- **`useFormStatus()`** — read the pending state of the nearest parent `<form>` from a child component (e.g. a reusable `<SubmitButton>`), without prop-drilling `isPending`.
- **`useOptimistic()`** — show an optimistic value immediately while an action is in flight, then reconcile with the real result.

For a form that's genuinely just local UI state with no submission (a filter input, a search box), plain `useState` is still correct — don't force Actions onto things that aren't submitting anywhere.

## `use()` for promises and context

`use()` reads a promise or a context value, and — unlike hooks — it can be called conditionally or in a loop:

```tsx
function Comments({ commentsPromise }: { commentsPromise: Promise<Comment[]> }) {
  const comments = use(commentsPromise); // suspends until resolved
  return <ul>{comments.map((c) => <li key={c.id}>{c.text}</li>)}</ul>;
}
```

The promise must be created or cached *outside* render (module scope, a ref, a cache like React Query's) — a fresh promise created inline on every render never resolves from the component's point of view, so the Suspense fallback never clears. This is the same rule that makes `useSuspenseQuery` from TanStack Query a natural fit for `use()`-adjacent patterns.

## The React Compiler — check before you hand-memoize

The React Compiler (stable since October 2025) auto-memoizes components and values, which makes a lot of manual `useMemo`/`useCallback`/`React.memo` unnecessary — but only in projects that have actually enabled it (Vite's `react-compiler-ts` template, or the `babel-plugin-react-compiler` / compiler-enabled `@vitejs/plugin-react` config). Don't assume it's on.

- **Compiler enabled** (check `vite.config.ts` for the compiler plugin, or the Vite template name): skip manual memoization by default. Reach for it only as an escape hatch — e.g. a value that needs referential stability for a non-React consumer (a `useEffect` dependency into a third-party library, a WeakMap key), or a computation expensive enough that you want to be explicit regardless of what the compiler infers.
- **Compiler not enabled**: manual memoization is still doing real work. Keep `useMemo` for expensive computations and `useCallback`/`React.memo` for props passed to expensive child components — just don't sprinkle them reflexively on everything, since unnecessary memoization adds overhead and complexity without helping.

## Testing

Use **React Testing Library** with Vitest (or Jest if the project already has it), testing behavior through the rendered DOM rather than component internals:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

test("submits the selected name", async () => {
  const onSelect = vi.fn();
  render(<UserCard name="Ada" role="Engineer" onSelect={onSelect} />);
  await userEvent.click(screen.getByRole("button"));
  expect(onSelect).toHaveBeenCalledWith("Ada");
});
```

Avoid `react-test-renderer` (deprecated) and shallow rendering (Enzyme-style) — both encourage testing implementation details instead of what the user actually experiences.
