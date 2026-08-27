# Data fetching: TanStack Query v5

`@tanstack/react-query` (formerly `react-query`) owns **server state** — anything that lives on a backend and needs fetching, caching, and background sync. It is not a replacement for `useState`/`useReducer`/Zustand/Context, which still own **client state** (a modal's open/closed flag, a theme toggle, form-field values before submission). Mixing the two — putting UI-only state into the query cache, or hand-rolling `useState` + `useEffect` for server data — is the most common source of tangled state management in React apps.

```
npm install @tanstack/react-query
npm install -D @tanstack/react-query-devtools
```

## Setup

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <YourApp />
    </QueryClientProvider>
  );
}
```

## Query key factories, not ad-hoc arrays

Scattering string/array query keys through the codebase makes cache invalidation unpredictable — you end up guessing which keys a mutation needs to invalidate. Centralize them per resource:

```tsx
export const userKeys = {
  all: ["users"] as const,
  lists: () => [...userKeys.all, "list"] as const,
  list: (filters: UserFilters) => [...userKeys.lists(), filters] as const,
  details: () => [...userKeys.all, "detail"] as const,
  detail: (id: string) => [...userKeys.details(), id] as const,
};
```

## `queryOptions()` — define once, use in three places

`queryOptions()` (v5) gives you a single, type-safe, reusable definition that works identically in `useQuery`, `useSuspenseQuery`, and a router loader's `ensureQueryData` — see the router-integration example below.

```tsx
import { queryOptions, useQuery } from "@tanstack/react-query";

function userQueryOptions(userId: string) {
  return queryOptions({
    queryKey: userKeys.detail(userId),
    queryFn: () => fetchUser(userId),
    staleTime: 5 * 60 * 1000, // 5 min — pick this per resource, not globally
  });
}

function UserProfile({ userId }: { userId: string }) {
  const { data: user, isPending, error } = useQuery(userQueryOptions(userId));

  if (isPending) return <Spinner />;
  if (error) return <ErrorMessage error={error} />;
  return <ProfileCard user={user} />;
}
```

Don't leave every query at the default `staleTime: 0` — that means "refetch on every mount and every window focus," which is rarely what you actually want. Set it per query based on how often that data actually changes: seconds for a live dashboard number, minutes for a user profile, much longer for something like a list of countries.

## What changed from the version most training data remembers (v3/v4)

| Old (v3/v4, `react-query`) | Current (v5, `@tanstack/react-query`) | Why |
|---|---|---|
| `useQuery({ queryFn, onSuccess, onError, onSettled })` | No query-level callbacks | Removed — see below for replacements |
| `cacheTime` | `gcTime` | Renamed for clarity ("garbage collection time") |
| `status === 'loading'` / `isLoading` as the primary "no data yet" flag | `isPending` | `status: 'loading'` was renamed to `'pending'`. `isLoading` still exists but is now a derived shorthand for `isPending && isFetching` (first-load specifically) — prefer `isPending` when you just mean "I have nothing to render yet" |
| `keepPreviousData: true` | `placeholderData: keepPreviousData` (import the `keepPreviousData` helper) | Boolean option replaced with an explicit placeholder-data strategy |
| `useErrorBoundary` | `throwOnError` | Renamed |

### Query-level callbacks are gone — here's what to use instead

`useQuery`/`useInfiniteQuery` no longer accept `onSuccess`/`onError`/`onSettled`. Reach for whichever of these actually matches what you were trying to do:

- **Side effect tied to the data itself** (e.g. sync fetched data into a form's default values): `useEffect` keyed on `data`.
- **Cross-cutting concern across many queries** (e.g. toast on any failed request, log all errors to Sentry): register it once on the `QueryClient`, not per-query.
  ```tsx
  const queryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => toast.error(error.message),
    }),
  });
  ```
- **Want failures to be handled by an Error Boundary instead of inline `error` state**: `throwOnError: true` on the query, paired with a `<ErrorBoundary>` above it.

**Mutations kept their callbacks** — `useMutation({ onSuccess, onError, onSettled })` is unchanged, because a mutation is an imperative, one-off action where "do this when it finishes" is exactly the right shape.

```tsx
const { mutate, isPending } = useMutation({
  mutationFn: updateUser,
  onSuccess: (updatedUser) => {
    queryClient.setQueryData(userKeys.detail(updatedUser.id), updatedUser);
  },
  onError: (error) => toast.error(error.message),
});
```

## Optimistic updates

```tsx
const { mutate } = useMutation({
  mutationFn: updateUser,
  onMutate: async (newUser) => {
    await queryClient.cancelQueries({ queryKey: userKeys.detail(newUser.id) });
    const previous = queryClient.getQueryData(userKeys.detail(newUser.id));
    queryClient.setQueryData(userKeys.detail(newUser.id), newUser);
    return { previous };
  },
  onError: (_err, newUser, context) => {
    // Roll back on failure
    queryClient.setQueryData(userKeys.detail(newUser.id), context?.previous);
  },
  onSettled: (_data, _err, newUser) => {
    queryClient.invalidateQueries({ queryKey: userKeys.detail(newUser.id) });
  },
});
```

## Pairing with React Router loaders (avoid double-fetching)

If the router is also fetching data (see `references/routing-react-router.md`), don't let both the loader and the component independently fetch the same resource. Use the same `queryOptions()` in both places: the loader ensures the cache is warm before the route renders, and the component reads from that same cache via `useSuspenseQuery` — one network request, not two.

```tsx
// route module
export const userRoute = {
  path: "users/:userId",
  loader: ({ params, context }) =>
    context.queryClient.ensureQueryData(userQueryOptions(params.userId)),
  Component: UserRouteComponent,
};

function UserRouteComponent() {
  const { userId } = useParams();
  const { data: user } = useSuspenseQuery(userQueryOptions(userId!));
  return <ProfileCard user={user} />;
}
```

## Devtools

Add `<ReactQueryDevtools />` inside `QueryClientProvider` during development — it visualizes cache state, staleness, and refetch activity, and is usually the fastest way to debug "why is this refetching" or "why is this stale" questions instead of guessing.
