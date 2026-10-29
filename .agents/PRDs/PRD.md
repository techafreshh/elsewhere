# PRD — ELSEWHERE

**Status:** Draft v1 · 2026-10-28
**Context:** Qloo Agentic Hackathon (Devpost) — submissions close **Oct 31, 2026, 4:45am WAT (GMT+1)**; judging Nov 2–5; winners announced Nov 5, 9:00pm WAT. Event: https://qloo.devpost.com/

---

## 1. Executive Summary

ELSEWHERE (*"Feel at home, elsewhere."*) is a taste-transplant agent for people who just landed in a new city. The user types what home feels like — "Asake, owambe parties, 2am shawarma" — and picks the city they just landed in. A generic LLM answers that prompt with TripAdvisor: top-10 lists, stereotypes, no reasons. ELSEWHERE answers with a *transplant*: a map of spots in the new city where people with overlapping taste actually gather, each with the reason attached and an affinity score grounded in the Qloo taste graph.

The product's centerpiece is an honest, side-by-side reveal. One screen shows **Tourist mode** (what a generic LLM says with the same words) next to **Transplant mode** (what the Qloo-grounded agent finds with the same words *plus* taste-graph signals). The same LLM writes both pane summaries — the honesty toggle shows the model slug to preempt "did you rig it?". A "Why Qloo won" strip exposes the cross-domain taste path the generic side couldn't see: *Asake → late-night Afrobeats crowds → this specific market rooftop*.

Built for the Qloo Agentic Hackathon, ELSEWHERE is a genuine agentic tool speaking **AG-UI 1.0**, the open agent↔UI protocol: the transplant loop (resolve → signal → recommend → explain) streams as standard agent events over SSE — every Qloo tool call visible — with a hand-rolled state-driven render on the frontend showing the reveal as the agent works (CopilotKit Tier 2 planned, not adopted). **MVP goal:** a publicly-hosted, judge-ready demo where tapping 4 taste chips and picking London produces a 5-card map transplant in <10s (cached) / <30s (cold), working even with the Qloo key removed via a server-side warmed cache — live through Nov 5 judging.

## 2. Mission

**Mission statement:** Make any new city feel like home on day one, by moving your taste — not just your luggage.

**Core principles:**
1. **Taste is identity, not preference** — recommendations carry reasons ("why *you*"), never bare listings.
2. **Honest comparison** — the same model, same words, both panes; only the evidence differs. The demo never hides its baseline.
3. **Grounded, never vibes** — every transplant card cites its entity chain (seed → tag → place) from the Qloo graph; freeform LLM guessing is the other pane, not this one.
4. **The demo cannot 500** — seeded pairs, server-side cache, key-removed fallback, template-copy fallback for LLM outages.
5. **Cultural intelligence is the differentiator** — if it would work the same without Qloo, it fails the premise (explicit hackathon rule).

## 3. Target Users

| Persona | Who | Technical comfort | Needs / pain points |
|---|---|---|---|
| **The Transplant** (primary product persona) | A Lagosian who just landed in London (or a Londoner in Lagos, a Nairobi in Toronto) | High — phones-first, chat-native | Generic travel tools flatten them into "Nigerian restaurants near me." They want the places where *their* people already are — night, price level, crowd, vibe included. |
| **The Judge** (primary demo persona) | Hackathon judge, 3–7 minutes per entry | High | Must grasp the premise in one screen, see Qloo's role without a video, and never hit a login, cold-start death, or error state. |
| **The Stage Demo** | The builder presenting live | — | Needs the 4 seeded pairs frozen in cache, a key-removed fallback drill already passed, and a narrative ("Google told me where tourists eat…"). |

## 4. MVP Scope

### In Scope

