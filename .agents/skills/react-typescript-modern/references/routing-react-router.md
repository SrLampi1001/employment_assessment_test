# Routing: React Router v8

## The package changed shape

As of v8, `react-router-dom` no longer exists as a separate package. Everything ships from `react-router`, with browser-specific entry points split into `react-router/dom`:

```tsx
// Core routing — components, hooks, data APIs
import { Routes, Route, Outlet, useNavigate, useParams, Link } from "react-router";

// Browser-specific entry points
import { BrowserRouter } from "react-router/dom";
// or, for data routers:
import { HydratedRouter } from "react-router/dom";
```

If you see `from 'react-router-dom'` in a codebase, that's either an older major (check `package.json` before "fixing" it) or a leftover that needs updating for a v8 project — don't assume, check.

React Router v8 is **ESM-only** and requires **Node 22.22+** and **React 19.2.7+**. If a project's toolchain doesn't meet that floor, it's on an older major — follow its conventions rather than v8 patterns.

## v5 muscle memory that's still floating around

React Router v5 has been gone for years, but its API keeps surfacing in generated code:

| v5 (gone) | Current |
|---|---|
| `<Switch>` | `<Routes>` |
| `useHistory()` | `useNavigate()` |
| `<Route component={Home} />` | `<Route element={<Home />} />` or `<Route Component={Home} />` |
| `<Route exact path="/">` | Exact matching is the default now — `exact` doesn't exist |

## Three modes — pick data mode unless there's a reason not to

React Router supports three ways of wiring up routes:

1. **Declarative** (`<BrowserRouter><Routes><Route>`) — routes rendered as JSX, no loaders/actions. Fine for a small app with no server data per route, or when embedding routing inside a component tree you don't control at the top level.
2. **Data mode** (`createBrowserRouter` + `<RouterProvider>`) — routes as data, with `loader`/`action`/`errorElement` per route. This is the default recommendation for anything beyond a trivial app, because it moves data-fetching out of `useEffect` and lets the router coordinate loading/error states across nested routes instead of every component managing its own.
3. **Framework mode** (file-based routing, SSR, via the React Router "framework" — formerly Remix) — only relevant if the project is explicitly built on it; don't introduce it into an existing SPA unprompted.

### Data mode example

```tsx
import { createBrowserRouter, RouterProvider } from "react-router";

const router = createBrowserRouter([
  {
    path: "/",
    Component: Layout,
    children: [
      { index: true, Component: Home },
      {
        path: "users/:userId",
        loader: async ({ params }) => fetchUser(params.userId!),
        Component: UserProfile,
        errorElement: <UserError />,
      },
    ],
  },
]);

function App() {
  return <RouterProvider router={router} />;
}

function UserProfile() {
  const user = useLoaderData(); // typed via the loader's return in TS setups using route typegen
  return <ProfileCard user={user} />;
}
```

If the project also uses TanStack Query, prefer wiring the loader through `queryClient.ensureQueryData(...)` and reading via `useSuspenseQuery` in the component, rather than a bare `fetch` in the loader — see the router-integration section in `references/data-fetching-react-query.md` to avoid fetching the same data twice.

## Middleware (stable by default in v8)

Middleware runs around loaders/actions for a route tree — the right place to centralize concerns like auth checks or logging instead of repeating a redirect check inside every route's loader:

```tsx
const requireAuth: Route.MiddlewareFunction = async ({ context }, next) => {
  if (!context.user) {
    throw redirect("/login");
  }
  return next();
};

const router = createBrowserRouter([
  {
    path: "/dashboard",
    middleware: [requireAuth],
    Component: Dashboard,
  },
]);
```

## Mutations: `<Form>`, actions, and `useFetcher`

For anything that mutates data, prefer the router's `<Form>` + `action` pair over a manual `onSubmit` handler that calls `fetch` and then imperatively navigates — the router handles the pending UI, revalidation of affected loaders, and error surfacing for you:

```tsx
{
  path: "users/:userId/edit",
  action: async ({ request, params }) => {
    const formData = await request.formData();
    await updateUser(params.userId!, formData);
    return redirect(`/users/${params.userId}`);
  },
  Component: EditUser,
}

function EditUser() {
  return (
    <Form method="post">
      <input name="displayName" />
      <button type="submit">Save</button>
    </Form>
  );
}
```

Use `useFetcher()` instead of `<Form>` when you need to submit without a full navigation — e.g. a "like" button in a list item that shouldn't change the URL or scroll position.

## When `useEffect` is still the right call for navigation-adjacent work

Loaders cover "data this route needs before it renders." Genuinely client-only, interaction-driven fetches (e.g. autocomplete suggestions as the user types) still belong in the component, generally via TanStack Query rather than a raw `useEffect`.
