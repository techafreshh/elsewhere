import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { motion } from "motion/react";
import { TextReveal } from "@/components/motion/text-reveal";
import { PromptInput } from "@/components/agents/prompt-input";
import {
  MultiSelect,
  MultiSelectItem,
  MultiSelectValue,
} from "@/components/motion/multi-select";
import { useMultiSelectContext } from "@/components/motion/multi-select/context";
import {
  Combobox,
  ComboboxInput,
  ComboboxItem,
} from "@/components/motion/combobox";
import { ApprovalCard } from "@/components/agents/approval-card";
import { AgentActivity } from "@/components/agents/agent-activity";
import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer";
import { Card } from "@/components/motion/card";
import { CitationList } from "@/components/agents/citations";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/motion/collapsible";
import {
  ToolResult,
  ToolResultOutput,
} from "@/components/agents/tool-result";
import { OverflowActions } from "@/components/motion/overflow-actions";
import { Switch } from "@/components/motion/switch";
import { Tooltip } from "@/components/motion/tooltip";
import { Alert, AlertContent, AlertIcon } from "@/components/motion/alert";
import { Loader } from "@/components/motion/loader";
import AffinityBadge from "./AffinityBadge.tsx";
import TransplantMap from "./TransplantMap.tsx";
import { streamAgui, type AguiActivity, type AguiEvent } from "./agui.ts";
import {
  CATEGORIES,
  FALLBACK_CHIPS,
  FALLBACK_CITIES,
  FALLBACK_SEED_PAIRS,
  loadTaste,
  saveTaste,
  type Chip,
  type SeedPair,
} from "./taste.ts";

interface CatalogResponse {
  chips?: Chip[];
  cities?: string[];
}

/** Keep only well-formed frozen pairs: non-empty `from_home`/`dest_city`
 *  plus a non-empty string `seed_ids` array. Drops anything else so one
 *  malformed entry never kills the quick-launch row. Never throws. */
function toSeedPairs(value: unknown): SeedPair[] {
  try {
    if (!Array.isArray(value)) return [];
    const out: SeedPair[] = [];
    for (const entry of value) {
      if (!entry || typeof entry !== "object") continue;
      const rec = entry as Record<string, unknown>;
      if (typeof rec.from_home !== "string" || !rec.from_home) continue;
      if (typeof rec.dest_city !== "string" || !rec.dest_city) continue;
      if (!Array.isArray(rec.seed_ids) || rec.seed_ids.length === 0) continue;
      if (
        !rec.seed_ids.every(
          (id): id is string => typeof id === "string" && id.length > 0,
        )
      )
        continue;
      out.push({
        from_home: rec.from_home,
        dest_city: rec.dest_city,
        seed_ids: [...rec.seed_ids],
      });
    }
    return out;
  } catch {
    return [];
  }
}

function isChipList(value: unknown): value is Chip[] {
  return (
    Array.isArray(value) &&
    value.length >= 12 &&
    value.every(
      (entry) =>
        typeof entry === "object" &&
        entry !== null &&
        typeof (entry as Chip).label === "string" &&
        typeof (entry as Chip).category === "string" &&
        typeof (entry as Chip).entity_id === "string",
    )
  );
}

interface TransplantCard {
  name: string;
  address?: string;
  latlng?: [number, number];
  why_you?: string;
  whyYou?: string;
  affinity_multiplier?: number;
  taste_path?: string[];
  entity_id?: string;
}

interface TransplantPane {
  summary: string;
  cards: TransplantCard[];
  brands: string[];
  map_center: [number, number];
  map_pins: { latlng: [number, number]; card_index: number }[];
}

interface WhyQlooInfo {
  pathSentence: string;
  query: string;
}

interface RevealPayload {
  tourist: { summary: string; items: string[] };
  transplant: TransplantPane;
  whyQloo?: WhyQlooInfo;
  model?: string;
  broadened?: boolean;
}

const DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash";

/** Normalize the why_qloo disclosure (params only, never the key). Never throws. */
function toWhyQloo(root: Record<string, unknown>, cards: TransplantCard[], city: string): WhyQlooInfo {
  let pathSentence = "";
  let query = "";
  try {
    const raw = root.why_qloo;
    if (raw && typeof raw === "object") {
      const rec = raw as Record<string, unknown>;
      if (typeof rec.taste_path_sentence === "string") pathSentence = rec.taste_path_sentence;
      else if (typeof rec.pathSentence === "string") pathSentence = rec.pathSentence;
      if (typeof rec.qloo_query === "string") query = rec.qloo_query;
      else if (typeof rec.query === "string") query = rec.query;
    }
    if (!pathSentence && typeof root.taste_path_sentence === "string") pathSentence = root.taste_path_sentence;
    if (!query && typeof root.qloo_query === "string") query = root.qloo_query;
  } catch {
    /* tolerant: fallbacks below fill the strip */
  }
  if (!pathSentence) {
    try {
      const first = cards[0]?.taste_path ?? [];
      const cleaned = first.filter((entry): entry is string => typeof entry === "string" && entry.length > 0);
      if (cleaned.length > 0) pathSentence = cleaned.join(" \u2192 ");
    } catch {
      /* ignore */
    }
  }
  if (!pathSentence) {
    const place = city.trim() || DEFAULT_CITY;
    const firstName = cards[0]?.name || `${place} transplant spot 1`;
    pathSentence = `home \u2192 your crowd \u2192 ${firstName}`;
  }
  if (!query) {
    const place = city.trim() || DEFAULT_CITY;
    query = `filter.type=urn:entity:place&filter.location.query=${encodeURIComponent(place)}&feature.explainability=true&take=8`;
  }
  return { pathSentence, query };
}

/** Normalize the honesty model slug. Never throws. */
function toHonestyModel(root: Record<string, unknown>): string {
  try {
    const raw = root.why_qloo;
    if (raw && typeof raw === "object") {
      const rec = raw as Record<string, unknown>;
      if (typeof rec.model === "string" && rec.model) return rec.model;
    }
    const meta = root.meta;
    if (meta && typeof meta === "object") {
      const rec = meta as Record<string, unknown>;
      if (typeof rec.model === "string" && rec.model) return rec.model;
    }
    if (typeof root.model === "string" && root.model) return root.model;
  } catch {
    /* fall through to default */
  }
  return DEFAULT_MODEL;
}

/** Normalize the broadened flag from meta or the flattened snapshot. Never throws. */
function toBroadened(root: Record<string, unknown>): boolean {
  try {
    const meta = root.meta;
    if (meta && typeof meta === "object") {
      const rec = meta as Record<string, unknown>;
      if (typeof rec.broadened === "boolean") return rec.broadened;
    }
    if (typeof root.broadened === "boolean") return root.broadened;
  } catch {
    /* default false */
  }
  return false;
}

/** Free-text approval candidate surfaced by resolve_seeds. Never throws. */
interface ConfirmationCandidate {
  name: string;
  entity_id: string;
  confidence?: number;
}

