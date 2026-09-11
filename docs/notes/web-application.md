# The web application

Phase 5 adds the visual SignalForge console on top of the Phase 4 API. It is a separate application
that consumes the same HTTP and Server-Sent Events contracts any client would: it imports no Python,
reads no SQLite, and knows nothing about the engine beyond what the API publishes.

## Stack decision

**React 19, TypeScript 5.9, Vite 7, one router, and hand-written CSS.** No Next.js, no UI kit, no
state framework, no chart library, no Tailwind.

The reasoning, in the order it mattered:

- **Next.js was considered and rejected.** Its value is server-side rendering, routing conventions
  and a server runtime. SignalForge already has an application server in Starlette, this is a local
  tool behind loopback, and nothing here benefits from SSR. Adding Next would mean a second
  application server to run, explain and keep alive for what is a client of an existing API.
- **No state framework.** The application has no shared mutable state worth managing. Each screen
  loads what it needs, and the one genuinely live thing, the event stream, is a hook over
  `EventSource`. Two small hooks (`useResource`, `usePolledResource`) cover everything else in about
  seventy lines. Redux, Zustand or TanStack Query would each be more code than the problem.
- **No UI kit.** The look this project needs is dense operational tables, precise hairline borders
  and a restrained palette. Component libraries are built for a different look and would be fought
  with overrides. The whole design system is two CSS files and about 180 lines of tokens.
- **No chart library.** The only quantitative visuals are confidence bars and a metric grid. A bar is
  a `<span>` with a width and a `role="meter"`; importing a charting library for that would be
  absurd. If a real time series is ever plotted, inline SVG comes first.
- **No Tailwind.** Habit is not a reason. Semantic class names plus CSS custom properties keep the
  markup readable and put the design decisions in one place.

Runtime dependencies are exactly three: `react`, `react-dom`, `react-router-dom`. The production
bundle is about 102 KB gzipped.

## Layout

```
web/
  src/
    api/client.ts          the only place that calls fetch; typed, with normalised errors
    app/                   App, Shell, fromEvents (pure folds over the event stream)
    components/            primitives, Timeline, HypothesisBoard, EvidencePanel, ReportView, TraceView
    hooks/                 useApi, useSystem, useInvestigationStream
    pages/                 one per route
    styles/                tokens.css, app.css
    types/api.ts           TypeScript mirror of the HTTP contract
  tests/                   vitest suites plus fixtures captured from the real API
  e2e/                     two Playwright flows against the real stack
```

## Routes

| Route | Screen |
|---|---|
| `/incidents` | the open incident queue, and the landing screen |
| `/incidents/:incidentId` | one incident, and the launch control for an investigation |
| `/investigations` | investigations this API process has run |
| `/investigations/:investigationId` | the workspace: timeline, hypotheses, evidence, report, trace |
| `/mcp` | the MCP server catalogue |
| `/evaluations` | the deterministic benchmark |
| `/system` | environment and provider status |

## The investigation workspace

Three columns: state and budget on the left, the live timeline in the middle, hypotheses and the
evidence workbench on the right. Below 1400px the right column moves under the timeline; below
1100px everything stacks.

The timeline is the screen that has to explain the architecture without a caption, so each entry is
tagged by kind: **MCP TOOL**, **RESOURCE READ**, **PROVIDER STEP**, **HYPOTHESIS**, **VALIDATION**,
**REFUSED**. A tool call renders as an operational command with its arguments, its latency and the
evidence id it produced. A reviewer should be able to see, in a few seconds, that the provider is
deciding what to ask for and MCP is answering.

When the investigation finishes the workspace moves to the report. Every citation is a button; a
narrowed citation like `EVD-000004#DEP-0038` is displayed in full and opens `EVD-000004`.

## The event stream client

`useInvestigationStream` wraps `EventSource`, which already handles retries and re-sends
`Last-Event-ID` on reconnect, which is exactly the cursor the API replays from. The hook adds what
the browser does not: de-duplication by sequence so a replayed overlap never renders twice, ordered
accumulation, closing on the terminal event instead of reconnecting forever, and a visible
connection state so the UI can say "reconnecting" rather than freezing silently. If the stream dies
for good, the workspace says so and falls back to polling the summary endpoint.

## Security

- The browser never sees, stores, or asks for a credential. The only request body the application
  sends is `{incident_id, provider, budget_profile}`, and a test asserts that.
- Providers are chosen from what `/api/providers` reports; an unconfigured one is disabled with the
  server's reason shown, and a live provider is labelled **LIVE API** with the cost stated before the
  button is pressed.
- Evidence is rendered as escaped text inside `<pre>` and `<p>` elements. There is no
  `dangerouslySetInnerHTML` anywhere, and an ESLint rule fails the build if one appears.
- Hidden provider reasoning is not exposed, because the API never publishes it. The trace screen is
  labelled **Observable investigation trace**, and a test asserts the phrase "chain of thought"
  appears nowhere.
- In development Vite proxies `/api` to the API, so requests are same-origin and the API needs no
  CORS origin at all. The Phase 4 network boundary is unchanged.

## Accessibility

Semantic landmarks (`nav`, `main`), real buttons and links, table captions and `scope` on headers,
`role="tablist"` with `aria-selected` on the workspace tabs, `role="meter"` with values on confidence
bars, visually hidden labels on the search and filter controls, and `aria-current` on the active
navigation item. Status is never colour alone: every badge carries its word. The one animation, the
loading shimmer, is disabled under `prefers-reduced-motion`.

## Development

```bash
signalforge serve          # terminal 1: the API on 127.0.0.1:8765
npm --prefix web run dev   # terminal 2: the web application on 127.0.0.1:5173
```

```bash
npm --prefix web run typecheck   # tsc across app, config and e2e
npm --prefix web run lint
npm --prefix web run test        # vitest, against fixtures captured from the real API
npm --prefix web run build
npm --prefix web run e2e         # Playwright: starts the API and the frontend itself
```

## Testing

The unit suite uses fixtures in `web/tests/fixtures` that are **real responses captured from the
running API**, including the full 70-event stream of a scripted investigation. Mocking stops at the
network boundary, so every component under test runs the same client code the application runs, and
a backend contract change surfaces as a failing test rather than a silently wrong screen.

The two Playwright flows run against the real stack: the real API, the real MCP server, the scripted
provider and the built frontend. The first drives an investigation from the queue to a traceable
conclusion; the second checks the benchmark renders from the API. Both are in CI, and neither
contacts a vendor.
