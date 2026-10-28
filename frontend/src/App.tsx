import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { motion } from "motion/react";
import {
  CATEGORIES,
  FALLBACK_CHIPS,
  FALLBACK_CITIES,
  loadTaste,
  saveTaste,
  type Chip,
} from "./taste.ts";

interface CatalogResponse {
  chips?: Chip[];
  cities?: string[];
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
  why_you?: string;
  whyYou?: string;
  affinity_multiplier?: number;
  taste_path?: string[];
  entity_id?: string;
}

interface RevealPayload {
  tourist: { summary: string; items: string[] };
  transplant: { summary: string; cards: TransplantCard[] };
}

type StatusPath = "idle" | "sse" | "json" | "template";

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
  return {
    tourist: {
      summary: `Tourist take on ${place}: the usual top-10 list, no reasons attached.`,
      items: defaultTouristItems(place),
    },
    transplant: {
      summary: `Grounded transplant of home tastes to ${place}: spots where your crowd already gathers, backed by Qloo affinity signals.`,
      cards: [1, 2, 3, 4, 5].map((n) => ({
        name: `${place} transplant spot ${n}`,
        address: place,
        why_you: `Because home nights belong at ${place} transplant spot ${n}`,
        affinity_multiplier: 1.0,
        taste_path: ["home", "your crowd", `${place} transplant spot ${n}`],
        entity_id: `urn:entity:place:template-${n}`,
      })),
    },
  };
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
    const rawCards = Array.isArray(transplant.cards) ? transplant.cards : [];
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
      cards.push({ ...(rec as Partial<TransplantCard>), name, why_you: why });
      if (cards.length >= 5) break;
    }
    if (!touristSummary || !transplantSummary || cards.length < 5) return null;
    return {
      tourist: { summary: touristSummary, items },
      transplant: { summary: transplantSummary, cards: cards.slice(0, 5) },
    };
  } catch {
    return null;
  }
}

/** Best-effort parse of the POST /agui SSE stream into a pane payload.
 *  Null on any failure (non-SSE body, RUN_ERROR, short snapshot) so the
 *  caller falls through to the JSON path. Never throws. */
function tryParseAguiStream(text: string, city: string): RevealPayload | null {
  try {
    let snapshot: Record<string, unknown> | null = null;
    let deltaTourist = "";
    let deltaTransplant = "";
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const jsonText = trimmed.slice("data:".length).trim();
      if (!jsonText) continue;
      let event: Record<string, unknown>;
      try {
        event = JSON.parse(jsonText) as Record<string, unknown>;
      } catch {
        continue;
      }
      if (!event || typeof event.type !== "string") continue;
      if (event.type === "RUN_ERROR") return null;
      if (event.type === "STATE_SNAPSHOT" && event.snapshot && typeof event.snapshot === "object") {
        snapshot = event.snapshot as Record<string, unknown>;
      } else if (event.type === "STATE_DELTA" && event.delta && typeof event.delta === "object") {
        const delta = event.delta as Record<string, unknown>;
        if (typeof delta.tourist_summary === "string" && delta.tourist_summary) {
          deltaTourist = delta.tourist_summary;
        }
        if (typeof delta.transplant_summary === "string" && delta.transplant_summary) {
          deltaTransplant = delta.transplant_summary;
        }
      }
    }
    if (!snapshot) return null;
    const rawTourist =
      typeof snapshot.tourist_summary === "string" && snapshot.tourist_summary
        ? snapshot.tourist_summary
        : deltaTourist;
    const rawTransplant =
      typeof snapshot.transplant_summary === "string" && snapshot.transplant_summary
        ? snapshot.transplant_summary
        : deltaTransplant;
    return toRevealPayload(
      {
        tourist: { summary: rawTourist, items: defaultTouristItems(city) },
        transplant: { summary: rawTransplant, cards: snapshot.cards },
      },
      city,
    );
  } catch {
    return null;
  }
}