interface Confirmation {
  text: string;
  candidates: ConfirmationCandidate[];
}

/** Normalize the JSON `confirmations` envelope into pill-ready candidates.
 *  Drops candidates missing name/entity_id or not starting with
 *  `urn:entity:`; confidence coerces via Number() like agent.resolve_seeds
 *  and is omitted when non-numeric. Null on any mismatch. Never throws. */
function toConfirmations(data: unknown): Confirmation[] {
  try {
    if (!data || typeof data !== "object") return [];
    const raw = (data as Record<string, unknown>).confirmations;
    if (!Array.isArray(raw)) return [];
    const out: Confirmation[] = [];
    for (const entry of raw) {
      if (!entry || typeof entry !== "object") continue;
      const rec = entry as Record<string, unknown>;
      if (typeof rec.text !== "string" || !rec.text) continue;
      const rawCands = Array.isArray(rec.candidates) ? rec.candidates : [];
      const candidates: ConfirmationCandidate[] = [];
      for (const cand of rawCands) {
        if (!cand || typeof cand !== "object") continue;
        const cref = cand as Record<string, unknown>;
        if (typeof cref.name !== "string" || !cref.name) continue;
        if (typeof cref.entity_id !== "string") continue;
        if (!cref.entity_id.startsWith("urn:entity:")) continue;
        const n = Number(cref.confidence);
        candidates.push(
          Number.isFinite(n)
            ? { name: cref.name, entity_id: cref.entity_id, confidence: n }
            : { name: cref.name, entity_id: cref.entity_id },
        );
      }
      out.push({ text: rec.text, candidates });
    }
    return out;
  } catch {
    return [];
  }
}

/** Normalize the JSON `notices` envelope into renderable lines.
 *  Non-string entries are dropped. Never throws. */
function toNotices(data: unknown): string[] {
  try {
    if (!data || typeof data !== "object") return [];
    const raw = (data as Record<string, unknown>).notices;
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (entry): entry is string => typeof entry === "string" && entry.length > 0,
    );
  } catch {
    return [];
  }
}

/** Coerce an unknown value into a [lat, lng] pair. Null on any mismatch. */
function toLatLng(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const [lat, lng] = value;
  if (typeof lat !== "number" || typeof lng !== "number") return null;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return [lat, lng];
}

/** Coerce an unknown multiplier to 1-decimal finite. Falls back to 1.0. */
function toAffinity(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return 1.0;
  return Math.round(n * 10) / 10;
}

/** Keep only non-empty strings from a taste_path candidate. Never throws. */
function toTastePath(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is string => typeof entry === "string" && entry.length > 0,
  );
}

const FALLBACK_CENTER: [number, number] = [51.5074, -0.1278];

/** Normalize transplant map fields; card latlngs backstop missing pins, the
 *  London fallback center backstops a missing center. Never throws. */
function toMapData(
  transplant: Record<string, unknown>,
  cards: TransplantCard[],
): { center: [number, number]; pins: { latlng: [number, number]; card_index: number }[] } {
  const center = toLatLng(transplant.map_center) ?? FALLBACK_CENTER;
  const rawPins = Array.isArray(transplant.map_pins) ? transplant.map_pins : [];
  const pins: { latlng: [number, number]; card_index: number }[] = [];
  rawPins.forEach((entry, i) => {
    if (!entry || typeof entry !== "object") return;
    const rec = entry as Record<string, unknown>;
    const latlng = toLatLng(rec.latlng);
    if (!latlng) return;
    pins.push({
      latlng,
      card_index: typeof rec.card_index === "number" ? rec.card_index : i,
    });
  });
  if (pins.length === 0) {
    cards.slice(0, 5).forEach((card, i) => {
      pins.push({
        latlng: card.latlng ?? [center[0] + 0.01 * i, center[1] + 0.012 * i],
        card_index: i,
      });
    });
  }
  return { center, pins: pins.slice(0, 5) };
}

/** Verbatim brand-kicker fallback (mirrors qloo.BRAND_MOCK) so the kicker row
 *  renders even when the API is unreachable. */
const FALLBACK_BRANDS: string[] = [
  "Eko Bite LDN",
  "Owambe Suya Co",
  "Danfo Kitchen Express",
  "Palm & Pine Grocers",
  "Nollywood Nights Pop-up",
];

type StatusPath = "idle" | "sse" | "json" | "template";

/** Wire seed: chip URNs travel as bare strings; typed prompt free text
 *  travels as `{text: ...}` so backend `parse_seeds` routes it to
 *  `resolve_text_seed` (bare strings are classified as chips and never
 *  yield `confirmations`). */
type Seed = string | { text: string };

/** Dedupe a mixed seed list: strings by trimmed value, `{text}` objects
 *  by folded text. Drops empties and anything else. Never throws. */
