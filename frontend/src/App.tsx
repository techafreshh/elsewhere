import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  ArrowUp,
  Check,
  ChevronDown,
  Copy,
  PanelLeft,
  PanelLeftClose,
  Plane,
  PlusCircle,
  Square,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { TextReveal } from "@/components/motion/text-reveal";
import { PromptInput } from "@/components/agents/prompt-input";
import type { AgentActivityItem } from "@/components/agents/agent-activity/types";
import { Card } from "@/components/motion/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/motion/collapsible";
import { Switch } from "@/components/motion/switch";
import { Tooltip } from "@/components/motion/tooltip";
import { Loader } from "@/components/motion/loader";
import { ThemeToggle } from "@/components/motion/theme-toggle";
import AffinityBadge from "./AffinityBadge.tsx";
import TransplantMap from "./TransplantMap.tsx";
import { streamAgui, type AguiEvent } from "./agui.ts";
import {
  CATEGORIES,
  FALLBACK_CHIPS,
  FALLBACK_CITIES,
  SESSION_KEY,
  loadTaste,
  saveTaste,
  hasSession,
  loadSession,
  saveSession,
  type Chip,
  type CityEntry,
  type TasteSession,
} from "./taste.ts";

interface CatalogResponse {
  chips?: Chip[];
  cities?: Array<string | CityEntry>;
}

/** Combobox entry: `value` is the wire city string, `label` prepends the
 *  country for world-city context ("Berlin, Germany"). */
interface CityOption {
  label: string;
  value: string;
}

function toCityOptions(rawCities: Array<string | CityEntry>): CityOption[] {
  return rawCities.map((entry) => {
    if (typeof entry === "string") return { label: entry, value: entry };
    const name = typeof entry.name === "string" && entry.name ? entry.name : "";
    const country =
      typeof entry.country === "string" && entry.country ? `, ${entry.country}` : "";
    return { label: name ? `${name}${country}` : name, value: name };
  });
}

/** A search/related taste suggestion, always a real Qloo entity id. */
interface TasteHit {
  label: string;
  entity_id: string;
  type: string;
}

/** Lenient hits normalizer for /api/search_tastes and /api/related. */
function toTasteHits(value: unknown): TasteHit[] {
  if (!Array.isArray(value)) return [];
  const hits: TasteHit[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const rec = entry as Record<string, unknown>;
    if (typeof rec.label !== "string" || !rec.label.trim()) continue;
    if (typeof rec.entity_id !== "string" || !rec.entity_id.trim()) continue;
    hits.push({
      label: rec.label.trim(),
      entity_id: rec.entity_id.trim(),
      type: typeof rec.type === "string" ? rec.type : "",
    });
  }
  return hits;
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

interface WhyQlooInfo {
  tastePath: string;
  qlooQuery: string;
  model: string;
}

interface RevealPayload {
  tourist: { summary: string; items: string[] };
  transplant: {
    summary: string;
    cards: TransplantCard[];
    brands: string[];
    map_center: [number, number];
    map_pins: { latlng: [number, number]; card_index: number }[];
  };
  whyQloo: WhyQlooInfo;
  model: string;
  broadened: boolean;
}

interface ConfirmationCandidate {
  name: string;
  entity_id: string;
  confidence?: number;
}

interface Confirmation {
  text: string;
  candidates: ConfirmationCandidate[];
}

/** Coerce an unknown value into a [lat, lng] pair. Null on any mismatch. */
function toLatLng(value: unknown): [number, number] | null {
  if (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === "number" &&
    typeof value[1] === "number" &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  ) {
    return [value[0], value[1]];
  }
  return null;
}

/** Coerce an unknown multiplier to a 1-decimal finite number. Falls back to 1.0. */
function toAffinity(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(parsed) && parsed > 0) return Math.round(parsed * 10) / 10;
  return 1.0;
}

/** Keep only non-empty strings from a taste_path candidate. Never throws. */
function toTastePath(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is string => typeof entry === "string" && entry.length > 0,
  );
}

const FALLBACK_CENTER: [number, number] = [51.5074, -0.1278];

/** Normalize transplant map fields; card latlngs backstop missing pins. */
function toMapData(
  transplant: {
    map_center?: unknown;
    map_pins?: unknown;
  },
  cards: TransplantCard[],
): { center: [number, number]; pins: { latlng: [number, number]; card_index: number }[] } {
  const center = toLatLng(transplant.map_center) ?? FALLBACK_CENTER;
  const rawPins = Array.isArray(transplant.map_pins) ? transplant.map_pins : [];
  const pins: { latlng: [number, number]; card_index: number }[] = [];
  for (const pin of rawPins) {
    if (!pin || typeof pin !== "object") continue;
    const record = pin as Record<string, unknown>;
    const latlng = toLatLng(record.latlng);
    if (!latlng) continue;
    const index =
      typeof record.card_index === "number" && Number.isInteger(record.card_index)
        ? record.card_index
        : pins.length;
    pins.push({ latlng, card_index: index });
  }
  if (pins.length === 0) {
    cards.forEach((card, index) => {
      const latlng = toLatLng(card.latlng);
      if (latlng) pins.push({ latlng, card_index: index });
    });
  }
  return { center, pins };
}

/** Normalize the why_qloo disclosure (params only, never the key). Never throws. */
function toWhyQloo(
  root: Record<string, unknown>,
  cards: TransplantCard[],
  city: string,
): WhyQlooInfo {
  const raw =
    root.why_qloo && typeof root.why_qloo === "object"
      ? (root.why_qloo as Record<string, unknown>)
      : {};
  const model =
    typeof raw.model === "string" && raw.model ? raw.model : DEFAULT_MODEL;
  const sentence =
    typeof raw.taste_path_sentence === "string" ? raw.taste_path_sentence : "";
  const query = typeof raw.qloo_query === "string" ? raw.qloo_query : "";
  let tastePath = sentence;
  if (!tastePath) {
    const head = (cards[0]?.taste_path ?? []).filter(Boolean).slice(0, 2);
    const tail = cards[0]?.name || city;
    tastePath = [...head, tail].join(" \u2192 ");
  }
  const fallbackQuery =
    "filter.type=urn:entity:place&filter.location.query=" +
    encodeURIComponent(city || "London") +
    "&feature.explainability=true&take=8";
  return { tastePath, qlooQuery: query || fallbackQuery, model };
}

/** Normalize the honesty model slug. Never throws. */
function toHonestyModel(root: Record<string, unknown>): string {
  const meta = root.meta;
  if (meta && typeof meta === "object") {
    const model = (meta as Record<string, unknown>).model;
    if (typeof model === "string" && model) return model;
  }
  if (typeof root.model === "string" && root.model) return root.model;
  return DEFAULT_MODEL;
}

/** Normalize the broadened flag from meta or the flattened snapshot. Never throws. */
function toBroadened(root: Record<string, unknown>): boolean {
  const meta = root.meta;
  if (meta && typeof meta === "object") {
    if ((meta as Record<string, unknown>).broadened === true) return true;
  }
  return root.broadened === true;
}

/** Free-text approval candidate surfaced by resolve_seeds. Never throws. */
function toConfirmationCandidate(value: unknown): ConfirmationCandidate | null {
  if (!value || typeof value !== "object") return null;
  const rec = value as Record<string, unknown>;
  if (typeof rec.name !== "string" || !rec.name) return null;
  // Live Qloo ids are bare UUIDs; mock fixtures use urn:entity:* URNs.
  const entityId = typeof rec.entity_id === "string" ? rec.entity_id : "";
  if (!/^(urn:entity:|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$)/.test(entityId)) {
    return null;
  }
  const confidence =
    typeof rec.confidence === "number" && Number.isFinite(rec.confidence)
      ? rec.confidence
      : undefined;
  return {
    name: rec.name as string,
    entity_id: entityId,
    ...(confidence !== undefined ? { confidence } : {}),
  };
}