**Core functionality**
- [x] Prompt box ("what does home feel like?") + quick-fill taste chips (pre-resolved Qloo entity IDs; tap 3–5) + city picker (London, Toronto, NYC, Lagos, Nairobi preseeded + free search)
- [x] Free-text taste input: LLM extracts candidate names → `/search` resolves → **approval-card** confirmation pills before transplant
- [x] Transplant engine: Qloo `/v2/insights` for `urn:entity:place` (hero, 5 cards) + `urn:entity:brand` (kicker row), entity signals + `filter.location.query` + `feature.explainability=true`
- [x] Two-pane reveal: Tourist (generic list, no reasons) vs Transplant (Leaflet map + 5 cards: name, why-you line, affinity badge, taste-path chain, entity link)
- [x] Affinity badges computed from explainability scores ("2.4× your graph") with count-up animation
- [x] "Why Qloo won" strip: the taste path + a disclosure of the actual Qloo query (params only, never the key)
- [x] Follow-up refinement prompts ("more late-night" → `filter.hours`; "cheaper" → `filter.price_level`) that re-run the transplant live
- [x] Honesty toggle: "same model underneath" showing the model slug
- [x] 4 seeded demo pairs (Lagos→London hero, Lagos→Toronto, NYC→Lagos, Nairobi→Lagos) frozen in server-side cache
- [x] Taste card persists in localStorage; "transplanting *this you* → {city}" banner

**Technical**
- [x] FastAPI single service: serves built SPA + `/api/*` proxy (Qloo key never reaches the browser)
- [x] Mock Qloo mode (hand-written synthetic shapes, ours) used whenever `QLOO_API_KEY` is absent — build never blocks on the key
- [x] Server-side JSON cache keyed by (sorted seed IDs + city + refinements); key-removed run passes via warmed cache
- [x] Fallback ladder on empty results: drop audiences → drop tags → drop weakest seed; "broadened search" surfaced in UI
- [x] LLM outage fallback: template copy instead of 500
- [x] pytest: param building, fallback ladder, affinity math, cache behavior, mock end-to-end

**Integration**
- [x] Qloo hackathon API (`https://hackathon.api.qloo.com`, `X-Api-Key` header, GET insights)
- [x] OpenRouter (`deepseek/deepseek-v4.1-flash`, one model for both panes)
- [x] beUI component kit (adopted — installed via `npx shadcn add @beui/<slug>` into `frontend/src/components/*` with the `@/*` alias; `frontend/components.json` pins the `@beui` registry; warm editorial theme through beUI CSS-variable tokens in `index.css`)
- [x] **AG-UI 1.0 agent endpoint (Tier 1, committed):** FastAPI streams the transplant loop as AG-UI events over SSE — `TOOL_CALL_*` for `qloo_search` / `qloo_insights_place` / `qloo_insights_brand` / `summarize_panes`, `STATE_SNAPSHOT`/`STATE_DELTA` driving the reveal. Agent loop becomes protocol-visible for judges. Frontend consumes SSE via hand-rolled `streamAgui` client in `frontend/src/agui.ts`, permitted by §14 Tier-1 wording
- [ ] **CopilotKit + generative UI (Tier 2, not adopted):** Tier-1 state-driven render ships instead — no `@copilotkit/react-core`, no `HttpAgent`/`useCoAgent` binding; reveal renders from hand-rolled SSE consumer state

**Deployment**
- [x] Dockerfile + docker-compose.yml + Caddyfile for the user's VPS
- [x] Public HTTPS URL, live and free through Nov 5
- [x] README (≤10 setup steps), MIT LICENSE, `.env.example`