export default function App() {
  const [initial] = useState(loadTaste);
  const [chips, setChips] = useState<Chip[]>(FALLBACK_CHIPS);
  const [cities, setCities] = useState<string[]>(FALLBACK_CITIES);
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
  const [submitting, setSubmitting] = useState(false);
  const [statusPath, setStatusPath] = useState<StatusPath>("idle");
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

  const toggleChip = (entityId: string) => {
    setSelected((prev) =>
      prev.includes(entityId)
        ? prev.filter((entry) => entry !== entityId)
        : [...prev, entityId],
    );
  };

  // Submit pipeline: optimistic POST /agui SSE attempt (~3s abort), then the
  // POST /api/transplant JSON fallback, then the inlined template-copy floor.
  // Never rejects and never surfaces a 500 — every path ends in panes.
  const onSubmit = async () => {
    if (submitting) return;
    setSubmitting(true);
    const seeds = selected.length > 0 ? selected : HERO_LONDON_SEEDS;
    const destCity = city.trim() || DEFAULT_CITY;
    const body = JSON.stringify({ seeds, dest_city: destCity });
    let payload: RevealPayload | null = null;
    let path: StatusPath = "json";
    try {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 3000);
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
        if (sseRes.ok) {
          const text = await sseRes.text();
          const parsed = tryParseAguiStream(text, destCity);
          if (parsed) {
            payload = parsed;
            path = "sse";
          }
        }
      } catch {
        /* SSE unavailable — the JSON fallback below handles it */
      } finally {
        window.clearTimeout(timer);
      }
    } catch {
      /* never let the optimistic path break the submit */
    }
    if (!payload) {
      try {
        const res = await fetch("/api/transplant", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        });
        if (res.ok) {
          const data: unknown = await res.json();
          payload = toRevealPayload(data, destCity);
        }
      } catch {
        payload = null;
      }
    }
    if (!payload) {
      payload = templateCopy(destCity);
      path = "template";
    }
    setReveal(payload);
    setRevealed(true);
    setStatusPath(path);
    setSubmitting(false);
  };

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

  const onCityChange = (value: string) => {
    queryRef.current = value;
    setCityQuery(value);
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
  const cityValue = cityOpen ? cityQuery : city;
  const visibleCities = cityOpen ? filteredCities : cities;

  return (
    <main className="hero-shell">
      <header className="hero-header">
        <h1>ELSEWHERE</h1>
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
          <input
            id="prompt-input"
            type="text"
            placeholder="What does home feel like? e.g. Asake, owambe parties, 2am shawarma"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>

        <div className="field">
          <h2 id="chips-heading">Taste chips</h2>
          <p className="hint">Tap 3–5 tastes that feel like home.</p>
          <div id="selected-tokens" aria-live="polite">
            {selectedChips.length === 0 ? (
              <span className="tokens-empty">No tastes picked yet.</span>
            ) : (
              <>
                {selectedChips.map((chip) => (
                  <button
                    key={chip.entity_id}
                    type="button"
                    data-token-remove={chip.entity_id}
                    aria-label={`Remove ${chip.label}`}
                    className="token"
                    onClick={() => toggleChip(chip.entity_id)}
                  >
                    {chip.label} <span aria-hidden="true">×</span>
                  </button>
                ))}
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
                      <motion.button
                        key={chip.entity_id}
                        type="button"
                        data-entity-id={chip.entity_id}
                        data-selected={isOn ? "true" : "false"}
                        aria-pressed={isOn}
                        className={isOn ? "chip chip-on" : "chip"}
                        whileTap={{ scale: 0.95 }}
                        onClick={() => toggleChip(chip.entity_id)}
                      >
                        {chip.label}
                      </motion.button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="field combobox-field">
          <label htmlFor="city-input">City</label>
          <input
            id="city-input"
            type="text"
            role="combobox"
            aria-expanded={cityOpen}
            aria-controls="city-list"
            aria-autocomplete="list"
            autoComplete="off"
            placeholder="Pick a city… e.g. London"
            value={cityValue}
            onFocus={openCityList}
            onChange={(e) => onCityChange(e.target.value)}
            onBlur={onCityBlur}
            onKeyDown={onCityKeyDown}
          />
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

        <div className="field">
          <button
            id="transplant-submit"
            type="button"
            onClick={onSubmit}
            disabled={submitting}
          >
            {submitting ? "Transplanting\u2026" : "Transplant me"}
          </button>
          <p id="transplant-status" role="status" data-status={statusPath}>
            {STATUS_COPY[statusPath]}
          </p>
        </div>
      </section>

      <section id="reveal" aria-label="Tourist versus transplant" hidden={!revealed}>
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
          <div className="transplant-grid">
            {(reveal?.transplant.cards ?? []).slice(0, 5).map((card, index) => (
              <article
                key={`${index}-${card.entity_id || card.name}`}
                className="transplant-card"
              >
                <h3>{card.name}</h3>
                <p className="why-you">{card.why_you ?? card.whyYou ?? ""}</p>
              </article>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