/** Normalize the JSON `confirmations` envelope into pill-ready candidates. */
function toConfirmations(data: unknown): Confirmation[] {
  try {
    if (!Array.isArray(data)) return [];
    const out: Confirmation[] = [];
    for (const entry of data) {
      if (!entry || typeof entry !== "object") continue;
      const rec = entry as Record<string, unknown>;
      if (typeof rec.text !== "string" || !rec.text) continue;
      const rawCandidates = Array.isArray(rec.candidates) ? rec.candidates : [];
      const candidates = rawCandidates
        .map(toConfirmationCandidate)
        .filter((c): c is ConfirmationCandidate => c !== null);
      out.push({ text: rec.text, candidates });
    }
    return out.filter((confirmation) => confirmation.candidates.length > 0);
  } catch {
    return [];
  }
}

/** Normalize the JSON `notices` envelope into renderable lines. */
function toNotices(data: unknown): string[] {
  if (!Array.isArray(data)) return [];
  return data.filter(
    (entry): entry is string => typeof entry === "string" && entry.length > 0,
  );
}

const DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash";

/** Normalize a transplant `cards` candidate into renderable cards (max 5). */
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
    return {
      tourist: { summary: touristSummary, items },
      transplant: {
        summary: transplantSummary,
        cards: trimmed,
        brands,
        map_center: mapData.center,
        map_pins: mapData.pins,
      },
      whyQloo: toWhyQloo(root, trimmed, city),
      model: toHonestyModel(root),
      broadened: toBroadened(root),
    };
  } catch {
    return null;
  }
}

/** Accumulated STATE_DELTA/STATE_SNAPSHOT fragments for one /agui run. */
interface StreamAcc {
  cards: unknown;
  brands: unknown;
  center: unknown;
  pins: unknown;
  touristSummary: string;
  transplantSummary: string;
  /** Live generic-LLM lane: TEXT_MESSAGE_* deltas under the `tourist` id. */
  touristStream: string;
  touristItems: unknown;
  root: Record<string, unknown>;
}

