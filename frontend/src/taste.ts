/** Taste-card persistence plus the offline fallback snapshot.
 *
 * The catalog of record lives server-side (backend/data/*.json, served by
 * GET /api/catalog). The snapshot below mirrors it so the hero still renders
 * its full chip tray offline or before the fetch resolves.
 */

export interface Chip {
  label: string;
  category: string;
  entity_id: string;
}

/** One frozen judge-demo pair: a home city plus its destination and seed set. */
export interface SeedPair {
  from_home: string;
  dest_city: string;
  seed_ids: string[];
}

export interface TasteState {
  chips: string[];
  city: string;
  prompt: string;
}

/** localStorage key for the taste card. Contains "taste" by contract. */
export const STORAGE_KEY = "elsewhere.taste.v1";

export const CATEGORIES = ["Music", "Food", "Screens", "Vibes"] as const;

/** Preseeded cities, mirroring backend/data/cities.json. */
export const FALLBACK_CITIES: string[] = [
  "London",
  "Toronto",
  "NYC",
  "Lagos",
  "Nairobi",
];

/** 16 pre-resolved taste chips, mirroring backend/data/chips.json. */
export const FALLBACK_CHIPS: Chip[] = [
  { category: "Music", entity_id: "urn:entity:artist:asake", label: "Asake" },
  {
    category: "Music",
    entity_id: "urn:entity:artist:burna-boy",
    label: "Burna Boy",
  },
  { category: "Music", entity_id: "urn:entity:artist:wizkid", label: "Wizkid" },
  { category: "Music", entity_id: "urn:entity:artist:tems", label: "Tems" },
  {
    category: "Food",
    entity_id: "urn:entity:cuisine:jollof-rice",
    label: "Owambe jollof",
  },
  {
    category: "Food",
    entity_id: "urn:entity:brand:shawarma-2am",
    label: "2am shawarma",
  },
  {
    category: "Food",
    entity_id: "urn:entity:cuisine:suya",
    label: "Suya nights",
  },
  {
    category: "Food",
    entity_id: "urn:entity:brand:owambe-catering",
    label: "Owambe parties",
  },
  {
    category: "Screens",
    entity_id: "urn:entity:movie:nollywood-slow-burn",
    label: "Nollywood slow-burns",
  },
  {
    category: "Screens",
    entity_id: "urn:entity:place:filmhouse-imax",
    label: "Filmhouse IMAX",
  },
  {
    category: "Screens",
    entity_id: "urn:entity:tv:afrobeats-videos",
    label: "Afrobeats videos",
  },
  {
    category: "Screens",
    entity_id: "urn:entity:place:late-night-cinema",
    label: "Late-night cinema",
  },
  {
    category: "Vibes",
    entity_id: "urn:entity:tag:rooftop-sunsets",
    label: "Rooftop sunsets",
  },
  {
    category: "Vibes",
    entity_id: "urn:entity:tag:street-markets",
    label: "Street markets",
  },
  {
    category: "Vibes",
    entity_id: "urn:entity:tag:late-night-crowds",
    label: "Late-night crowds",
  },
  {
    category: "Vibes",
    entity_id: "urn:entity:tag:quiet-rooftops",
    label: "Quiet rooftops",
  },
];

/** 4 frozen judge-demo pairs, mirroring backend/data/seed_pairs.json. */
export const FALLBACK_SEED_PAIRS: SeedPair[] = [
  {
    from_home: "Lagos",
    dest_city: "London",
    seed_ids: [
      "urn:entity:artist:asake",
      "urn:entity:place:filmhouse-imax",
      "urn:entity:brand:owambe-catering",
      "urn:entity:brand:shawarma-2am",
    ],
  },
  {
    from_home: "Lagos",
    dest_city: "Toronto",
    seed_ids: [
      "urn:entity:artist:burna-boy",
      "urn:entity:movie:nollywood-slow-burn",
      "urn:entity:cuisine:jollof-rice",
      "urn:entity:tag:late-night-crowds",
    ],
  },
  {
    from_home: "NYC",
    dest_city: "Lagos",
    seed_ids: [
      "urn:entity:artist:tems",
      "urn:entity:place:late-night-cinema",
      "urn:entity:tag:rooftop-sunsets",
      "urn:entity:tag:street-markets",
    ],
  },
  {
    from_home: "Nairobi",
    dest_city: "Lagos",
    seed_ids: [
      "urn:entity:artist:wizkid",
      "urn:entity:tv:afrobeats-videos",
      "urn:entity:cuisine:suya",
      "urn:entity:tag:quiet-rooftops",
    ],
  },
];

const EMPTY_STATE: TasteState = { chips: [], city: "", prompt: "" };

/** Read the persisted taste card; never throws, even with corrupt JSON. */
export function loadTaste(): TasteState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY_STATE;
    const parsed = JSON.parse(raw) as Partial<TasteState>;
    return {
      chips: Array.isArray(parsed.chips)
        ? parsed.chips.filter(
            (entry): entry is string => typeof entry === "string",
          )
        : [],
      city: typeof parsed.city === "string" ? parsed.city : "",
      prompt: typeof parsed.prompt === "string" ? parsed.prompt : "",
    };
  } catch {
    return EMPTY_STATE;
  }
}

/** Persist the taste card; storage failure must never break the hero. */
export function saveTaste(state: TasteState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* private mode / quota: the page stays usable without persistence */
  }
}