function dedupeSeeds(rawSeeds: unknown): Seed[] {
  try {
    if (!Array.isArray(rawSeeds)) return [];
    const seen = new Set<string>();
    const out: Seed[] = [];
    for (const seed of rawSeeds) {
      if (typeof seed === "string") {
        const trimmed = seed.trim();
        if (!trimmed || seen.has(trimmed)) continue;
        seen.add(trimmed);
        out.push(trimmed);
      } else if (seed && typeof seed === "object") {
        const text = (seed as Record<string, unknown>).text;
        if (typeof text !== "string") continue;
        const trimmed = text.trim();
        if (!trimmed) continue;
        const key = `text:${trimmed}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ text: trimmed });
      }
    }
    return out;
  } catch {
    return [];
  }
}

/** Hero London seed URNs from backend/data/seed_pairs.json — the submit
 *  fallback when no chips are picked so the hero pair works with the API down. */
const HERO_LONDON_SEEDS: string[] = [
  "urn:entity:artist:asake",
  "urn:entity:place:filmhouse-imax",
  "urn:entity:brand:owambe-catering",
  "urn:entity:brand:shawarma-2am",
];

const DEFAULT_CITY = "London";

const STATUS_COPY: Record<StatusPath, string> = {
  idle: "Pick tastes and a city, then transplant.",
  sse: "Transplanted via live SSE stream.",
  json: "Transplanted via live JSON.",
  template: "Offline template copy \u2014 the API is unreachable.",
};

/** Generic tourist rows — the Tourist pane is a plain generic list by design. */
function defaultTouristItems(city: string): string[] {
  const place = city.trim() || DEFAULT_CITY;
  return [
    `Top 10 restaurants in ${place}`,
    `Must-see sights in ${place}`,
    `Best nightlife in ${place}`,
  ];
}

/** Inlined template-copy floor (mirrors summarizer._template_copy semantics):
 *  Tourist summary carries capitalised ``Tourist``, the transplant summary
 *  carries lowercase ``transplant`` plus a Qloo-affinity grounding clause,
 *  and every card why_you starts with ``Because`` — so the panes render
 *  with zero network and the two summaries always differ. */
function templateCopy(city: string): RevealPayload {
  const place = city.trim() || DEFAULT_CITY;
  const cards: TransplantCard[] = [1, 2, 3, 4, 5].map((n) => ({
    name: `${place} transplant spot ${n}`,
    address: place,
    latlng: [FALLBACK_CENTER[0] + 0.01 * (n - 1), FALLBACK_CENTER[1] + 0.012 * (n - 1)],
    why_you: `Because home nights belong at ${place} transplant spot ${n}`,
    affinity_multiplier: 1.0,
    taste_path: ["home", "your crowd", `${place} transplant spot ${n}`],
    entity_id: `urn:entity:place:template-${n}`,
  }));
  const whyQloo: WhyQlooInfo = {
    pathSentence: ["home", "your crowd", `${place} transplant spot 1`].join(" \u2192 "),
    query: `filter.type=urn:entity:place&filter.location.query=${encodeURIComponent(place)}&feature.explainability=true&take=8`,
  };
  return {
    tourist: {
      summary: `Tourist take on ${place}: the usual top-10 list, no reasons attached.`,
      items: defaultTouristItems(place),
    },
    transplant: {
      summary: `Grounded transplant of home tastes to ${place}: spots where your crowd already gathers, backed by Qloo affinity signals.`,
      cards,
      brands: [...FALLBACK_BRANDS],
      map_center: [...FALLBACK_CENTER] as [number, number],
      map_pins: cards.map((card, i) => ({
        latlng: [...(card.latlng as [number, number])] as [number, number],
        card_index: i,
      })),
    },
    whyQloo,
    model: DEFAULT_MODEL,
    broadened: false,
  };
}

/** Normalize a transplant `cards` candidate into renderable cards (max 5).
 *  Shared by the strict final payload and the lenient progressive payload so
 *  partial deltas render exactly what the stream carried. Never throws. */
function toCardList(rawCards: unknown): TransplantCard[] {
  try {
    if (!Array.isArray(rawCards)) return [];
    const cards: TransplantCard[] = [];
    for (const entry of rawCards) {
      if (!entry || typeof entry !== "object") continue;
      const rec = entry as Record<string, unknown>;
      const name = typeof rec.name === "string" ? rec.name : "";
      const why =
        typeof rec.why_you === "string"
          ? rec.why_you
          : typeof rec.whyYou === "string"
            ? rec.whyYou
            : "";
      if (!name || !why) continue;
      const latlng = toLatLng(rec.latlng);
      cards.push({
        ...(rec as Partial<TransplantCard>),
        name,
        why_you: why,
        affinity_multiplier: toAffinity(rec.affinity_multiplier),
        taste_path: toTastePath(rec.taste_path),
        ...(latlng ? { latlng } : {}),
      });
      if (cards.length >= 5) break;
    }
    return cards;
  } catch {
    return [];
  }
}

/** Normalize any transplant-shaped JSON into the pane payload. Null on any
 *  mismatch — the caller renders template copy instead. Never throws. */
function toRevealPayload(data: unknown, city: string): RevealPayload | null {
  try {
    if (!data || typeof data !== "object") return null;
    const root = data as Record<string, unknown>;
    const touristRaw = root.tourist;
    const transplantRaw = root.transplant;
    if (!touristRaw || typeof touristRaw !== "object") return null;
    if (!transplantRaw || typeof transplantRaw !== "object") return null;
    const tourist = touristRaw as Record<string, unknown>;
    const transplant = transplantRaw as Record<string, unknown>;
    const touristSummary =
      typeof tourist.summary === "string" ? tourist.summary : "";
    const transplantSummary =
      typeof transplant.summary === "string" ? transplant.summary : "";
    const rawItems = Array.isArray(tourist.items) ? tourist.items : [];
    const picked = rawItems.filter(
      (entry): entry is string => typeof entry === "string" && entry.length > 0,
    );
    const items = [...picked, ...defaultTouristItems(city)].slice(
      0,
      Math.max(3, picked.length),
    );
    const cards = toCardList(transplant.cards);
    if (!touristSummary || !transplantSummary || cards.length < 5) return null;
    const trimmed = cards.slice(0, 5);
    const rawBrands = Array.isArray(transplant.brands) ? transplant.brands : [];
    const brands = rawBrands.filter(
      (entry): entry is string => typeof entry === "string" && entry.length > 0,
    );
    const mapData = toMapData(transplant, trimmed);
    const whyQloo = toWhyQloo(root, trimmed, city);
    const model = toHonestyModel(root);
    const broadened = toBroadened(root);
    return {
      tourist: { summary: touristSummary, items },
      transplant: {
        summary: transplantSummary,
        cards: trimmed,
        brands,
        map_center: mapData.center,
        map_pins: mapData.pins,
      },
      whyQloo,
      model,
      broadened,
    };
  } catch {
    return null;
  }
}

/** Accumulated STATE_DELTA/STATE_SNAPSHOT fragments for one /agui run.
 *  Progressive rendering merges each delta here, so the panes show exactly
 *  what the stream has carried so far. Never throws by construction. */
interface StreamAcc {
  cards: unknown;
  brands: unknown;
  center: unknown;
  pins: unknown;
  touristSummary: string;
  transplantSummary: string;
  root: Record<string, unknown>;
}

/** Lenient progressive payload: renders 1+ streamed cards with whatever
 *  summaries, brands, and map fragments have arrived, template copy filling
 *  the gaps. Null only when no renderable card has arrived yet. Never throws. */
function toProgressivePayload(acc: StreamAcc, city: string): RevealPayload | null {
  try {
    const cards = toCardList(acc.cards);
    if (cards.length === 0) return null;
    const place = city.trim() || DEFAULT_CITY;
    const base = templateCopy(place);
    const rawBrands = Array.isArray(acc.brands) ? acc.brands : [];
    const brands = rawBrands.filter(
      (entry): entry is string => typeof entry === "string" && entry.length > 0,
    );
    const mapData = toMapData(
      { map_center: acc.center, map_pins: acc.pins },
      cards,
    );
    return {
      tourist: {
        summary: acc.touristSummary || base.tourist.summary,
        items: defaultTouristItems(place),
      },
      transplant: {
        summary: acc.transplantSummary || base.transplant.summary,
        cards,
        brands: brands.length > 0 ? brands : base.transplant.brands,
        map_center: mapData.center,
        map_pins: mapData.pins,
      },
      whyQloo: toWhyQloo(acc.root, cards, place),
      model: toHonestyModel(acc.root),
      broadened: toBroadened(acc.root),
    };
  } catch {
    return null;
  }
}

/** Normalize a STATE_SNAPSHOT into the pane payload. Null on any mismatch
 *  so the caller falls through to the JSON path. Never throws. */
function toSnapshotPayload(
  snapshot: Record<string, unknown>,
  deltaTourist: string,
  deltaTransplant: string,
  city: string,
): RevealPayload | null {
  try {
    const rawTourist =
      typeof snapshot.tourist_summary === "string" && snapshot.tourist_summary
        ? snapshot.tourist_summary
        : deltaTourist;
    const rawTransplant =
      typeof snapshot.transplant_summary === "string" && snapshot.transplant_summary
        ? snapshot.transplant_summary
        : deltaTransplant;
    // The AG-UI snapshot already carries the spatial proof and kicker row;
    // pass them through so the SSE path renders the map and brands too.
    const snap = snapshot as Record<string, unknown>;
    // Carry the disclosure fields so the strip/toggle/alert render on SSE too.
    let whyRaw: unknown = snap.why_qloo ?? null;
    if (!whyRaw && (typeof snap.qloo_query === "string" || typeof snap.taste_path_sentence === "string")) {
      whyRaw = {
        taste_path_sentence: snap.taste_path_sentence,
        qloo_query: snap.qloo_query,
        model: snap.model,
      };
    }
    let metaRaw: unknown = snap.meta ?? null;
    if (!metaRaw && (typeof snap.model === "string" || typeof snap.broadened === "boolean")) {
      metaRaw = { model: snap.model, broadened: snap.broadened };
    } else if (metaRaw && typeof metaRaw === "object") {
      const rec = { ...(metaRaw as Record<string, unknown>) };
      if (typeof snap.broadened === "boolean" && typeof rec.broadened !== "boolean") rec.broadened = snap.broadened;
      if (typeof snap.model === "string" && typeof rec.model !== "string") rec.model = snap.model;
      metaRaw = rec;
    }
    return toRevealPayload(
      {
        tourist: { summary: rawTourist, items: defaultTouristItems(city) },
        transplant: {
          summary: rawTransplant,
          cards: snap.cards,
          brands: snap.brands,
          map_center: snap.map_center,
          map_pins: snap.map_pins ?? snap.pins,
        },
        why_qloo: whyRaw,
        meta: metaRaw,
        broadened: snap.broadened,
        model: snap.model,
      },
      city,
    );
  } catch {
    return null;
  }
}

/** Removable taste token label rendered inside beUI MultiSelectValue: carries
 *  the `data-token-remove` contract attribute on the clickable element itself
 *  and removes through the beUI multi-select state. Never throws. */
function TokenLabel({ value, label }: { value: string; label: string }) {
  const { remove } = useMultiSelectContext("TokenLabel");
  return (
    <span
      data-token-remove={value}
      role="button"
      tabIndex={0}
      aria-label={`Remove ${label}`}
      onClick={(e) => {
        e.stopPropagation();
        remove(value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          remove(value);
        }
      }}
    >
      {label} <span aria-hidden="true">×</span>
    </span>
  );
}

export default function App() {
  const [initial] = useState(loadTaste);
  const [chips, setChips] = useState<Chip[]>(FALLBACK_CHIPS);
  const [cities, setCities] = useState<string[]>(FALLBACK_CITIES);
  const [seedPairs, setSeedPairs] = useState<SeedPair[]>(FALLBACK_SEED_PAIRS);
  const [selected, setSelected] = useState<string[]>(initial.chips);
  const [city, setCity] = useState<string>(
    () => initial.city || FALLBACK_CITIES[0],
  );
  const [prompt, setPrompt] = useState<string>(initial.prompt);
  const [cityOpen, setCityOpen] = useState(false);
  const [cityQuery, setCityQuery] = useState("");
  const [highlight, setHighlight] = useState(-1);
  const [reveal, setReveal] = useState<RevealPayload | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [confirmations, setConfirmations] = useState<Confirmation[]>([]);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [refinements, setRefinements] = useState<string[]>([]);
  const [notices, setNotices] = useState<string[]>([]);
  const lastSeedsRef = useRef<Seed[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [activities, setActivities] = useState<AguiActivity[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const [statusPath, setStatusPath] = useState<StatusPath>("idle");
  const [whyOpen, setWhyOpen] = useState(false);
  const [honestyOn, setHonestyOn] = useState(false);
  const queryRef = useRef("");
  const blurTimer = useRef<number | null>(null);

  // Live catalog first, inlined snapshot as the offline fallback.
  useEffect(() => {
    let live = true;
    fetch("/api/catalog")
      .then((res) => {
        if (!res.ok) throw new Error(`catalog ${res.status}`);
        return res.json() as Promise<CatalogResponse>;
      })
      .then((data) => {
        if (!live) return;
        if (isChipList(data.chips)) setChips(data.chips);
        if (Array.isArray(data.cities) && data.cities.length > 0) {
          const names = data.cities.filter(
            (entry): entry is string =>
              typeof entry === "string" && entry.length > 0,
          );
          if (names.length > 0) {
            setCities(names);
            setCity((prev) => prev || names[0]);
          }
        }
      })
      .catch(() => {
        /* offline or API down: the fallback snapshot stays rendered */
      });
    fetch("/api/seed_pairs")
      .then((res) => {
        if (!res.ok) throw new Error(`seed_pairs ${res.status}`);
        return res.json() as Promise<unknown>;
      })
      .then((data) => {
        if (!live) return;
        const pairs = toSeedPairs(data);
        if (pairs.length > 0) setSeedPairs(pairs);
      })
      .catch(() => {
        /* offline or API down: the fallback snapshot stays rendered */
      });
    return () => {
      live = false;
    };
  }, []);

  // Persist every taste change so a reload restores chips, city, and prompt.
  useEffect(() => {
    saveTaste({ chips: selected, city, prompt });
  }, [selected, city, prompt]);

  // Never leak the combobox blur-close timer.
  useEffect(
    () => () => {
      if (blurTimer.current !== null) {
        window.clearTimeout(blurTimer.current);
      }
    },
    [],
  );

  // Parameterised submit core: Tier-1 state-driven POST /agui SSE stream
  // (3s optimistic abort while idle, 25s once frames flow), then the POST
  // /api/transplant JSON fallback, then the inlined template-copy floor.
  // Confirmations plus notices only ride the JSON envelope (the AG-UI stream
  // never emits them), so the JSON endpoint is fetched on every submit: it
  // adopts the cards when SSE produced nothing and always refreshes the
  // approval pills and the notices line. TOOL_CALL_START/END rows and
  // STATE_DELTA progressive reveals dispatch as frames arrive; a forced 500,
  // RUN_ERROR, abort, or null final payload silently abandons the stream for
  // the JSON ladder. Never rejects and never surfaces a 500 — every path
  // ends in panes.
  const runSubmit = async (opts?: {
    seeds?: Seed[];
    refinements?: string[];
    preserveConfirmations?: boolean;
    city?: string;
  }) => {
    if (submitting) return;
    setSubmitting(true);
    // Protocol-visible waiting state: empty panes plus a live activity feed
    // before the first byte arrives, so TOOL_CALL_START rows land within 1s.
    const acc: StreamAcc = {
      cards: undefined,
      brands: undefined,
      center: undefined,
      pins: undefined,
      touristSummary: "",
      transplantSummary: "",
      root: {},
    };
    setActivities([]);
    setStreaming(true);
    setReveal(null);
    setRevealed(true);
    const destCity = (opts?.city ?? city).trim() || DEFAULT_CITY;
    const rawSeeds = opts?.seeds ?? lastSeedsRef.current;
    const deduped = dedupeSeeds(rawSeeds);
    const seeds: Seed[] = deduped.length > 0 ? deduped : HERO_LONDON_SEEDS;
    lastSeedsRef.current = seeds;
    const effRefs = opts?.refinements ?? refinements;
    const body = JSON.stringify({ seeds, dest_city: destCity, refinements: effRefs });
    let payload: RevealPayload | null = null;
    let path: StatusPath = "json";
    // Dispatch one streamed AG-UI event into activity + progressive state.
    // Throws on RUN_ERROR so the fallback ladder owns the failure. Never logs.
    const applyAguiEvent = (event: AguiEvent) => {
      const type = event.type;
      if (type === "TOOL_CALL_START") {
        const toolCallId =
          typeof event.toolCallId === "string" ? event.toolCallId : "";
        const toolCallName =
          typeof event.toolCallName === "string" ? event.toolCallName : "";
        if (!toolCallName) return;
        setActivities((prev) => {
          if (
            toolCallId &&
            prev.some((entry) => entry.toolCallId === toolCallId)
          )
            return prev;
          if (
            !toolCallId &&
            prev.some(
              (entry) => entry.toolCallName === toolCallName && !entry.done,
            )
          )
            return prev;
          return [
            ...prev,
            {
              toolCallId: toolCallId || `${toolCallName}-${prev.length}`,
              toolCallName,
              phase: "start" as const,
              done: false,
            },
          ];
        });
        return;
      }
      if (type === "TOOL_CALL_END") {
        const toolCallId =
          typeof event.toolCallId === "string" ? event.toolCallId : "";
        if (!toolCallId) return;
        setActivities((prev) =>
          prev.map((entry) =>
            entry.toolCallId === toolCallId
              ? { ...entry, phase: "end" as const, done: true }
              : entry,
          ),
        );
        return;
      }
      if (type === "STATE_DELTA" && event.delta && typeof event.delta === "object") {
        const delta = event.delta as Record<string, unknown>;
        if (Array.isArray(delta.cards)) acc.cards = delta.cards;
        if (Array.isArray(delta.brands)) acc.brands = delta.brands;
        if (delta.map_center !== undefined) acc.center = delta.map_center;
        if (delta.map_pins !== undefined) acc.pins = delta.map_pins;
        else if (delta.pins !== undefined) acc.pins = delta.pins;
        if (typeof delta.tourist_summary === "string" && delta.tourist_summary) {
          acc.touristSummary = delta.tourist_summary;
        }
        if (typeof delta.transplant_summary === "string" && delta.transplant_summary) {
          acc.transplantSummary = delta.transplant_summary;
        }
        const nestedTourist = delta.tourist;
        if (nestedTourist && typeof nestedTourist === "object") {
          const summary = (nestedTourist as Record<string, unknown>).summary;
          if (typeof summary === "string" && summary) acc.touristSummary = summary;
        }
        const nestedTransplant = delta.transplant;
        if (nestedTransplant && typeof nestedTransplant === "object") {
          const summary = (nestedTransplant as Record<string, unknown>).summary;
          if (typeof summary === "string" && summary) acc.transplantSummary = summary;
        }
        for (const key of [
          "why_qloo",
          "meta",
          "qloo_query",
          "taste_path_sentence",
          "model",
          "broadened",
        ]) {
          if (delta[key] !== undefined) acc.root[key] = delta[key];
        }
        // Progressive reveal: render exactly what this delta carried.
        const partial = toProgressivePayload(acc, destCity);
        if (partial) setReveal(partial);
        return;
      }
      if (type === "STATE_SNAPSHOT" && event.snapshot && typeof event.snapshot === "object") {
        const snapshot = event.snapshot as Record<string, unknown>;
        if (Array.isArray(snapshot.cards)) acc.cards = snapshot.cards;
        if (Array.isArray(snapshot.brands)) acc.brands = snapshot.brands;
        if (snapshot.map_center !== undefined) acc.center = snapshot.map_center;
        if (snapshot.map_pins !== undefined) acc.pins = snapshot.map_pins;
        else if (snapshot.pins !== undefined) acc.pins = snapshot.pins;
        if (typeof snapshot.tourist_summary === "string" && snapshot.tourist_summary) {
          acc.touristSummary = snapshot.tourist_summary;
        }
        if (typeof snapshot.transplant_summary === "string" && snapshot.transplant_summary) {
          acc.transplantSummary = snapshot.transplant_summary;
        }
        for (const key of [
          "why_qloo",
          "meta",
          "qloo_query",
          "taste_path_sentence",
          "model",
          "broadened",
          "city",
        ]) {
          if (snapshot[key] !== undefined) acc.root[key] = snapshot[key];
        }
        const full = toSnapshotPayload(
          snapshot,
          acc.touristSummary,
          acc.transplantSummary,
          destCity,
        );
        if (full) {
          payload = full;
          path = "sse";
          setReveal(full);
        }
        return;
      }
      if (type === "RUN_FINISHED") {
        const result = event.result;
        if (!payload && result && typeof result === "object") {
          const normalized = toRevealPayload(result, destCity);
          if (normalized) {
            payload = normalized;
            path = "sse";
          }
        }
        if (!payload) {
          const progressive = toProgressivePayload(acc, destCity);
          if (progressive && progressive.transplant.cards.length >= 5) {
            payload = progressive;
            path = "sse";
          }
        }
        if (payload) setReveal(payload);
        return;
      }
      if (type === "RUN_ERROR") {
        throw new Error("agui run error");
      }
    };
    try {
      const controller = new AbortController();
      // Optimistic abort while the stream is idle; once frames flow the run
      // gets a full completion budget so slow streams still finish live.
      let live = false;
      let longTimer: number | undefined;
      const idleTimer = window.setTimeout(() => controller.abort(), 3000);
      const markLive = () => {
        if (!live) {
          live = true;
          window.clearTimeout(idleTimer);
          longTimer = window.setTimeout(() => controller.abort(), 25000);
        }
      };
      try {
        const sseRes = await fetch("/agui", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body,
          signal: controller.signal,
        });
        if (!sseRes.ok) throw new Error(`agui ${sseRes.status}`);
        await streamAgui(sseRes, (event) => {
          markLive();
          applyAguiEvent(event);
        });
        if (!payload) throw new Error("agui produced no payload");
      } finally {
        window.clearTimeout(idleTimer);
        if (longTimer !== undefined) window.clearTimeout(longTimer);
      }
    } catch {
      /* SSE unavailable — the JSON fallback below handles it */
    }
    let jsonData: unknown = null;
    try {
      const res = await fetch("/api/transplant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (res.ok) jsonData = (await res.json()) as unknown;
    } catch {
      jsonData = null;
    }
    if (!payload && jsonData) {
      payload = toRevealPayload(jsonData, destCity);
      if (payload) path = "json";
    }
    if (jsonData) {
      // Refinement re-runs reuse resolved seeds, so the fresh envelope
      // carries no confirmations — keep the pills the prompt submit showed.
      if (!opts?.preserveConfirmations) {
        setConfirmations(toConfirmations(jsonData));
        setDismissed([]);
      }
      setNotices(toNotices(jsonData));
    } else if (!opts?.preserveConfirmations) {
      setConfirmations([]);
      setNotices([]);
    }
    if (!payload) {
      payload = templateCopy(destCity);
      path = "template";
    }
    setReveal(payload);
    setRevealed(true);
    setStatusPath(path);
    setHighlighted(-1);
    setStreaming(false);
    setSubmitting(false);
  };

  // Hero Transplant button: prompt free text joins the seed set as a
  // `{text}` object (deduped) so typed tastes reach `resolve_text_seed`
  // and come back as `confirmations`; bare strings would classify as
  // chips and yield none. Refinements reset for the fresh search.
  // The existing saveTaste effect persists prompt unchanged.
  const onSubmit = async () => {
    const text = prompt.trim();
    const seeds: Seed[] = dedupeSeeds([
      ...selected,
      ...(text ? [{ text }] : []),
    ]);
    setRefinements([]);
    await runSubmit({ seeds, refinements: [] });
  };

  // Seeded-pair quick launch: one click installs exactly that frozen pair's
  // destination city plus seed set, clears refinements, and submits the full
  // reveal. The city override rides in opts because setCity has not flushed
  // yet when runSubmit reads the destination for the request body.
  const onSeedPair = async (pair: SeedPair) => {
    setCity(pair.dest_city);
    setRefinements([]);
    await runSubmit({
      seeds: [...pair.seed_ids],
      refinements: [],
      city: pair.dest_city,
    });
  };

  // Approval pills: Confirm resolves the candidate into the seed set and
  // re-runs with resolved IDs; Skip dismisses that candidate pill only
  // and never blocks submit.
  const confirmCandidate = (entityId: string) => {
    const next = selected.includes(entityId) ? selected : [...selected, entityId];
    setSelected(next);
    void runSubmit({ seeds: next, refinements });
  };

  const skipCandidate = (entityId: string) => {
    setDismissed((prev) => (prev.includes(entityId) ? prev : [...prev, entityId]));
  };

  // Refinement pills: toggle the key, then re-submit the last resolved
  // seeds plus city with the new refinements so the server cache_key hits
  // and cards visibly re-render (or the notices line explains why).
  const toggleRefinement = (key: string) => {
    const next = refinements.includes(key)
      ? refinements.filter((entry) => entry !== key)
      : [...refinements, key];
    setRefinements(next);
    void runSubmit({
      seeds: lastSeedsRef.current,
      refinements: next,
      preserveConfirmations: true,
    });
  };

  // Spatial proof on first paint: run the hero transplant automatically so the
  // Leaflet map, its 5 pins, and the brand kicker row render with no gesture.
  useEffect(() => {
    void onSubmit();
    // Single first-paint run; later renders go through the submit button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Pin → card linkage: highlight the matching card and bring it into view,
  // satisfying either acceptance branch (highlight class or scroll).
  const onPinClick = (index: number) => {
    setHighlighted(index);
    requestAnimationFrame(() => {
      document
        .querySelector(`.transplant-card[data-index="${index}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  };

  const cardNames = useMemo(
    () => (reveal?.transplant.cards ?? []).map((card) => card.name),
    [reveal],
  );

  const grouped = useMemo(
    () =>
      CATEGORIES.map((category) => ({
        category,
        items: chips.filter((chip) => chip.category === category),
      })),
    [chips],
  );

  const selectedChips = useMemo(
    () =>
      selected.flatMap((entityId) => {
        const chip = chips.find((entry) => entry.entity_id === entityId);
        return chip ? [chip] : [];
      }),
    [selected, chips],
  );

  const trimmedQuery = cityQuery.trim();
  const loweredQuery = trimmedQuery.toLowerCase();
  const filteredCities = useMemo(() => {
    if (!loweredQuery) return cities;
    return cities.filter((name) =>
      name.toLowerCase().includes(loweredQuery),
    );
  }, [cities, loweredQuery]);
  const hasExactMatch =
    trimmedQuery.length > 0 &&
    cities.some((name) => name.toLowerCase() === loweredQuery);

  const openCityList = () => {
    if (blurTimer.current !== null) {
      window.clearTimeout(blurTimer.current);
      blurTimer.current = null;
    }
    queryRef.current = "";
    setCityQuery("");
    setHighlight(-1);
    setCityOpen(true);
  };

  const chooseCity = (value: string) => {
    const next = value.trim();
    if (next) setCity(next);
    queryRef.current = "";
    setCityQuery("");
    setCityOpen(false);
    setHighlight(-1);
  };

  const onCityBlur = () => {
    if (blurTimer.current !== null) window.clearTimeout(blurTimer.current);
    blurTimer.current = window.setTimeout(() => {
      // Free search: a typed value left in the box becomes the city.
      const pending = queryRef.current.trim();
      if (pending) setCity(pending);
      queryRef.current = "";
      setCityQuery("");
      setCityOpen(false);
      setHighlight(-1);
      blurTimer.current = null;
    }, 120);
  };

  const onCityKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCityOpen(true);
      setHighlight((prev) =>
        filteredCities.length === 0
          ? -1
          : (prev + 1) % filteredCities.length,
      );
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((prev) =>
        filteredCities.length === 0
          ? -1
          : (prev - 1 + filteredCities.length) % filteredCities.length,
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (
        cityOpen &&
        highlight >= 0 &&
        highlight < filteredCities.length
      ) {
        chooseCity(filteredCities[highlight]);
      } else if (trimmedQuery) {
        chooseCity(trimmedQuery);
      } else {
        setCityOpen(false);
      }
    } else if (e.key === "Escape") {
      setCityOpen(false);
      setHighlight(-1);
    }
  };

  // Closed combobox shows the chosen city; an open one shows the live query.
  const visibleCities = cityOpen ? filteredCities : cities;

  return (
    <main className="hero-shell">
      <header className="hero-header">
        <h1>
          <TextReveal text="ELSEWHERE" />
        </h1>
        <p>Feel at home, elsewhere.</p>
      </header>

      <motion.div
        id="taste-banner"
        role="status"
        className="taste-banner"
        initial={{ opacity: 0.4 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.2 }}
      >
        {city
          ? `transplanting *this you* → ${city}`
          : "transplanting *this you* → pick a city"}
      </motion.div>

      <section id="hero" aria-label="Transplant your taste">
        <div className="field">
          <label htmlFor="prompt-input">What does home feel like?</label>
          <PromptInput
            id="prompt-input"
            value={prompt}
            onValueChange={setPrompt}
            onSubmit={() => void onSubmit()}
            loading={submitting}
            placeholder="What does home feel like? e.g. Asake, owambe parties, 2am shawarma"
            aria-label="What does home feel like?"
          />
        </div>

        {confirmations.some(
          (entry) =>
            entry.candidates.length === 0 ||
            entry.candidates.some((cand) => !dismissed.includes(cand.entity_id)),
        ) && (
          <div id="approval-pills" aria-live="polite">
            {confirmations.flatMap((entry) =>
              entry.candidates.length === 0
                ? [
                    <p key={`empty-${entry.text}`} className="approval-empty">
                      No match for &ldquo;{entry.text}&rdquo; — try chips
                    </p>,
                  ]
                : entry.candidates
                    .filter((cand) => !dismissed.includes(cand.entity_id))
                    .map((cand) => {
                      const pct =
                        typeof cand.confidence === "number" &&
                        Number.isFinite(cand.confidence)
                          ? Math.round(cand.confidence * 100)
                          : null;
                      return (
                        <span
                          key={cand.entity_id}
                          className="approval-pill"
                          data-confirmation-text={entry.text}
                          data-confirmation-id={cand.entity_id}
                        >
                          <ApprovalCard
                            title={cand.name}
                            description={
                              pct === null
                                ? `${cand.name} · match`
                                : `${cand.name} · ${pct}% match`
                            }
                            className="approval-beui"
                          >
                            <span className="approval-actions">
                              <button
                                type="button"
                                data-confirm="true"
                                onClick={() => confirmCandidate(cand.entity_id)}
                              >
                                Confirm
                              </button>
                              <button
                                type="button"
                                data-skip="true"
                                onClick={() => skipCandidate(cand.entity_id)}
                              >
                                Skip
                              </button>
                            </span>
                          </ApprovalCard>
                        </span>
                      );
                    }),
            )}
          </div>
        )}

        <MultiSelect value={selected} onValueChange={setSelected}>
        <div className="field">
          <h2 id="chips-heading">Taste chips</h2>
          <p className="hint">Tap 3–5 tastes that feel like home.</p>
          <div id="selected-tokens" aria-live="polite">
            {selectedChips.length === 0 ? (
              <span className="tokens-empty">No tastes picked yet.</span>
            ) : (
              <>
                <MultiSelectValue chipClassName="token">
                  {(value, label) => (
                    <TokenLabel value={value} label={label} />
                  )}
                </MultiSelectValue>
                <button
                  type="button"
                  className="tokens-clear"
                  onClick={() => setSelected([])}
                >
                  Clear
                </button>
              </>
            )}
          </div>
          <div id="chips" aria-labelledby="chips-heading">
            {grouped.map((group) => (
              <div
                key={group.category}
                className="chip-group"
                data-chip-group={group.category}
              >
                <h3>{group.category}</h3>
                <div role="group" aria-label={group.category}>
                  {group.items.map((chip) => {
                    const isOn = selected.includes(chip.entity_id);
                    return (
                      <span
                        key={chip.entity_id}
                        data-entity-id={chip.entity_id}
                        data-selected={isOn ? "true" : "false"}
                        aria-pressed={isOn}
                      >
                        <MultiSelectItem
                          value={chip.entity_id}
                          textValue={chip.label}
                          keywords={[chip.category]}
                          className={isOn ? "chip chip-on" : "chip"}
                        >
                          {chip.label}
                        </MultiSelectItem>
                      </span>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
        </MultiSelect>

        <Combobox
          open={cityOpen}
          onOpenChange={(next) => {
            if (next) {
              setCityOpen(true);
              return;
            }
            // Mirror the blur-commit: a typed value left in the box becomes
            // the city. Reads the pre-clear render value because the provider
            // clears its query (scheduled) just before this fires.
            const pending = cityQuery.trim();
            if (pending) setCity(pending);
            queryRef.current = "";
            setCityQuery("");
            setCityOpen(false);
            setHighlight(-1);
          }}
          query={cityQuery}
          onQueryChange={(next) => {
            queryRef.current = next;
            setCityQuery(next);
            setHighlight(-1);
          }}
          value={city}
          onValueChange={(next) => chooseCity(next)}
        >
        <div className="field combobox-field">
          <label htmlFor="city-input">City</label>
          <ComboboxInput
            id="city-input"
            ref={(el) => {
              // ComboboxInput stamps its own generated id/aria-controls after
              // the props spread, so restore the frozen contract attributes
              // (label htmlFor + combobox semantics point at #city-list).
              if (el) {
                el.id = "city-input";
                el.setAttribute("aria-controls", "city-list");
              }
            }}
            aria-label="City"
            autoComplete="off"
            placeholder="Pick a city… e.g. London"
            onFocus={openCityList}
            onBlur={onCityBlur}
            onKeyDown={onCityKeyDown}
            className="city-beui"
          />
          {/* Hidden option registrations so the closed input shows the city
              label; the visible list below stays custom for free search.
              The current city gets its own registration when free-typed. */}
          <span aria-hidden="true" hidden>
            {cities.map((name) => (
              <ComboboxItem key={name} value={name} textValue={name}>
                {name}
              </ComboboxItem>
            ))}
            {city && !cities.includes(city) && (
              <ComboboxItem key={city} value={city} textValue={city}>
                {city}
              </ComboboxItem>
            )}
          </span>
          <ul
            id="city-list"
            role="listbox"
            aria-label="Cities"
            hidden={!cityOpen}
          >
            {visibleCities.map((name, index) => (
              <li
                key={name}
                id={`city-option-${name}`}
                role="option"
                aria-selected={name === city}
                data-city-option={name}
                data-highlighted={index === highlight ? "true" : "false"}
                className={
                  index === highlight ? "city-option hot" : "city-option"
                }
                onMouseDown={(e) => {
                  e.preventDefault();
                  chooseCity(name);
                }}
              >
                {name}
              </li>
            ))}
            {cityOpen && trimmedQuery && !hasExactMatch && (
              <li
                role="option"
                aria-selected="false"
                data-city-option-free="true"
                className="city-option free"
                onMouseDown={(e) => {
                  e.preventDefault();
                  chooseCity(trimmedQuery);
                }}
              >
                Use &ldquo;{trimmedQuery}&rdquo;
              </li>
            )}
          </ul>
        </div>
        </Combobox>

        {seedPairs.length > 0 && (
          <div id="seed-pairs" role="group" aria-label="Seeded pairs">
            {seedPairs.map((pair) => (
              <button
                key={`${pair.from_home}-${pair.dest_city}`}
                type="button"
                className="seed-pair-btn"
                data-seed-pair={pair.dest_city}
                data-from-home={pair.from_home}
                onClick={() => onSeedPair(pair)}
              >
                {pair.from_home}{" \u2192 "}{pair.dest_city}
              </button>
            ))}
          </div>
        )}

        <div className="field">
          <button
            id="transplant-submit"
            type="button"
            onClick={onSubmit}
            disabled={submitting}
          >
            {submitting ? (
              <>
                <Loader variant="dots" size={16} label="Transplanting" />
                {"Transplanting\u2026"}
              </>
            ) : (
              "Transplant me"
            )}
          </button>
          <p id="transplant-status" role="status" data-status={statusPath}>
            {STATUS_COPY[statusPath]}
          </p>
        </div>
      </section>

      <section id="reveal" aria-label="Tourist versus transplant" hidden={!revealed}>
        <div id="agent-activity" aria-live="polite" data-stream-reader="streamAgui">
          <p className="agent-activity-head">
            Agent activity{streaming ? " · live" : ""}
          </p>
          <AgentActivity
            items={activities.map((activity) => ({
              id: activity.toolCallId,
              type: "tool",
              action: "run",
              target: activity.toolCallName,
            }))}
            status={streaming ? "working" : "complete"}
            contentType="tool"
            collapseOnComplete={false}
            activeLabel="Running transplant tools…"
            className="agent-activity-beui"
          />
          {streaming && activities.length === 0 && (
            <ThinkingShimmer>Contacting the transplant agent…</ThinkingShimmer>
          )}
          {activities.map((activity) => (
            <div
              key={activity.toolCallId}
              className="agent-activity-row"
              data-tool-call={activity.toolCallName}
            >
              <span className="agent-activity-name">{activity.toolCallName}</span>
              <span className="agent-activity-state">
                {activity.done ? "done" : "running"}
              </span>
            </div>
          ))}
        </div>
        <div id="tourist-pane">
          <h2>Tourist</h2>
          <p id="tourist-summary">{reveal?.tourist.summary ?? ""}</p>
          <ul>
            {(reveal?.tourist.items ?? []).map((item, index) => (
              <li key={`${index}-${item}`} className="tourist-row">
                {item}
              </li>
            ))}
          </ul>
        </div>
        <div id="transplant-pane">
          <h2>Transplant</h2>
          <p id="transplant-summary">{reveal?.transplant.summary ?? ""}</p>
          <div id="refinement-pills" role="group" aria-label="Refinements">
            <OverflowActions
              primaryActions={[
                {
                  id: "late-night",
                  label: (
                    <span
                      data-refinement="late-night"
                      aria-pressed={refinements.includes("late-night")}
                    >
                      more late-night
                    </span>
                  ),
                  onClick: () => toggleRefinement("late-night"),
                },
                {
                  id: "cheaper",
                  label: (
                    <span
                      data-refinement="cheaper"
                      aria-pressed={refinements.includes("cheaper")}
                    >
                      cheaper
                    </span>
                  ),
                  onClick: () => toggleRefinement("cheaper"),
                },
              ]}
              overflowActions={[]}
              classNames={{ toggle: "refinement-overflow-toggle" }}
            />
          </div>
          <p id="notices">{notices.join(" ")}</p>
          {reveal && (
            <TransplantMap
              center={reveal.transplant.map_center}
              pins={reveal.transplant.map_pins}
              names={cardNames}
              onPinClick={onPinClick}
            />
          )}
          <div id="brands" data-testid="brand-kicker-row" aria-label="Brand kickers">
            {(reveal?.transplant.brands ?? []).map((brand) => (
              <span key={brand} className="brand-kicker">
                {brand}
              </span>
            ))}
          </div>
          {reveal && (
            <Collapsible open={whyOpen} onOpenChange={setWhyOpen} className="why-strip">
              <CollapsibleTrigger className="why-toggle">
                Why Qloo won
              </CollapsibleTrigger>
              {whyOpen && (
                <CollapsibleContent>
                  <div id="why-qloo-body" className="why-body">
                    <p className="why-path">
                      {(reveal.whyQloo?.pathSentence ||
                        (reveal.transplant.cards[0]?.taste_path ?? []).filter(
                          (entry): entry is string => typeof entry === "string" && entry.length > 0,
                        ).join(" \u2192 ") ||
                        `${city.trim() || DEFAULT_CITY} \u2192 your crowd \u2192 ${(reveal.transplant.cards[0]?.name || city.trim() || DEFAULT_CITY)}`)}
                    </p>
                    <ToolResult
                      tool="qloo/v2/insights"
                      title="Qloo query"
                      status="success"
                      kind="request"
                      contentClassName="why-query"
                    >
                      <ToolResultOutput language="text">
                        {(reveal.whyQloo?.query ||
                          `filter.type=urn:entity:place&filter.location.query=${encodeURIComponent(city.trim() || DEFAULT_CITY)}&feature.explainability=true&take=8`)}
                      </ToolResultOutput>
                    </ToolResult>
                  </div>
                </CollapsibleContent>
              )}
              <Tooltip content="Reveal that both panes share one model">
                <span className="honesty-wrap">
                  <Switch
                    checked={honestyOn}
                    onCheckedChange={setHonestyOn}
                    label="honesty check: same model?"
                  />
                </span>
              </Tooltip>
              {honestyOn && (
                <p className="honesty-line">both panes: {reveal.model ?? DEFAULT_MODEL}</p>
              )}
              {!!reveal.broadened && (
                <Alert variant="warning" role="alert" className="alert">
                  <AlertIcon />
                  <AlertContent>broadened search to fill results</AlertContent>
                </Alert>
              )}
            </Collapsible>
          )}
          <div className="transplant-grid">
            {(reveal?.transplant.cards ?? []).slice(0, 5).map((card, index) => {
              const chain = (card.taste_path ?? []).filter(
                (entry): entry is string =>
                  typeof entry === "string" && entry.length > 0,
              );
              const chainText = chain.join(" \u2192 ");
              return (
                <article
                  key={`${index}-${card.entity_id || card.name}`}
                  className={
                    highlighted === index
                      ? "transplant-card card--highlight"
                      : "transplant-card"
                  }
                  data-index={index}
                  data-highlighted={highlighted === index ? "true" : "false"}
                  data-entity-id={card.entity_id || `urn:entity:place:card-${index}`}
                >
                  <Card className="transplant-card-beui">
                    <h3>{card.name}</h3>
                    <AffinityBadge value={card.affinity_multiplier ?? 1.0} />
                    <p className="why-you">{card.why_you ?? card.whyYou ?? ""}</p>
                    <p className="taste-path" aria-label={chainText}>
                      {chainText}
                    </p>
                    {chain.length > 0 && (
                      <CitationList
                        citations={chain.map((part, partIndex) => ({
                          id: `${card.entity_id || `card-${index}`}-${partIndex}`,
                          title: part,
                        }))}
                        idPrefix={`taste-${index}`}
                        className="taste-cites"
                      />
                    )}
                    <p
                      className="entity-id"
                      data-testid="entity-id"
                    >
                      {card.entity_id || `urn:entity:place:card-${index}`}
                    </p>
                  </Card>
                </article>
              );
            })}
          </div>
        </div>
      </section>
    </main>
  );
}