/** Lenient progressive payload: renders 1+ streamed cards with whatever
 *  summaries and map fragments have arrived. Null only when no renderable
 *  card has arrived yet. Never throws. */
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
    // Neutral placeholder while the pane summaries stream (the template
    // floor's copy would read as fake results mid-run). The live generic
    // lane (TEXT_MESSAGE deltas) wins over whole-string STATE_DELTA text.
    const writeLine = (place: string) => `Writing your ${place} summary…`;
    const touristItems = Array.isArray(acc.touristItems)
      ? toTastePath(acc.touristItems)
      : [];
    return {
      tourist: {
        summary:
          acc.touristStream || acc.touristSummary || writeLine("generic"),
        items: touristItems.length > 0 ? touristItems : defaultTouristItems(place),
      },
      transplant: {
        summary: acc.transplantSummary || writeLine("grounded"),
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
    const snapshotTourist =
      snapshot.tourist && typeof snapshot.tourist === "object"
        ? (snapshot.tourist as Record<string, unknown>)
        : null;
    const rawTouristItems = Array.isArray(snapshotTourist?.items)
      ? snapshotTourist.items
      : Array.isArray(snapshot.tourist_items)
        ? snapshot.tourist_items
        : defaultTouristItems(city);
    const snap = snapshot as Record<string, unknown>;
    let whyRaw: unknown = snap.why_qloo ?? null;
    if (
      !whyRaw &&
      (typeof snap.qloo_query === "string" || typeof snap.taste_path_sentence === "string")
    ) {
      whyRaw = {
        taste_path_sentence: snap.taste_path_sentence,
        qloo_query: snap.qloo_query,
        model: snap.model,
      };
    }
    let metaRaw: unknown = snap.meta ?? null;
    if (!metaRaw && (typeof snap.model === "string" || typeof snap.broadened === "boolean")) {
      metaRaw = { model: snap.model, broadened: snap.broadened };
    }
    return toRevealPayload(
      {
        tourist: { summary: rawTourist, items: rawTouristItems },
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

/** Verbatim brand-kicker fallback (mirrors qloo.BRAND_MOCK) so the kicker
 *  row still renders with zero network. */
const FALLBACK_BRANDS: string[] = [
  "Eko Bite LDN",
  "Owambe Suya Co",
  "Danfo Kitchen Express",
  "Palm & Pine Grocers",
  "Nollywood Nights Pop-up",
];

/** A wire seed: chip ids travel as bare strings; free text rides as {text}. */
type Seed = string | { text: string };

/** Dedupe a mixed seed list: strings by trimmed value, `{text}` objects by text. */
function dedupeSeeds(rawSeeds: unknown): Seed[] {
  if (!Array.isArray(rawSeeds)) return [];
  const seenStrings = new Set<string>();
  const seenTexts = new Set<string>();
  const out: Seed[] = [];
  for (const seed of rawSeeds) {
    if (typeof seed === "string" && seed.trim()) {
      const value = seed.trim();
      if (!seenStrings.has(value)) {
        seenStrings.add(value);
        out.push(value);
      }
    } else if (seed && typeof seed === "object" && !Array.isArray(seed)) {
      const text = (seed as Record<string, unknown>).text;
      if (typeof text === "string" && text.trim()) {
        const value = text.trim();
        if (!seenTexts.has(value)) {
          seenTexts.add(value);
          out.push({ text: value });
        }
      }
    }
  }
  return out;
}

const DEFAULT_CITY = "London";

/** Generic tourist rows — the generic take is deliberately plain. */
function defaultTouristItems(city: string): string[] {
  return [
    `Top 10 restaurants in ${city}`,
    `Must-see sights in ${city}`,
    `Best nightlife in ${city}`,
  ];
}

/** Inlined template-copy floor (mirrors summarizer._template_copy semantics):
 *  the panes never go empty, even offline. */
function templateCopy(place: string): RevealPayload {
  const city = place.trim() || DEFAULT_CITY;
  const cards: TransplantCard[] = Array.from({ length: 5 }, (_, i) => {
    const n = i + 1;
    return {
      name: `${city} transplant spot ${n}`,
      address: city,
      latlng: [FALLBACK_CENTER[0] + 0.01 * i, FALLBACK_CENTER[1] + 0.012 * i],
      why_you: `Because home nights belong at ${city} transplant spot ${n}`,
      affinity_multiplier: 1.0,
      taste_path: ["your tastes", "your crowd", `${city} transplant spot ${n}`],
      entity_id: `urn:entity:place:template-${n}`,
    };
  });
  return {
    tourist: {
      summary: `Tourist take on ${city}: the usual top-10 list, no reasons attached.`,
      items: defaultTouristItems(city),
    },
    transplant: {
      summary: `transplant view for ${city}: grounded picks for your tastes, cached or computed fresh.`,
      cards,
      brands: FALLBACK_BRANDS,
      map_center: FALLBACK_CENTER,
      map_pins: cards.map((card, i) => ({
        latlng: card.latlng ?? FALLBACK_CENTER,
        card_index: i,
      })),
    },
    whyQloo: {
      tastePath: `your tastes \u2192 your crowd \u2192 ${city}`,
      qlooQuery:
        "filter.type=urn:entity:place&filter.location.query=" +
        encodeURIComponent(city) +
        "&feature.explainability=true&take=8",
      model: DEFAULT_MODEL,
    },
    model: DEFAULT_MODEL,
    broadened: false,
  };
}

/* ------------------------------------------------------------------ */
/* Exchange model                                                      */
/* ------------------------------------------------------------------ */

interface Exchange {
  id: string;
  /** What the user asked, displayed in the user bubble. */
  userText: string;
  /** Whole-run history: prior user texts the current result replaced. */
  history: string[];
  /** Chips + city context line under the user bubble. */
  metaLine: string;
  seeds: Seed[];
  city: string;
  refinements: string[];
  status: "streaming" | "json" | "template" | "error";
  activities: AgentActivityItem[];
  activityDone: boolean;
  summaryText: string;
  payload: RevealPayload | null;
  confirmations: Confirmation[];
  notices: string[];
}

function newExchangeId(): string {
  return `ex-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Rebuild the last live exchange from the persisted session. Returns []
 *  for anything unusable — a corrupt session degrades to a fresh chat. */
function restoreExchanges(session: TasteSession | null): Exchange[] {
  try {
    const raw = session?.exchanges;
    if (!Array.isArray(raw) || raw.length === 0) return [];
    const last = raw[raw.length - 1];
    if (!last || typeof last !== "object") return [];
    const rec = last as Record<string, unknown>;
    const city = typeof rec.city === "string" && rec.city ? rec.city : DEFAULT_CITY;
    const payload = toRevealPayload(rec.payload, city);
    if (!payload) return [];
    return [
      {
        id: typeof rec.id === "string" && rec.id ? rec.id : newExchangeId(),
        userText: typeof rec.userText === "string" ? rec.userText : "",
        history: Array.isArray(rec.history)
          ? rec.history.filter((entry): entry is string => typeof entry === "string")
          : [],
        metaLine: typeof rec.metaLine === "string" ? rec.metaLine : "",
        seeds: dedupeSeeds(rec.seeds),
        city,
        refinements: Array.isArray(rec.refinements)
          ? rec.refinements.filter((entry): entry is string => typeof entry === "string")
          : [],
        status: "json" as const,
        activities: [],
        activityDone: true,
        summaryText:
          typeof rec.summaryText === "string" && rec.summaryText
            ? rec.summaryText
            : payload.transplant.summary,
        payload,
        confirmations: toConfirmations(rec.confirmations),
        notices: toNotices(rec.notices),
      },
    ];
  } catch {
    return [];
  }
}

/** Map TOOL_CALL events to AgentActivity items (the beUI activity feed). */
function toolActivityItem(event: Record<string, unknown>, phase: "start" | "end"): AgentActivityItem | null {
  const id = typeof event.toolCallId === "string" ? event.toolCallId : "";
  const name = typeof event.toolCallName === "string" ? event.toolCallName : "";
  if (!id || !name) return null;
  const args = event.args;
  let target = name;
  if (phase === "start" && args && typeof args === "object") {
    const record = args as Record<string, unknown>;
    const location = typeof record.location === "string" ? record.location : "";
    const query = typeof record.query === "string" ? record.query : "";
    target = location ? `${name} · ${location}` : query ? `${name} · "${query}"` : name;
  }
  return { id: `${id}-${phase}`, type: "tool", action: "run", target };
}

/* ------------------------------------------------------------------ */
/* Result card (rendered inside the assistant reply)                   */
/* ------------------------------------------------------------------ */

interface ResultCardProps {
  exchange: Exchange;
  highlighted: number | null;
  onPinClick: (index: number) => void;
  onRefinement: (refinement: string) => void;
  onSkipCandidate: (text: string) => void;
}

function ResultCard({
  exchange,
  highlighted,
  onPinClick,
  onRefinement,
  onSkipCandidate,
}: ResultCardProps) {
  const [whyOpen, setWhyOpen] = useState(false);
  const [honestyOn, setHonestyOn] = useState(false);
  const payload = exchange.payload;
  if (!payload) return null;
  const { transplant, tourist, whyQloo, model } = payload;
  const names = transplant.cards.map((card) => card.name);
  const activeRefinements = new Set(exchange.refinements);

  return (
    <div id="reveal">
      {exchange.notices.length > 0 && (
        <p id="notices">{exchange.notices.join(" · ")}</p>
      )}
      <div className="comparison">
        <section id="tourist-pane" aria-label="What a generic LLM says">
          <div className="col-label">What a generic LLM says</div>
          <p id="tourist-summary">{tourist.summary}</p>
          <div className="tourist-list">
            {tourist.items.map((item) => (
              <p key={item} className="tourist-row">
                {item}
              </p>
            ))}
          </div>
          <p className="col-footnote">Same model, no taste graph — deliberately plain.</p>
        </section>
        <section id="transplant-pane" aria-label="Where your people gather">
          <div className="col-label">Where your people gather</div>
          <p id="transplant-summary">{transplant.summary}</p>
          <div id="refinements" aria-label="Refine the search">
            {["more late-night", "cheaper"].map((label) => {
              const refinement = label === "more late-night" ? "late-night" : label;
              return (
                <button
                  key={label}
                  type="button"
                  className="refinement-pill"
                  data-refinement={refinement}
                  aria-pressed={activeRefinements.has(refinement)}
                  onClick={() => onRefinement(refinement)}
                >
                  {label}
                </button>
              );
            })}
          </div>
          <TransplantMap
            center={transplant.map_center}
            pins={transplant.map_pins}
            names={names}
            onPinClick={onPinClick}
          />
          {transplant.brands.length > 0 && (
            <div id="brands" className="brand-kicker-row" aria-label="Brand kickers">
              {transplant.brands.map((brand) => (
                <span key={brand} className="brand-kicker">
                  {brand}
                </span>
              ))}
            </div>
          )}
          <div className="transplant-grid">
            {transplant.cards.slice(0, 5).map((card, index) => {
              const chain = (card.taste_path ?? []).filter(Boolean);
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
                  <Card size="compact" className="transplant-card-beui">
                    <h3>{card.name}</h3>
                    <AffinityBadge value={card.affinity_multiplier ?? 1.0} />
                    <p className="why-you">{card.why_you ?? card.whyYou ?? ""}</p>
                    <p className="taste-path" aria-label={chainText}>
                      {chainText}
                    </p>
                    <p className="entity-id" data-testid="entity-id">
                      {card.entity_id || `urn:entity:place:card-${index}`}
                    </p>
                  </Card>
                </article>
              );
            })}
          </div>
        </section>
      </div>
      {exchange.confirmations.length > 0 && (
        <div id="approval-pills">
          {exchange.confirmations.map((confirmation) => (
            <div
              key={confirmation.text}
              className="approval-pill"
              data-confirmation-text={confirmation.text}
            >
              <span>
                <span className="approval-name">
                  {confirmation.candidates[0].name}
                </span>{" "}
                <span className="approval-confidence">
                  {confirmation.candidates[0].confidence !== undefined
                    ? `· ${Math.round(confirmation.candidates[0].confidence * 100)}% match`
                    : ""}
                </span>
              </span>
              <span className="approval-actions">
                <button type="button" data-confirm onClick={() => onSkipCandidate(`__keep__${confirmation.text}`)}>
                  Confirm
                </button>
                <button type="button" data-skip onClick={() => onSkipCandidate(confirmation.text)}>
                  Skip
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
      <Collapsible open={whyOpen} onOpenChange={setWhyOpen} className="why-strip">
        <CollapsibleTrigger className="why-toggle">
          <span>Why this beats a list — the Qloo evidence</span>
          <ChevronDown className="size-4" aria-hidden="true" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="why-strip-body">
            <p className="why-path">{whyQloo.tastePath}</p>
            <p className="why-query">{whyQloo.qlooQuery}</p>
            <div className="honesty-toggle">
              <Tooltip content="Both summaries come from the same model — only the evidence differs.">
                <Switch
                  checked={honestyOn}
                  onCheckedChange={setHonestyOn}
                  aria-label="honesty check: same model?"
                />
              </Tooltip>
              <span>honesty check: same model?</span>
            </div>
            {honestyOn && <p className="honesty-line">both panes: {model}</p>}
            {payload.broadened && (
              <div className="alert" role="alert">
                <span>broadened search to fill results</span>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Onboarding screen                                                   */
/* ------------------------------------------------------------------ */

interface OnboardingProps {
  chips: Chip[];
  cities: CityOption[];
  selected: string[];
  labelMap: Record<string, string>;
  city: string;
  prompt: string;
  submitting: boolean;
  related: TasteHit[];
  relatedLoading: boolean;
  hasChat: boolean;
  onToggleChip: (entityId: string) => void;
  onClearChips: () => void;
  onAddTaste: (entityId: string, label: string) => void;
  onCityChange: (city: string) => void;
  onPromptChange: (prompt: string) => void;
  /** Submit with a city override — the typed combobox text, when the user
   *  never pressed Enter or picked an option. */
  onSubmit: (cityOverride?: string) => void;
  onBackToChat: () => void;
}

/** The correlated-suggestions row, shared by every wizard step. While the
 *  Qloo call runs in the background the pills render as pulsing skeletons,
 *  so the row is never blank and the fill-in is visible. */
function SuggestionsRow({
  related,
  loading,
  onAddTaste,
}: {
  related: TasteHit[];
  loading: boolean;
  onAddTaste: (entityId: string, label: string) => void;
}) {
  if (!loading && related.length === 0) return null; // upstream down AND nothing cached
  return (
    <div id="suggested-tastes" aria-label="You might also like" aria-busy={loading}>
      <span className="suggested-caption">
        People into your picks also like
        {loading && <Loader size={11} className="suggested-loader" />}
      </span>
      <div className="suggested-row">
        {loading && related.length === 0
          ? [5.5, 4, 6.5, 4.5, 5].map((width, index) => (
              <span
                key={index}
                className="suggested-pill suggested-pill-skeleton"
                style={{ width: `${width}rem` }}
              />
            ))
          : related.map((hit) => (
              <button
                key={hit.entity_id}
                type="button"
                className="suggested-pill"
                data-suggested={hit.label}
                onClick={() => onAddTaste(hit.entity_id, hit.label)}
              >
                + {hit.label}
              </button>
            ))}
      </div>
    </div>
  );
}

/** One wizard section: this category's chips plus its own custom-taste
 *  search so every bullet can take custom entries. Remounts per step
 *  (keyed), so its search box starts empty each section. */
function CategoryStep({
  category,
  chips,
  selected,
  related,
  relatedLoading,
  onToggleChip,
  onAddTaste,
}: {
  category: string;
  chips: Chip[];
  selected: string[];
  related: TasteHit[];
  relatedLoading: boolean;
  onToggleChip: (entityId: string) => void;
  onAddTaste: (entityId: string, label: string) => void;
}) {
  const [tasteQuery, setTasteQuery] = useState("");
  const [tasteResults, setTasteResults] = useState<TasteHit[]>([]);
  const [tasteSearching, setTasteSearching] = useState(false);

  // Search-as-you-type: any free text becomes a real Qloo entity.
  useEffect(() => {
    const query = tasteQuery.trim();
    if (query.length < 2) {
      setTasteResults([]);
      setTasteSearching(false);
      return;
    }
    setTasteSearching(true);
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetch(`/api/search_tastes?q=${encodeURIComponent(query)}`, {
        signal: controller.signal,
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => setTasteResults(toTasteHits(data?.results).slice(0, 6)))
        .catch(() => {
          /* aborted or offline: keep whatever row state is showing */
        })
        .finally(() => {
          if (!controller.signal.aborted) setTasteSearching(false);
        });
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [tasteQuery]);

  const group = chips.filter((chip) => chip.category === category);
  return (
    <section className="onboarding-field" aria-label={`${category} tastes`}>
      <div id="chips">
        <div className="chip-group-label">{category}</div>
        <div className="chip-row" role="group" aria-label={`${category} chips`}>
          {group.map((chip) => {
            const isOn = selected.includes(chip.entity_id);
            return (
              <button
                key={chip.entity_id}
                type="button"
                className={isOn ? "chip chip-on" : "chip"}
                data-entity-id={chip.entity_id}
                data-selected={isOn ? "true" : "false"}
                aria-pressed={isOn}
                onClick={() => onToggleChip(chip.entity_id)}
              >
                {chip.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="taste-search">
        <input
          id="taste-search"
          type="text"
          autoComplete="off"
          placeholder={`Or search any ${category.toLowerCase()} taste — artists, dishes, brands…`}
          value={tasteQuery}
          onChange={(event) => setTasteQuery(event.target.value)}
          aria-label={`Search any ${category.toLowerCase()} taste`}
        />
        {tasteQuery.trim().length >= 2 && (
          <div className="taste-search-results" role="listbox" aria-label="Search results">
            {tasteResults.map((hit) => (
              <button
                key={hit.entity_id}
                type="button"
                role="option"
                aria-selected={false}
                className="taste-hit"
                data-taste-hit={hit.label}
                onMouseDown={(event) => {
                  event.preventDefault();
                  onAddTaste(hit.entity_id, hit.label);
                  setTasteQuery("");
                  setTasteResults([]);
                }}
              >
                <span className="taste-hit-label">{hit.label}</span>
                {hit.type && <span className="taste-hit-type">{hit.type}</span>}
              </button>
            ))}
            {tasteResults.length === 0 && (
              <p className="taste-search-empty">
                {tasteSearching ? "Searching Qloo…" : "Nothing in the graph yet"}
              </p>
            )}
          </div>
        )}
      </div>
      <SuggestionsRow related={related} loading={relatedLoading} onAddTaste={onAddTaste} />
    </section>
  );
}

function Onboarding({
  chips,
  cities,
  selected,
  labelMap,
  city,
  prompt,
  submitting,
  related,
  relatedLoading,
  hasChat,
  onToggleChip,
  onClearChips,
  onAddTaste,
  onCityChange,
  onPromptChange,
  onSubmit,
  onBackToChat,
}: OnboardingProps) {
  const [step, setStep] = useState(0);
  const [cityOpen, setCityOpen] = useState(false);
  const [cityQuery, setCityQuery] = useState("");
  const [geo, setGeo] = useState<{ state: "checking" | "ok" | "unknown"; label: string } | null>(
    null,
  );

  const labelFor = useCallback(
    (entityId: string) =>
      chips.find((chip) => chip.entity_id === entityId)?.label ??
      labelMap[entityId] ??
      entityId,
    [chips, labelMap],
  );

  const selectedLabels = selected.map(labelFor).filter(Boolean);
  const canSubmit = (selected.length > 0 || prompt.trim().length > 0) && !submitting;
  const isFinalStep = step >= CATEGORIES.length;

  // Geocode status while typing a city — instant dictionary hits first.
  useEffect(() => {
    const query = cityQuery.trim();
    if (cityQuery.length < 2) {
      setGeo(null);
      return;
    }
    setGeo({ state: "checking", label: query });
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetch(`/api/geocode?name=${encodeURIComponent(query)}`, {
        signal: controller.signal,
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => {
          if (data?.found) {
            setGeo({
              state: "ok",
              label: data.country ? `${data.name}, ${data.country}` : data.name,
            });
          } else {
            setGeo({ state: "unknown", label: query });
          }
        })
        .catch(() => {
          /* aborted: leave the last status standing */
        });
    }, 350);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [cityQuery]);

  const commitCity = (value: string) => {
    onCityChange(value.trim());
    setCityQuery("");
    setCityOpen(false);
    setGeo(null);
  };

  /** Submit button path: an uncommitted typed city rides along — a user
   *  typing "Tokyo" and hitting Transplant me must never get London. */
  const handleOnboardSubmit = () => {
    const typed = cityQuery.trim();
    const override = typed || undefined;
    if (typed) {
      onCityChange(typed);
      setCityOpen(false);
      setGeo(null);
    }
    onSubmit(override);
  };

  const filteredCities = cities.filter((option) => {
    const needle = cityQuery.trim().toLowerCase();
    return (
      option.value.toLowerCase().includes(needle) ||
      option.label.toLowerCase().includes(needle)
    );
  });
  const showFreeCity =
    cityQuery.trim().length > 0 &&
    !cities.some((option) => option.value.toLowerCase() === cityQuery.trim().toLowerCase());

  return (
    <main className="onboarding-shell">
      <div className="theme-toggle-slot">
        <ThemeToggle variant="circle" />
      </div>
      <div className="onboarding-card">
        {hasChat && (
          <button
            type="button"
            id="back-to-chat"
            className="back-to-chat"
            onClick={onBackToChat}
          >
            ← Back to chat
          </button>
        )}
        <header className="onboarding-header">
          <div className="volt-logo-tile volt-logo-lg volt-glow" aria-hidden="true">
            <Plane className="h-7 w-7" />
          </div>
          <h1 style={{ margin: 0 }}>
            <TextReveal text="ELSEWHERE" />
          </h1>
          <p className="onboarding-tagline">Feel at home, elsewhere.</p>
          <p className="volt-mono-cap">grounded by qloo · live taste graph</p>
        </header>

        <div className="step-dots" role="tablist" aria-label="Onboarding steps">
          {[...CATEGORIES, "Go"].map((name, index) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-label={`Step ${index + 1}: ${name}`}
              aria-current={index === step ? "step" : undefined}
              className={
                index === step
                  ? "step-dot step-dot-on"
                  : index < step
                    ? "step-dot step-dot-done"
                    : "step-dot"
              }
              onClick={() => setStep(index)}
            >
              {name}
            </button>
          ))}
        </div>
        <p className="onboarding-step-label" aria-live="polite">
          {isFinalStep
            ? "Where did you land?"
            : `${CATEGORIES[step]} — what do you love?`}
        </p>

        {selectedLabels.length > 0 && (
          <div id="selected-tokens">
            {selectedLabels.map((label, index) => (
              <span key={selected[index]} className="token">
                {label}
                <button
                  type="button"
                  data-token-remove
                  aria-label={`Remove ${label}`}
                  onClick={() => onToggleChip(selected[index])}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}

        {isFinalStep ? (
          <>
            <SuggestionsRow related={related} loading={relatedLoading} onAddTaste={onAddTaste} />
            <div className="onboarding-field">
              <label className="onboarding-label" htmlFor="prompt-input">
                Anything else home feels like? (optional)
              </label>
              <PromptInput
                id="prompt-input"
                placeholder="e.g. Afrobeats, late-night ramen, jazz clubs…"
                value={prompt}
                onValueChange={onPromptChange}
                onSubmit={() => {
                  if (canSubmit) handleOnboardSubmit();
                }}
                maxRows={3}
                aria-label="What does home feel like?"
              />
            </div>

            <div className="onboarding-field">
          <label className="onboarding-label" htmlFor="city-input">
            City you just landed in — anywhere
          </label>
          <input
            id="city-input"
            role="combobox"
            aria-expanded={cityOpen}
            aria-controls="city-list"
            autoComplete="off"
            placeholder="Type any city on earth"
            value={cityOpen ? cityQuery : city}
            onChange={(event) => {
              setCityQuery(event.target.value);
              setCityOpen(true);
            }}
            onFocus={() => {
              setCityQuery("");
              setCityOpen(true);
            }}
            onBlur={() => {
              window.setTimeout(() => setCityOpen(false), 120);
            }}
            onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
              if (event.key === "Enter") {
                const first = filteredCities[0];
                commitCity(cityQuery.trim() ? (showFreeCity ? cityQuery : first?.value ?? city) : city);
              }
              if (event.key === "Escape") setCityOpen(false);
            }}
            style={{
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              background: "var(--card)",
              color: "var(--foreground)",
              padding: "0.55rem 0.7rem",
              width: "100%",
            }}
          />
          {geo && cityOpen && (
            <p className="city-geocode-line" data-state={geo.state} aria-live="polite">
              {geo.state === "ok"
                ? `✓ ${geo.label}`
                : geo.state === "checking"
                  ? "Checking the map…"
                  : `Can't place “${geo.label}” yet — Qloo may still know it`}
            </p>
          )}
          {cityOpen && (
            <ul id="city-list" role="listbox" aria-label="Cities">
              {filteredCities.slice(0, 8).map((option) => (
                <li
                  key={option.value}
                  role="option"
                  aria-selected={option.value === city}
                  className="city-option"
                  data-city-option={option.value}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    commitCity(option.value);
                  }}
                >
                  {option.label}
                </li>
              ))}
              {showFreeCity && (
                <li
                  role="option"
                  aria-selected={false}
                  className="city-option"
                  data-city-option={cityQuery}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    commitCity(cityQuery);
                  }}
                >
                  Use “{cityQuery.trim()}”
                </li>
              )}
            </ul>
          )}
        </div>

            <button
              type="button"
              id="transplant-submit"
              className="chat-send-button"
              disabled={!canSubmit}
              onClick={handleOnboardSubmit}
            >
              {submitting ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem" }}>
                  <Loader size={14} /> Transplanting…
                </span>
              ) : (
                "Transplant me"
              )}
            </button>
            <p id="transplant-status" aria-live="polite">
              {submitting
                ? "Transplanting…"
                : selected.length === 0 && !prompt.trim()
                  ? "Pick at least one taste or type what home feels like."
                  : `Transplanting this you → ${city || DEFAULT_CITY}`}
            </p>
          </>
        ) : (
          <CategoryStep
            key={CATEGORIES[step]}
            category={CATEGORIES[step]}
            chips={chips}
            selected={selected}
            related={related}
            relatedLoading={relatedLoading}
            onToggleChip={onToggleChip}
            onAddTaste={onAddTaste}
          />
        )}

        <footer className="step-nav">
          <button
            type="button"
            className="step-nav-btn"
            disabled={step === 0}
            onClick={() => setStep((s) => Math.max(0, s - 1))}
          >
            ← Back
          </button>
          <span className="step-count">
            {step + 1} / {CATEGORIES.length + 1}
          </span>
          {step < CATEGORIES.length ? (
            <button
              type="button"
              className="step-nav-btn step-nav-next"
              onClick={() => setStep((s) => Math.min(CATEGORIES.length, s + 1))}
            >
              {step === CATEGORIES.length - 1 ? "Almost there →" : "Continue →"}
            </button>
          ) : (
            <button
              type="button"
              className="step-nav-btn"
              disabled={selected.length === 0}
              onClick={onClearChips}
            >
              Clear picks
            </button>
          )}
        </footer>
      </div>
    </main>
  );
}