### Out of Scope
- [ ] Accounts / auth / social features / login of any kind
- [ ] Booking or reservations
- [ ] Entity types beyond `place` + `brand`
- [ ] Real-time Qloo streaming; multi-city comparison; native apps
- [ ] Demo videos (not required — the live demo is the artifact)
- [ ] Custom domain (VPS host default is fine if DNS isn't ready)

## 5. User Stories

1. **As a** Transplant, **I want to** tap "Asake", "owambe", "2am shawarma" and pick London, **so that** I get five specific spots where my crowd actually hangs — not "Top 10 Nigerian restaurants."
   *Example: hero seeded pair → Asake + Filmhouse IMAX + owambe + 2am shawarma → London; right pane shows 5 pinned places with badges; left shows the generic list.*
2. **As a** Transplant, **I want to** type "I love slow-burn Nollywood and quiet rooftops" **so that** I don't need to know which of my tastes exist in a database.
   *Example: free text → agent resolves candidates via `/search` → approval pills ("Nollywood film industry · 97% match") → confirm → transplant to Toronto.*
3. **As a** Transplant, **I want to** say "more late-night" or "cheaper" after a transplant **so that** I can steer the agent without restarting.
   *Example: follow-up prompt maps to `filter.hours` / `filter.price_level` and re-runs in <10s from cache or <30s cold.*
4. **As a** skeptic, **I want to** see Tourist mode next to Transplant mode with the same model named **so that** I trust the difference comes from Qloo's graph, not a rigged prompt.
   *Example: honesty toggle reveals "both panes: deepseek/deepseek-v4.1-flash".*
5. **As a** Transplant in a reverse scenario, **I want to** transplant *into* Lagos and Nairobi **so that** I can see this is a transplant engine, not a Lagos listicle.
   *Example: NYC→Lagos and Nairobi→Lagos seeded pairs pass identically.*
6. **As a** Judge, **I want to** click one seeded-pair button and get the full reveal in <10s **so that** I can evaluate the whole product in one screenful with zero setup.
7. **As a** Judge, **I want to** expand "Why Qloo won" **so that** I can see the cross-domain taste path and the actual query the generic side had no access to.
8. **As the** builder on stage, **I want the** app to serve all 4 seeded pairs with the Qloo key removed **so that** a network/key failure can never kill the demo.

**Technical stories**
- **As the** operator, **I want** the Qloo key in `.env` only, proxied server-side, never logged or committed, **so that** the public repo stays clean and the key stays live through judging.
- **As the** operator, **I want** a `scripts/seed_cache.py` run on the VPS with the real key **so that** frozen fixtures exist on the server without Qloo response data ever entering git.

## 6. Core Architecture & Patterns

Single service. FastAPI serves the built SPA and proxies Qloo — the browser never sees the key.

```
Browser (React SPA, hand-rolled custom components + hand-rolled SSE consumer)
   │  POST /agui (SSE: AG-UI events)      POST /api/transplant (fallback, cache mode)
   ▼
FastAPI ──► agent.py  ──► qloo.py ──► hackathon.api.qloo.com  (X-Api-Key header)
   │   │        │                    ├─ /search        (names → entity IDs)
   │   │        │                    ├─ /v2/tags       (keywords → tag IDs)
   │   │        │                    └─ /v2/insights   (place hero + brand kicker)
   │   │        └── summarizer.py ──► OpenRouter (deepseek/deepseek-v4.1-flash, ×2 calls)
   │   ├──► agui.py — wraps agent.py, emits AG-UI 1.0 events (RUN_*, TOOL_CALL_*, STATE_*, TEXT_MESSAGE_*, RUN_FINISHED)
   │   ├──► cache.py (JSON file; hit <10s / cold <30s; mock mode when key absent)
   └──► static/ (built Vite SPA)
```

**Key patterns**
- **Agent loop as protocol, not vibes:** one agent implementation (`agent.py`), two transports — a generator of AG-UI events (`POST /agui`, SSE) and the plain `POST /api/transplant` JSON response. The UI prefers streaming; the JSON endpoint is the demo-safe fallback and what cache/seed mode serves.
- **Generative UI (as-built: hand-rolled state-driven render, Tier 2 not adopted):** the reveal is rendered *from agent state*, progressively — chips resolve as `qloo_search` returns, pins drop on the Leaflet map as `qloo_insights_place` streams in, panes fill when `summarize_panes` completes.
- **Mock-first:** `qloo.py` returns hand-written synthetic fixtures (committed; clearly ours, not Qloo data) when `QLOO_API_KEY` is missing — identical response shapes, so the key is a config flip, not a code change.
- **Cache-first:** every transplant result is cached by a hash of (sorted seed IDs + city + refinements). Seeded pairs are pre-warmed on the server. Cache directory is gitignored — **no Qloo response data is ever committed**.
- **Fallback ladder:** empty Qloo results → drop audience signals → drop tag signals → drop weakest seed → report "broadened search" in the response payload.
- **Never-500:** LLM down → template copy; Qloo down → cache; cache cold + no key → seeded fixtures; streaming endpoint fails → JSON endpoint.
- **Affinity math:** `multiplier = entity.explainability ÷ median(explainability of result set)`, displayed as "2.4× your graph". Taste path = tag overlap between seed entities and result entity, rendered as a citation chain.

**Directory structure**
```
elsewhere/
├── backend/
│   ├── app/{main,qloo,agent,agui,summarizer,cache,catalog}.py
│   ├── data/{chips,cities,seed_pairs}.json      # config: label→entity-ID, cities, 4 pairs
│   ├── mocks/                                    # synthetic shapes (ours)
│   ├── scripts/seed_cache.py                     # run on VPS with real key
│   └── tests/
├── frontend/
│   └── src/{App,promptbox,reveal,panes,map,cards,whystrip}.tsx
│   └── src/components/{motion,agents}               # shadcn-installed beUI components (`@/*` alias)
├── Dockerfile  docker-compose.yml  Caddyfile
├── .env.example  README.md  LICENSE (MIT)
└── data/cache/                                   # gitignored; warmed on server
```

## 7. Tools / Features

### 7.1 Transplant agent (backend loop)
| Step | What happens | Failure behavior |
|---|---|---|
| Parse | Chips = pre-resolved IDs (zero latency). Free text → DeepSeek extracts candidate names | No candidates → ask to pick chips |
| Resolve | `/search` per candidate; approval pills for ambiguous matches | Unresolved → dropped with visible note |
| Signal | Build `signal.interests.entities`, optional tags/audiences, `filter.location.query=<city>`, `feature.explainability=true` | Invalid params silently ignored by Qloo → verify + log |
| Recommend | `GET /v2/insights` ×2 (`place` take≈8→pick 5; `brand` take≈5) | Fallback ladder; "broadened search" flag |
| Explain | Affinity multipliers, taste-path chains, why-you lines | Missing tags → summary-only card |
| Summarize | Two DeepSeek calls (tourist prompt = raw words; transplant prompt = words + Qloo evidence); JSON: summary + per-card why-you | Template copy fallback |

### 7.2 Frontend — beUI components (adopted)
| UI element | beUI component (as-built) |
|---|---|
| Prompt box (hero) | `agents/prompt-input` — send/stop bound to the existing submit; `id="prompt-input"`, label, placeholder and value ride the underlying textarea |
| Taste chips / card tokens | `motion/multi-select` — grouped Music/Food/Screens/Vibes options (`MultiSelectItem`, `data-entity-id`/`data-selected` on wrappers), removable tokens via `MultiSelectValue` (`data-token-remove`), localStorage |
| City picker | `motion/combobox` — preseeded cities + free search; beUI input/state with `id="city-input"` combobox semantics and the custom `#city-list`/`[data-city-option]` rows |
| Free-text confirmation pills | `agents/approval-card` — one card per candidate; `data-confirmation-text`/`data-confirmation-id` on wrappers, Confirm/Skip carry `data-confirm`/`data-skip`, `% match` label, empty-candidate note |
| Agent visibility while waiting | `agents/agent-activity` (+ `agents/loading-states` skeleton while streaming) — fed by real AG-UI tool-call events, not fake timers; `data-tool-call` contract rows kept alongside |
| Generative UI (progressive reveal) | state-driven render over the SSE stream (Tier-1 wording per §14; Tier 2 CopilotKit `useCopilotAction` renderers not adopted): cards + map pins appear as Qloo tool calls resolve |
| Transplant cards | `motion/card` (inside the `article.transplant-card` contract shell: `data-index`/`data-highlighted`/`data-entity-id`, highlight class, name/why-you/entity-id lines) |
| Affinity badge | `motion/animated-badge` + `motion/number` count-up (`.affinity-badge`, `N.N× your graph`, one decimal) |
| Entity-chain link ("why you") | `agents/citations` — seed → tag → spot chain kept as `.taste-path` text/aria-label plus a per-part citation list |
| "Why Qloo won" strip | `motion/collapsible` (trigger keeps `.why-toggle`) + `agents/tool-result` for the query (params only); body keeps `#why-qloo-body`/`.why-path`/`.why-query` |
| Refinement pills | `blocks/overflow-actions` — pills carry `data-refinement` + `aria-pressed` on their labels |
| Honesty toggle | `motion/switch` (native `role=switch` + `aria-checked`, `.honesty-line` keeps `both panes: …`) + `motion/tooltip` for the disclosure hint |
| Notices ("broadened search") | `motion/alert` (`role=alert`, broadened text) |
| Loading | `motion/loader` in `#transplant-submit` (id/disabled/`Transplanting…` kept) |
| Wordmark | `motion/text-animation` docs group, installed as `motion/text-reveal` (registry has no `text-animation` item) |
| Tourist pane list | deliberately plain styled rows (generic **by design**) |
| Map | Leaflet (custom; warm pins re-skinned through theme tokens — no beUI map exists) |

**As-built frontend note:** the shipped frontend renders every §7.2 mapped element on real beUI components installed via `npx shadcn add @beui/<slug>` into `frontend/src/components/{motion,agents}` (see `frontend/components.json` for the `@beui` registry pin and the `@/*` alias). Three registry slugs 404'd and were substituted with the closest same-category item, noted per row: `motion/text-animation` → `motion/text-reveal`, `motion/number` → `motion/animated-number`, `agents/loading-states` → the installed `loading-states/thinking-shimmer` file. The two legitimately-custom elements stay custom as planned: the Leaflet map and the deliberately-plain tourist rows. Install also pulls `lib/ease.ts` + `lib/hooks/*` helpers; `frontend/src/lib/utils.ts` (`cn`) is the shared merge helper. Warm editorial theme (cream `#F7F3EC`, ink, terracotta) is defined as beUI CSS-variable tokens in `index.css` (Tailwind v4 `@theme`). Tier-1 AG-UI 1.0 SSE is consumed by the hand-rolled `streamAgui` tolerant-normalizer client in `frontend/src/agui.ts` (never-throw, skip-malformed, per `backend/tests/test_agui.py` `_collect_events` split-on-`data:` framing); Tier-2 CopilotKit was not adopted. DOM contracts (`#prompt-input`, `#transplant-submit`, `#city-input` combobox semantics, chip/token/approval attributes, `#agent-activity` rows, `#tourist-pane`/`#transplant-pane` + summaries, refinement pills, `#notices`, `#brands`, `.transplant-card` attrs, `.affinity-badge` text, `.why-toggle`/`#why-qloo-body`/`.why-path`/`.why-query`, honesty `role=switch` + `both panes:` line, `.alert[role=alert]` broadened text, seeded-pair `from → city` labels, entity-id lines) are frozen.

### 7.3 Qloo API facts (load-bearing — implement exactly)
- Base URL `https://hackathon.api.qloo.com` (staging/prod 401). Auth: `X-Api-Key` header only. `/v2/insights` is **GET with query string**; always append a path. Never `/recommendations`.
- `filter.type` values: `urn:entity:place`, `urn:entity:brand` (restaurants = `place`/`brand`, not `food_and_drink`).
- Location: `filter.location.query=London` (fuzzy, 400 if no match) + optional `filter.location.radius` (meters). Signals need real IDs from `/search` or `/v2/tags`.
- `take` max 50; `sort_by=affinity` (default); `feature.explainability=true` → per-entity 0–1 scores; `filter.hours`, `filter.price_level` (1–4) for refinements.
- Invalid params → 200 + empty `entities` (silently ignored) — always validate.

## 8. Technology Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI, uvicorn, httpx | Single service; serves SPA + API |
| Store | JSON files (config) + JSON-file cache | SQLite unnecessary at this scale; cache dir gitignored |
| LLM | OpenRouter — `deepseek/deepseek-v4.1-flash` | One model, both panes; `OPENROUTER_MODEL` env-swappable |
| Frontend | Vite + React + TypeScript, Tailwind CSS v4 | shadcn-style CSS-variable theming (warm editorial: cream `#F7F3EC`, ink, terracotta) |
| Components | beUI kit via shadcn (`npx shadcn add @beui/<slug>` → `src/components/*`; `motion` animation library + `lucide-react` + `clsx`/`tailwind-merge` underneath) | Warm palette mapped onto beUI CSS-variable tokens (`--background:#F7F3EC`, ink, terracotta) in `index.css` |
| Agent protocol | **AG-UI 1.0** over SSE — backend emits hand-rolled ~10 event types; frontend consumes via hand-rolled `streamAgui` client in `frontend/src/agui.ts` (Tier-1 wording per §14) | Tier 2 (`@copilotkit/react-core`, `@ag-ui/client`) not adopted |
| Map | Leaflet (+ theme-styled tiles, warm pins) | Custom UI element alongside the deliberately-plain tourist rows; both stay custom as planned |
| Tests | pytest (backend) | Frontend: build + manual/browser e2e |
| Deploy | Docker + docker-compose + Caddy on user's VPS | Public HTTPS URL through Nov 5 |

## 9. Security & Configuration

- **No auth** (by design, out of scope). No user data leaves the browser except taste selections sent to our API.
- **Secrets:** `QLOO_API_KEY`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` in `.env` (gitignored; `.env.example` documents them). Key never logged, never sent to the browser, never committed. Hackathon rule: credentials must not be placed in source control.
- **No Qloo response data committed** to the public repo — cache lives on the server, warmed by `scripts/seed_cache.py`.
- **Proxy model:** all Qloo/OpenRouter calls server-side; browser talks only to `/api/*`.
- **Repo:** public GitHub, MIT LICENSE (must be detectable in About), README ≤10 steps, `.gitignore` covers `.env` + `data/cache/`.
- **Deployment:** VPS + Caddy (automatic HTTPS); Docker compose single service; must stay live, free, unrestricted through Nov 5.

## 10. API Specification (our backend)

### `POST /api/transplant`
```jsonc
// Request
{
  "seeds": [
    { "id": "urn:entity:artist:..." },            // pre-resolved chip
    { "text": "slow-burn Nollywood" }             // free text → resolved server-side
  ],
  "dest_city": "London",
  "refinements": ["late-night", "cheaper"]         // optional; map to filter.hours / price_level
}
```
```jsonc
// Response
{
  "tourist": { "summary": "…generic list summary…", "items": ["Top 10 Nigerian restaurants in London", "…"] },
  "transplant": {
    "summary": "…grounded summary…",
    "cards": [{
      "name": "Ridley's Market Roof", "address": "…", "latlng": [51.5, -0.1],
      "why_you": "Because Asake nights and 2am shawarma both live here",
      "affinity_multiplier": 2.4, "taste_path": ["Asake", "late-night Afrobeats", "Ridley's Market Roof"],
      "entity_id": "urn:entity:place:…"
    }],
    "brands": ["…kicker row…"],
    "map_center": [51.5, -0.1], "map_pins": [ { "latlng": [51.5,-0.1], "card_index": 0 } ]
  },
  "confirmations": [ { "text": "slow-burn Nollywood", "candidates": [{"name": "Nollywood", "entity_id": "…", "confidence": 0.97}] } ],
  "meta": { "model": "deepseek/deepseek-v4.1-flash", "cache_hit": true, "broadened": false, "qloo_mode": "live|mock|cache" }
}
```

### `GET /api/health` → `{ "qloo": true, "model": true, "mode": "live|mock" }`
### `GET /api/catalog` → chip catalog (label, category, entity_id) + cities + 4 seed pairs
### `GET /api/seed_pairs` → the 4 frozen pairs with pre-resolved seeds

### `POST /agui` (AG-UI 1.0 over SSE) — same request body as `/api/transplant`
Streams standard agent events while the transplant loop runs:
```
RUN_STARTED
  TOOL_CALL_START {name:"qloo_search"} → TOOL_CALL_ARGS → TOOL_CALL_END        (free-text seeds only)
  TOOL_CALL_START {name:"qloo_insights_place"} → … → TOOL_CALL_END             (result → STATE_DELTA: cards, pins)
  TOOL_CALL_START {name:"qloo_insights_brand"} → … → TOOL_CALL_END             (result → STATE_DELTA: brand kicker)
  TOOL_CALL_START {name:"summarize_panes"} → … → TOOL_CALL_END                 (result → STATE_DELTA: both summaries)
  TEXT_MESSAGE_CONTENT {delta:"…"}                                             (agent narration, optional)
RUN_FINISHED  {final state snapshot}
```
Errors stream as `RUN_ERROR`; the UI falls back to `POST /api/transplant`. The Qloo key never appears in any event — tool calls expose params only.


**Qloo calls (upstream)** — `GET https://hackathon.api.qloo.com/v2/insights?filter.type=urn:entity:place&signal.interests.entities=<ids>&filter.location.query=London&feature.explainability=true&take=8` (+ brand call, + optional `filter.hours`/`filter.price_level`).

## 11. Success Criteria

**MVP success = the hackathon deliverable is submittable and demo-safe.**
- [ ] End-to-end: 4 chips → London → map with 5 cards, each with why-you + affinity badge, **<10s cached / <30s cold**
- [ ] Tourist pane visibly generic on all 4 seeded pairs; Transplant pane visibly specific (named entities, numbers, map pins)
- [ ] All 4 seeded pairs pass **with the Qloo key removed** (server cache fallback)
- [ ] Free-text flow resolves candidates and shows approval pills before transplant
- [ ] Refinement prompts visibly change results
- [ ] Honesty toggle shows the shared model slug
- [ ] No secret in the repo; no Qloo response data committed; license detectable in About; README ≤10 steps
- [ ] Live URL reachable from a fresh browser, no login, through Nov 5
- [ ] Devpost submission complete before Oct 31, 4:45am WAT (description text drafted, draft saved early)

**Quality indicators:** zero 500s during a 10-minute judge session; every card explains itself; the same-model claim is visible in one click.

## 12. Implementation Phases

### Phase 1 — Mock-mode build (Day 1, Oct 28)
**Goal:** the whole product works end-to-end without the key.
- [ ] **Walking skeleton first:** repo scaffold (backend + frontend + Docker + Caddyfile + .env.example + LICENSE + .gitignore) with `GET /api/health` live in mock mode
- [ ] **Ship a CI workflow** (`.github/workflows/ci.yml`) running backend pytest + frontend build on every PR — delivered with the scaffold so the merge gate engages from lap 2
- [ ] `qloo.py` client + mock fixtures; `catalog` (chips/cities/seed pairs as config with placeholder IDs)
- [ ] Transplant agent, cache, summarizer (stubbed LLM if OpenRouter unverified) + pytest green
- [ ] `agui.py`: AG-UI 1.0 event endpoint (SSE) wrapping the same agent loop; hand-rolled `streamAgui` consumer in `frontend/src/agui.ts`; agent-activity fed by real events
- [x] Frontend: beUI components + warm-token theming (adopted); prompt box, chip tray, city picker, two panes, Leaflet map, badges, citation chains, Why-Qloo-won strip, refinements, honesty toggle
- **Validation:** hero scenario fully clickable in mock mode; `npm run build` + `pytest` green; all §7.2 mapped elements render from shadcn-installed beUI components; `frontend/components.json` present.

### Phase 2 — Live wiring (the hour the key lands, Oct 29)
**Goal:** real Qloo data end-to-end.
- [ ] Resolve chip catalog + free-text candidates via `/search`; fix any mock-shape mismatches
- [ ] Verify `/v2/insights` params live (location query, explainability, brand call)
- [ ] `scripts/seed_cache.py`: warm all 4 seeded pairs on the VPS; timing check (<10s cached)
- [ ] OpenRouter live check (DeepSeek both panes); template fallback verified
- [ ] Tier 2 (CopilotKit headless layer + generative-UI renderers for progressive card/pin reveal): not adopted — Tier-1 state-driven render ships instead
- **Validation:** hero pair on live data; fallback ladder exercised; key-removed drill passes.

### Phase 3 — Deploy + submit (Oct 30)
**Goal:** public URL + Devpost submitted well before the evening.
- [ ] Docker + Caddy on VPS; public HTTPS URL; fresh-browser smoke test (no login)
- [ ] Git secret scan; README (≤10 steps); repo public + MIT in About
- [ ] Devpost: description text (features + "why Qloo-powered"), testing instructions, draft saved early; **submit before the evening** (deadline 4:45am WAT Oct 31 — nothing ships at 4am)
- **Validation:** acceptance checklist §11 fully checked.

## 12.1 Operator checklist (NOT factory issues — laps cannot do these)
1. **Watch for the Qloo key** (spam folder); ping Discord #api-help if it slips past ~24h. When it lands: it goes in `.env` at deploy time only — never committed, never in factory config.
2. **Instabox prerequisite:** confirm the shared `factory-base-r2` snapshot exists VPS-side (it does if bi-ai-ui/tic-tac-toe ran recently); if missing, run `foreman bake elsewhere` VPS-side before the first lap.
3. **Deploy (Phase 3):** Docker + Caddy on the VPS, public HTTPS URL, run `backend/scripts/seed_cache.py` with the real key to warm the 4 seeded pairs, then the key-removed drill.
4. **OpenRouter key:** needed at deploy time for live pane summaries (`OPENROUTER_API_KEY` in VPS `.env`); factory laps and pytest run fine without it (stub/template fallback).
5. **Devpost:** save the draft early with the description text; submit well before Oct 31, 4:45am WAT. No demo video required.

## 13. Future Considerations (post-judging)
- Multi-city comparison ("London vs Toronto for *your* taste")
- Audience overlays ("where Lagos 25–34s gather in Toronto")
- Full `chat-app`-style conversation workspace on top of the transplant engine
- More entity types (music venues via `place` parents, artists as seeds), events/weekend modes
- Taste-card sharing links (still no accounts)

## 14. Risks & Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| **Qloo key delayed** | Cannot seed real caches / verify shapes | Mock-shape build continues (Phase 1 unaffected); #api-help escalation Day 2; seeded-cache design means a late key needs hours, not days |
| **Empty Qloo results** (thin coverage for Lagos/Nairobi) | Seeded pairs look empty | Fallback ladder (drop audiences → tags → weakest seed); broaden radius; log "broadened" visibly; worst case a pair's cache is warmed from the broadest valid query |
| **Quota/rate limits during judging** | 429s on the live demo | Generous hackathon quotas + #api-help bump path; all demo traffic absorbed by cache |
| **LLM outage/slow (OpenRouter)** | Panes empty or slow | Template-copy fallback; <30s budget with timeout; both panes degrade gracefully |
| **Deploy slips / VPS issues** | No public URL = disqualified | Repo ships Dockerfile+Caddyfile so any host works in minutes; fallback: workers.dev static + tiny proxy first, VPS second |
| **AG-UI/CopilotKit integration friction** | Lost time in a 3-day window | Tier 1 hand-rolls ~10 SSE event types (protocol is simple JSON) — no SDK dependency; `@ag-ui/client` optional (not installed; hand-rolled `streamAgui` ships instead); CopilotKit is Tier 2 only (not adopted); non-streaming JSON endpoint stays as fallback |

## 15. Appendix

**Key dependencies & links**
- Qloo dev guide: https://docs.qloo.com/reference/qloo-llm-hackathon-developer-guide · Parameter reference: https://docs.qloo.com/reference/parameters · Insights deep dive: https://docs.qloo.com/reference/insights-api-deep-dive
- Hackathon kit: https://github.com/qloo/qloo-hackathon-kit · Event: https://qloo.devpost.com/ · Discord #api-help: https://discord.gg/rF9PKsD5Q7 · Contact: ian@qloo.com
- beUI (adopted — install from `frontend/`: `npx shadcn add @beui/<slug>`): wordmark https://beui.dev/components/motion/text-animation · prompt box https://beui.dev/components/agents/prompt-input · chips https://beui.dev/components/motion/multi-select · city picker https://beui.dev/components/motion/combobox · approvals https://beui.dev/components/agents/approval-card · activity https://beui.dev/components/agents/agent-activity · loading states https://beui.dev/components/agents/loading-states · cards https://beui.dev/components/motion/card · badge https://beui.dev/components/motion/animated-badge · number https://beui.dev/components/motion/number · citations https://beui.dev/components/agents/citations · collapsible https://beui.dev/components/motion/collapsible · tool result https://beui.dev/components/agents/tool-result · refinements https://beui.dev/components/blocks/overflow-actions · switch https://beui.dev/components/motion/switch · tooltip https://beui.dev/components/motion/tooltip · alert https://beui.dev/components/motion/alert · loader https://beui.dev/components/motion/loader
- AG-UI 1.0 spec: https://docs.ag-ui.com/introduction · CopilotKit: https://github.com/copilotkit/copilotkit · Generative UI guide: https://www.copilotkit.ai/blog/the-developer-s-guide-to-generative-ui-in-2026
- OpenRouter: https://openrouter.ai

**Hackathon deliverables checklist**
- [ ] Functional, externally-hosted live demo URL (live/free through Nov 5)
- [ ] Public repo URL, MIT license detectable in About
- [ ] Text description (features + what makes it Qloo-powered)
- [ ] No login → no test credentials needed
- [ ] (No demo video required)

**Submission rules snapshot:** new work (Qloo integration post-Sept 30, 2026 — satisfied: project is fully new); frozen after deadline (save draft early, submit before cutoff); judging = Stage 1 pass/fail (theme + genuine API use), Stage 2 four equal criteria (Technological Implementation, Design, Potential Impact, Quality of the Idea).