/* ------------------------------------------------------------------ */
/* App root                                                            */
/* ------------------------------------------------------------------ */

export default function App() {
  const [chips, setChips] = useState<Chip[]>(FALLBACK_CHIPS);
  const [cities, setCities] = useState<CityOption[]>(
    FALLBACK_CITIES.map((name) => ({ label: name, value: name })),
  );
  const bootSession = useRef(loadSession());
  const [selected, setSelected] = useState<string[]>(
    () => bootSession.current?.tokens ?? loadTaste().chips,
  );
  const [labelMap, setLabelMap] = useState<Record<string, string>>(
    () => bootSession.current?.labelMap ?? {},
  );
  const [city, setCity] = useState<string>(
    () => bootSession.current?.city || loadTaste().city || DEFAULT_CITY,
  );
  const [prompt, setPrompt] = useState<string>(() => loadTaste().prompt);
  const [screen, setScreen] = useState<"onboarding" | "chat">(() =>
    hasSession() ? "chat" : "onboarding",
  );
  const [exchanges, setExchanges] = useState<Exchange[]>(() =>
    restoreExchanges(bootSession.current),
  );
  const [related, setRelated] = useState<TasteHit[]>([]);
  const [relatedLoading, setRelatedLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Persist the taste card; storage failure must never break the app.
  useEffect(() => {
    saveTaste({ chips: selected, city, prompt });
  }, [selected, city, prompt]);

  // Persist the live session (results ride as opaque JSON) so a reload
  // lands back in the chat with everything intact.
  useEffect(() => {
    if (submitting) return;
    const last = exchanges[exchanges.length - 1];
    if (!last || !last.activityDone) return;
    saveSession({ tokens: selected, city, labelMap, exchanges: [last] });
  }, [exchanges, submitting, selected, city, labelMap]);

  // Live catalog first, inlined snapshot as the offline fallback.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/catalog")
      .then((response) => (response.ok ? response.json() : null))
      .then((data: CatalogResponse | null) => {
        if (cancelled || !data) return;
        if (isChipList(data.chips)) setChips(data.chips);
        if (
          Array.isArray(data.cities) &&
          data.cities.length >= 12 &&
          toCityOptions(data.cities).every((option) => option.value)
        ) {
          setCities(toCityOptions(data.cities));
        }
      })
      .catch(() => {
        /* offline or API down: the fallback snapshot stays rendered */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Correlated suggestions: Qloo's "your crowd also likes", refreshed on
  // every selection change (debounced). Always on: catalog pills fill the
  // cold start before the first pick, then the row refills personalized —
  // skeleton pills show while Qloo works in the background.
  useEffect(() => {
    const controller = new AbortController();
    setRelatedLoading(true);
    const timer = window.setTimeout(() => {
      fetch("/api/related", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_ids: selected.slice(0, 8) }),
        signal: controller.signal,
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => {
          const known = new Set(selected);
          setRelated(
            toTasteHits(data?.results)
              .filter((hit) => !known.has(hit.entity_id))
              .slice(0, 10),
          );
        })
        .catch(() => {
          /* transient: keep the previous suggestion pills */
        })
        .finally(() => {
          if (!controller.signal.aborted) setRelatedLoading(false);
        });
    }, 600);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [selected]);

  const toggleChip = useCallback((entityId: string) => {
    setSelected((current) =>
      current.includes(entityId)
        ? current.filter((entry) => entry !== entityId)
        : [...current, entityId],
    );
  }, []);

  /** Add a taste from search / suggestions; the label may be unknown to
   *  the static chip catalog, so it rides in labelMap. */
  const addTaste = useCallback((entityId: string, label: string) => {
    setSelected((current) =>
      current.includes(entityId) ? current : [...current, entityId],
    );
    if (label.trim()) {
      setLabelMap((current) => ({ ...current, [entityId]: label.trim() }));
    }
  }, []);

  const patchExchange = useCallback((id: string, patch: Partial<Exchange>) => {
    setExchanges((current) =>
      current.map((exchange) =>
        exchange.id === id ? { ...exchange, ...patch } : exchange,
      ),
    );
  }, []);

  /** Run the transplant; the transcript holds exactly ONE result — every
   *  run (first, refinement, follow-up) replaces the current one, and the
   *  prior asks fold into the history trail. SSE first, JSON fallback,
   *  template floor. */
  const runExchange = useCallback(
    async (options: {
      seeds: Seed[];
      city: string;
      refinements: string[];
      userText: string;
      metaLine: string;
    }) => {
      const exchangeId = newExchangeId();
      const exchange: Exchange = {
        id: exchangeId,
        userText: options.userText,
        history: [],
        metaLine: options.metaLine,
        seeds: options.seeds,
        city: options.city,
        refinements: options.refinements,
        status: "streaming",
        activities: [],
        activityDone: false,
        summaryText: "",
        payload: null,
        confirmations: [],
        notices: [],
      };
      setExchanges((current) => {
        const last = current[current.length - 1];
        // The one-result model: replace, carrying the prior asks as trail.
        const carried = last ? [...last.history, last.userText] : [];
        return [{ ...exchange, history: carried.filter(Boolean).slice(-8) }];
      });
      setSubmitting(true);
      setHighlighted(null);

      const seeds = dedupeSeeds(options.seeds);
      const body = JSON.stringify({
        seeds,
        dest_city: options.city,
        refinements: options.refinements,
      });
      // Stop any in-flight run (new submit or Edit tastes), then own the wire.
      abortRef.current?.abort();
      const controller = new AbortController();
      const controllers = new Set<AbortController>([controller]);
      abortRef.current = {
        abort: () => controllers.forEach((entry) => entry.abort()),
      } as AbortController;

      // Idle watchdog, not a total budget: a cold run legitimately streams
      // for 30s+ (two LLM pane summaries), and a rate-limited Qloo ladder
      // blocks 30s+-quiet between rows — so only truly dead streams die.
      let idleTimer = window.setTimeout(() => controller.abort(), 40000);
      const resetIdle = () => {
        window.clearTimeout(idleTimer);
        idleTimer = window.setTimeout(() => controller.abort(), 40000);
      };

      const acc: StreamAcc = {
        cards: undefined,
        brands: undefined,
        center: undefined,
        pins: undefined,
        touristSummary: "",
        transplantSummary: "",
        touristStream: "",
        touristItems: undefined,
        root: {},
      };
      let streamedConfirmations: Confirmation[] = [];
      const activityItems: AgentActivityItem[] = [];

      const applyEvent = (event: AguiEvent) => {
        resetIdle();
        const type = event.type;
        if (type === "TEXT_MESSAGE_START" || type === "TEXT_MESSAGE_END") {
          // The generic lane is tracked via its deltas; nothing to do here.
          return;
        }
        if (type === "TEXT_MESSAGE_CONTENT") {
          if (event.messageId !== "tourist") return;
          const delta = typeof event.delta === "string" ? event.delta : event.content;
          if (typeof delta !== "string" || !delta) return;
          acc.touristStream += delta;
          const progressive = toProgressivePayload(acc, options.city);
          if (progressive) {
            patchExchange(exchangeId, { payload: progressive, summaryText: progressive.transplant.summary });
          }
          return;
        }
        if (type === "TOOL_CALL_START") {
          const item = toolActivityItem(event as Record<string, unknown>, "start");
          if (item) {
            activityItems.push(item);
            patchExchange(exchangeId, { activities: [...activityItems] });
          }
          return;
        }
        if (type === "TOOL_CALL_END") {
          const item = toolActivityItem(event as Record<string, unknown>, "end");
          if (item) {
            activityItems.push(item);
            patchExchange(exchangeId, { activities: [...activityItems] });
          }
          return;
        }
        if (type === "STATE_DELTA" && event.delta && typeof event.delta === "object") {
          const delta = event.delta as Record<string, unknown>;
          if (Array.isArray(delta.cards)) acc.cards = delta.cards;
          if (Array.isArray(delta.brands)) acc.brands = delta.brands;
          if (Array.isArray(delta.confirmations)) {
            streamedConfirmations = toConfirmations(delta.confirmations);
            if (streamedConfirmations.length > 0) {
              patchExchange(exchangeId, { confirmations: streamedConfirmations });
            }
          }
          if (delta.map_center !== undefined) acc.center = delta.map_center;
          if (delta.map_pins !== undefined) acc.pins = delta.map_pins;
          else if (delta.pins !== undefined) acc.pins = delta.pins;
          if (typeof delta.tourist_summary === "string" && delta.tourist_summary) {
            acc.touristSummary = delta.tourist_summary;
          }
          if (Array.isArray(delta.tourist_items)) acc.touristItems = delta.tourist_items;
          if (typeof delta.transplant_summary === "string" && delta.transplant_summary) {
            acc.transplantSummary = delta.transplant_summary;
          }
          for (const key of ["why_qloo", "qloo_query", "taste_path_sentence", "model", "broadened", "meta"]) {
            if (delta[key] !== undefined) acc.root[key] = delta[key];
          }
          const progressive = toProgressivePayload(acc, options.city);
          if (progressive) {
            patchExchange(exchangeId, { payload: progressive, summaryText: progressive.transplant.summary });
          }
          return;
        }
        if (type === "STATE_SNAPSHOT" && event.snapshot && typeof event.snapshot === "object") {
          const snap = event.snapshot as Record<string, unknown>;
          if (Array.isArray(snap.confirmations)) {
            streamedConfirmations = toConfirmations(snap.confirmations);
            if (streamedConfirmations.length > 0) {
              patchExchange(exchangeId, { confirmations: streamedConfirmations });
            }
          }
          const snapshotPayload = toSnapshotPayload(
            event.snapshot as Record<string, unknown>,
            acc.touristSummary,
            acc.transplantSummary,
            options.city,
          );
          if (snapshotPayload) {
            acc.root = event.snapshot as Record<string, unknown>;
            patchExchange(exchangeId, {
              payload: snapshotPayload,
              summaryText: snapshotPayload.transplant.summary,
            });
          }
          return;
        }
        if (type === "RUN_FINISHED") {
          patchExchange(exchangeId, { activityDone: true });
          return;
        }
        if (type === "RUN_ERROR") {
          // Fall through to the JSON path by throwing inside streamAgui's caller.
          throw new Error("run error");
        }
      };

      let adopted = false;
      try {
        const response = await fetch("/agui", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error("agui unavailable");
        await streamAgui(response, applyEvent);
        // Adopt whatever the stream produced.
        const current = acc;
        const final = toProgressivePayload(current, options.city);
        patchExchange(exchangeId, {
          status: "streaming",
          activityDone: true,
          ...(final ? { payload: final } : {}),
        });
        adopted = true;
      } catch {
        adopted = false;
      } finally {
        window.clearTimeout(idleTimer);
      }

      if (!adopted) {
        // JSON fallback — also the only path for confirmations/notices.
        // Fresh controller: an aborted SSE stream must not poison this call.
        const jsonController = new AbortController();
        controllers.add(jsonController);
        const jsonTimer = window.setTimeout(() => jsonController.abort(), 45000);
        try {
          const response = await fetch("/api/transplant", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
            signal: jsonController.signal,
          });
          if (response.ok) {
            const data = (await response.json()) as Record<string, unknown>;
            const payload = toRevealPayload(data, options.city);
            patchExchange(exchangeId, {
              confirmations: toConfirmations(data.confirmations),
              notices: toNotices(data.notices),
              ...(payload
                ? {
                    payload,
                    summaryText: payload.transplant.summary,
                    status: "json" as const,
                    activityDone: true,
                  }
                : { status: "template" as const, activityDone: true }),
            });
            adopted = true;
          }
        } catch {
          adopted = false;
        } finally {
          window.clearTimeout(jsonTimer);
        }
      }

      if (!adopted) {
        const base = templateCopy(options.city);
        patchExchange(exchangeId, {
          payload: base,
          summaryText: base.transplant.summary,
          status: "template",
          activityDone: true,
        });
      }
      setSubmitting(false);
      abortRef.current = null;
    },
    [patchExchange],
  );

  const submitOnboarding = useCallback(
    (cityOverride?: string) => {
      const chipSeeds: Seed[] = selected;
      const textSeed: Seed[] = prompt.trim() ? [{ text: prompt.trim() }] : [];
      const seeds = [...chipSeeds, ...textSeed];
      if (seeds.length === 0) return;
      const useCity = (cityOverride || city || DEFAULT_CITY).trim();
      const labels = selected
        .map((id) => chips.find((chip) => chip.entity_id === id)?.label ?? labelMap[id] ?? "")
        .filter(Boolean);
      const metaLine = [
        labels.length ? labels.join(", ") : prompt.trim(),
        `→ ${useCity}`,
      ]
        .filter(Boolean)
        .join(" ");
      const userText = prompt.trim() || labels.join(", ");
      setScreen("chat");
      void runExchange({
        seeds,
        city: useCity,
        refinements: [],
        userText,
        metaLine,
      });
    },
    [selected, prompt, chips, labelMap, city, runExchange],
  );

  const submitChatPrompt = useCallback(
    (value: string) => {
      const text = value.trim();
      if (!text || submitting) return;
      const seeds: Seed[] = [...selected, { text }];
      const metaLine = `${text} → ${city || DEFAULT_CITY}`;
      void runExchange({
        seeds,
        city: city || DEFAULT_CITY,
        refinements: [],
        userText: text,
        metaLine,
      });
    },
    [selected, city, submitting, runExchange],
  );

  const runRefinement = useCallback(
    (refinement: string) => {
      if (submitting) return;
      const last = exchanges[exchanges.length - 1];
      if (!last) return;
      const label = refinement === "late-night" ? "more late-night" : refinement;
      void runExchange({
        seeds: last.seeds,
        city: last.city,
        refinements: [refinement],
        userText: label,
        metaLine: `${label} → ${last.city}`,
      });
    },
    [exchanges, submitting, runExchange],
  );

  const skipCandidate = useCallback(
    (text: string) => {
      if (submitting || text.startsWith("__keep__")) return;
      const last = exchanges[exchanges.length - 1];
      if (!last) return;
      const seeds = dedupeSeeds(last.seeds).filter(
        (seed) => !(typeof seed === "object" && seed.text === text),
      );
      void runExchange({
        seeds,
        city: last.city,
        refinements: last.refinements,
        userText: last.userText,
        metaLine: last.metaLine,
      });
    },
    [exchanges, submitting, runExchange],
  );

  const onPinClick = useCallback((index: number) => {
    setHighlighted(index);
    window.setTimeout(() => {
      document
        .querySelector(`.transplant-card[data-index="${index}"]`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, 50);
  }, []);

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const prevRunRef = useRef<string>("");
  useEffect(() => {
    const last = exchanges[exchanges.length - 1];
    if (last && last.id !== prevRunRef.current) {
      prevRunRef.current = last.id;
      transcriptRef.current?.scrollTo({
        top: transcriptRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [exchanges]);

  /** VoltChat "New Chat" analog: abort, wipe the session, back to the wizard. */
  const startOver = useCallback(() => {
    abortRef.current?.abort();
    setSubmitting(false);
    setSelected([]);
    setExchanges([]);
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch {
      /* storage may be unavailable — the state reset still applies */
    }
    setScreen("onboarding");
    setSidebarOpen(false);
  }, []);

  const sideLabels = selected.map(
    (id) => chips.find((chip) => chip.entity_id === id)?.label ?? labelMap[id] ?? id,
  );

  const chatBody = (
    <div className="chat-inset">
      <VoltSidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        labels={sideLabels}
        city={city || DEFAULT_CITY}
        selected={selected}
        onRemoveToken={toggleChip}
        onNewTransplant={() => {
          abortRef.current?.abort();
          setSubmitting(false);
          setScreen("onboarding");
          setSidebarOpen(false);
        }}
        onStartOver={startOver}
      />
      <header className="volt-header">
        <button
          type="button"
          className="volt-icon-btn"
          aria-label={sidebarOpen ? "Close session panel" : "Open session panel"}
          aria-expanded={sidebarOpen}
          onClick={() => setSidebarOpen((v) => !v)}
        >
          {sidebarOpen ? (
            <PanelLeftClose className="h-4 w-4" />
          ) : (
            <PanelLeft className="h-4 w-4" />
          )}
        </button>
        <div className="volt-logo-tile volt-glow" aria-hidden="true">
          <Plane className="h-4 w-4" />
        </div>
        <div className="volt-app-name">
          <span className="chat-wordmark">ELSEWHERE</span>
          <span className="volt-mono-cap">
            → {city || DEFAULT_CITY} · {selected.length} taste
            {selected.length === 1 ? "" : "s"}
          </span>
        </div>
        <span className="chat-header-spacer" />
        <button
          type="button"
          className="header-taste-chip"
          onClick={() => {
            abortRef.current?.abort();
            setSubmitting(false);
            setScreen("onboarding");
          }}
          title="Edit your tastes"
        >
          {selected.length > 0
            ? `${selected.length} taste${selected.length > 1 ? "s" : ""}`
            : "Add tastes"}
        </button>
        <ThemeToggle variant="circle" />
      </header>
      <div className="chat-transcript" ref={transcriptRef}>
        <Transcript
          exchanges={exchanges}
          highlighted={highlighted}
          onPinClick={onPinClick}
          onRefinement={runRefinement}
          onSkipCandidate={skipCandidate}
        />
      </div>
      <VoltComposer
        loading={submitting}
        onSubmit={(value) => submitChatPrompt(value)}
        onStop={() => {
          abortRef.current?.abort();
          setSubmitting(false);
        }}
      />
    </div>
  );

  return (
    <>
      {screen === "onboarding" ? (
        <Onboarding
          chips={chips}
          cities={cities}
          selected={selected}
          labelMap={labelMap}
          city={city}
          prompt={prompt}
          submitting={submitting}
          related={related}
          relatedLoading={relatedLoading}
          hasChat={exchanges.length > 0}
          onToggleChip={toggleChip}
          onClearChips={() => setSelected([])}
          onAddTaste={addTaste}
          onCityChange={setCity}
          onPromptChange={setPrompt}
          onSubmit={submitOnboarding}
          onBackToChat={() => setScreen("chat")}
        />
      ) : (
        chatBody
      )}
    </>
  );
}

/** Transcript: a centered voltchat column — user asks in a right-aligned
 *  primary pill, the agent answers full-width (activity rows, streamed
 *  summary, result card, action row). */
function Transcript({
  exchanges,
  highlighted,
  onPinClick,
  onRefinement,
  onSkipCandidate,
}: {
  exchanges: Exchange[];
  highlighted: number | null;
  onPinClick: (index: number) => void;
  onRefinement: (refinement: string) => void;
  onSkipCandidate: (text: string) => void;
}) {
  return (
    <div className="volt-transcript">
      {exchanges.map((exchange) => {
        const trail = exchange.history.filter(
          (entry, index) => entry && entry !== exchange.userText && exchange.history.indexOf(entry) === index,
        );
        const streaming = !exchange.activityDone;
        return (
        <div key={exchange.id} className="volt-exchange">
          <div className="volt-msg-user message-enter">
            {trail.length > 0 && (
              <div className="exchange-trail" aria-label="Earlier asks this replaced">
                {trail.map((entry, index) => (
                  <span key={`${index}-${entry}`} className="exchange-trail-chip">
                    {entry}
                  </span>
                ))}
              </div>
            )}
            <div className="volt-user-bubble">
              <p className="exchange-user-line">{exchange.userText}</p>
              <p className="exchange-meta">{exchange.metaLine}</p>
            </div>
          </div>
          <div className="volt-msg-assistant message-enter">
            <div id="agent-activity" aria-label="Agent activity">
              <ActivityLine
                items={exchange.activities}
                done={exchange.activityDone}
                city={exchange.city}
              />
            </div>
            {!exchange.payload && streaming && (
              <div className="volt-dots" role="status" aria-label="Thinking">
                <span className="loading-dot" />
                <span className="loading-dot" />
                <span className="loading-dot" />
              </div>
            )}
            {(exchange.summaryText || exchange.payload) && (
              <div className="volt-assistant-body">
                <p className={`volt-summary ${streaming ? "cursor-blink" : ""}`}>
                  {exchange.summaryText}
                </p>
                {exchange.payload && (
                  <ResultCard
                    exchange={exchange}
                    highlighted={highlighted}
                    onPinClick={onPinClick}
                    onRefinement={onRefinement}
                    onSkipCandidate={onSkipCandidate}
                  />
                )}
                {!streaming && <VoltActions text={exchange.summaryText} />}
              </div>
            )}
          </div>
        </div>
        );
      })}
    </div>
  );
}

/** Copy / feedback row under a finished agent answer (voltchat action bar). */
function VoltActions({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [thumb, setThumb] = useState<"up" | "down" | null>(null);
  const copy = () => {
    if (!text) return;
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      },
      () => {
        /* clipboard unavailable in some contexts — nothing to do */
      },
    );
  };
  return (
    <div className="volt-actions" aria-label="Message actions">
      <Tooltip content={copied ? "Copied" : "Copy summary"}>
        <button type="button" className="volt-action" onClick={copy} aria-label="Copy summary">
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </Tooltip>
      <Tooltip content="Good answer">
        <button
          type="button"
          className={`volt-action ${thumb === "up" ? "volt-action-on" : ""}`}
          aria-pressed={thumb === "up"}
          aria-label="Good answer"
          onClick={() => setThumb((t) => (t === "up" ? null : "up"))}
        >
          <ThumbsUp className="h-3.5 w-3.5" />
        </button>
      </Tooltip>
      <Tooltip content="Off the mark">
        <button
          type="button"
          className={`volt-action ${thumb === "down" ? "volt-action-on" : ""}`}
          aria-pressed={thumb === "down"}
          aria-label="Off the mark"
          onClick={() => setThumb((t) => (t === "down" ? null : "down"))}
        >
          <ThumbsDown className="h-3.5 w-3.5" />
        </button>
      </Tooltip>
    </div>
  );
}

/** One quiet line for the whole agent run: bouncing dots + a mono caption
 *  while working, an expandable "Ran N tool calls" trace at rest (voltchat
 *  tool-row styling: status dot + mono name + target). */
function ActivityLine({
  items,
  done,
  city,
}: {
  items: AgentActivityItem[];
  done: boolean;
  city: string;
}) {
  const [open, setOpen] = useState(false);
  if (!done) {
    return (
      <div className="activity-line" role="status">
        <span className="activity-caption volt-mono-cap">
          <span className="volt-status-dot volt-status-running" aria-hidden="true" />
          {items.length > 0
            ? `querying ${city}…`
            : "reading what home feels like…"}
        </span>
        <span className="volt-dots" aria-hidden="true">
          <span className="loading-dot" />
          <span className="loading-dot" />
          <span className="loading-dot" />
        </span>
      </div>
    );
  }
  return (
    <div className="activity-line">
      {items.length === 0 ? (
        <span className="activity-summary" role="status">
          <span className="volt-status-dot volt-status-done" aria-hidden="true" /> Result
          ready
        </span>
      ) : (
        <>
          <button
            type="button"
            className="activity-summary"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            <span className="volt-status-dot volt-status-done" aria-hidden="true" /> Ran{" "}
            {items.length} tool
            {items.length === 1 ? "" : "s"}
            <ChevronDown className="size-3.5 chev" aria-hidden="true" data-open={open} />
          </button>
          {open && (
            <div className="activity-rows">
              {items.map((item) =>
                item.type === "tool" ? (
                  <div key={item.id} className="agent-activity-row">
                    <span
                      className="volt-status-dot volt-status-done"
                      aria-hidden="true"
                    />
                    <span className="activity-tool-name">{item.action}</span>
                    <span className="activity-tool-target">{item.target}</span>
                  </div>
                ) : null,
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Collapsible session rail (voltchat sidebar): new-transplant, the live
 *  picks + city, start-over. Overlay drawer at every size — safe on mobile. */
function VoltSidebar({
  open,
  onClose,
  labels,
  city,
  selected,
  onRemoveToken,
  onNewTransplant,
  onStartOver,
}: {
  open: boolean;
  onClose: () => void;
  labels: string[];
  city: string;
  selected: string[];
  onRemoveToken: (entityId: string) => void;
  onNewTransplant: () => void;
  onStartOver: () => void;
}) {
  return (
    <>
      <div
        className={open ? "volt-backdrop open" : "volt-backdrop"}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={open ? "volt-sidebar open" : "volt-sidebar"}
        aria-label="Session panel"
        aria-hidden={!open}
      >
        <div className="volt-side-body">
          <button type="button" className="volt-new-chat" onClick={onNewTransplant}>
            <PlusCircle className="h-4 w-4" /> New transplant
          </button>
          <h3 className="volt-side-label volt-mono-cap">your picks</h3>
          {labels.length === 0 ? (
            <p className="volt-side-empty volt-mono-cap">nothing picked yet</p>
          ) : (
            <div id="selected-tokens" className="volt-side-tokens">
              {labels.map((label, index) => (
                <span key={selected[index]} className="token">
                  {label}
                  <button
                    type="button"
                    data-token-remove
                    aria-label={`Remove ${label}`}
                    onClick={() => onRemoveToken(selected[index])}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <p className="volt-side-city volt-mono-cap">→ {city}</p>
        </div>
        <div className="volt-side-foot">
          <p className="volt-mono-cap">
            <span className="volt-status-dot volt-status-done" aria-hidden="true" /> live ·
            qloo grounded
          </p>
          <button
            type="button"
            className="volt-action volt-start-over"
            onClick={onStartOver}
            disabled={labels.length === 0}
          >
            Start over
          </button>
        </div>
      </aside>
    </>
  );
}

/** voltchat composer: glass card with glow-on-focus, round send button,
 *  mono hint row. Enter sends; Shift+Enter is a newline. */
function VoltComposer({
  loading,
  onSubmit,
  onStop,
}: {
  loading: boolean;
  onSubmit: (value: string) => void;
  onStop: () => void;
}) {
  const [value, setValue] = useState("");
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const canSend = value.trim().length > 0 && !loading;

  const submit = () => {
    const text = value.trim();
    if (!text || loading) return;
    onSubmit(text);
    setValue("");
    window.setTimeout(() => {
      if (areaRef.current) areaRef.current.style.height = "auto";
    }, 0);
  };

  const autosize = () => {
    const area = areaRef.current;
    if (!area) return;
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, 200)}px`;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="volt-composer-wrap">
      <div className="volt-composer">
        <div className="volt-composer-card">
          <textarea
            ref={areaRef}
            className="volt-composer-input"
            placeholder="Say what home feels like — or refine: more late-night, cheaper…"
            value={value}
            rows={1}
            onChange={(event) => {
              setValue(event.target.value);
              autosize();
            }}
            onKeyDown={onKeyDown}
            aria-label="Chat with the transplant agent"
          />
          {loading ? (
            <button
              type="button"
              className="volt-send volt-send-stop"
              onClick={onStop}
              aria-label="Stop generating"
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              type="button"
              className="volt-send"
              disabled={!canSend}
              onClick={submit}
              aria-label="Send message"
            >
              <ArrowUp className="h-5 w-5" strokeWidth={2.5} />
            </button>
          )}
        </div>
        <p className="volt-hints volt-mono-cap" aria-hidden="true">
          <span>↵ to send</span>
          <span className="volt-hint-sep">·</span>
          <span>Shift+↵ for newline</span>
        </p>
      </div>
    </div>
  );
}
