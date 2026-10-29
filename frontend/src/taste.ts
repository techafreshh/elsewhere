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
  { category: "Music", entity_id: "A80A473D-103A-4B71-8BFB-461D537B35EA", label: "Asake" },
  {
    category: "Music",
    entity_id: "4A322BDD-04A3-4369-8234-B38A7FB485D9",
    label: "Burna Boy",
  },
  { category: "Music", entity_id: "C510E01A-E17C-45FC-8DCE-64A963A121A4", label: "Wizkid" },
  { category: "Music", entity_id: "A78C19AA-DF8B-4D1C-9120-0F39041BA590", label: "Tems" },
  {
    category: "Food",
    entity_id: "720A3BDF-18FA-450F-997F-34B79EE1BE77",
    label: "Owambe jollof",
  },
  {
    category: "Food",
    entity_id: "8F8A810B-FDE6-4481-B344-D4F9D9623A8E",
    label: "2am shawarma",
  },
  {
    category: "Food",
    entity_id: "B0FF0F27-62B1-4D0E-B86C-ECF0E0DF39B5",
    label: "Suya nights",
  },
  {
    category: "Food",
    entity_id: "6E524581-0ED4-49EF-8935-0CE390A82F92",
    label: "Owambe parties",
  },
  {
    category: "Screens",
    entity_id: "7240FF40-BB23-4CD6-B8F5-131C21DAAAA2",
    label: "Nollywood slow-burns",
  },
  {
    category: "Screens",
    entity_id: "5DC6251A-9EC6-4D3A-8562-E0F8A5596CA2",
    label: "Filmhouse IMAX",
  },
  {
    category: "Screens",
    entity_id: "EB79F0FD-E747-47A3-8E82-494729DA7C91",
    label: "Afrobeats videos",
  },
  {
    category: "Screens",
    entity_id: "7FCAE187-B060-46FB-8A92-6C4199CE2C51",
    label: "Late-night cinema",
  },
  {
    category: "Vibes",
    entity_id: "1BEF27FB-A71F-4368-AC38-E5B49868B39C",
    label: "Rooftop sunsets",
  },
  {
    category: "Vibes",
    entity_id: "26F0478A-3A0B-48B0-B809-74F18C55CAC7",
    label: "Street markets",
  },
  {
    category: "Vibes",
    entity_id: "11538FAE-116D-47BD-9915-5BB1785FFF4E",
    label: "Late-night crowds",
  },
  {
    category: "Vibes",
    entity_id: "1B2DE081-8B04-410A-A75D-FC5FD147CDD4",
    label: "Quiet rooftops",
  },
];

/** 4 frozen judge-demo pairs, mirroring backend/data/seed_pairs.json. */
export const FALLBACK_SEED_PAIRS: SeedPair[] = [
  {
    from_home: "Lagos",
    dest_city: "London",
    seed_ids: [
      "A80A473D-103A-4B71-8BFB-461D537B35EA",
      "5DC6251A-9EC6-4D3A-8562-E0F8A5596CA2",
      "6E524581-0ED4-49EF-8935-0CE390A82F92",
      "8F8A810B-FDE6-4481-B344-D4F9D9623A8E",
    ],
  },
  {
    from_home: "Lagos",
    dest_city: "Toronto",
    seed_ids: [
      "4A322BDD-04A3-4369-8234-B38A7FB485D9",
      "7240FF40-BB23-4CD6-B8F5-131C21DAAAA2",
      "720A3BDF-18FA-450F-997F-34B79EE1BE77",
      "11538FAE-116D-47BD-9915-5BB1785FFF4E",
    ],
  },
  {
    from_home: "NYC",
    dest_city: "Lagos",
    seed_ids: [
      "A78C19AA-DF8B-4D1C-9120-0F39041BA590",
      "7FCAE187-B060-46FB-8A92-6C4199CE2C51",
      "1BEF27FB-A71F-4368-AC38-E5B49868B39C",
      "26F0478A-3A0B-48B0-B809-74F18C55CAC7",
    ],
  },
  {
    from_home: "Nairobi",
    dest_city: "Lagos",
    seed_ids: [
      "C510E01A-E17C-45FC-8DCE-64A963A121A4",
      "EB79F0FD-E747-47A3-8E82-494729DA7C91",
      "B0FF0F27-62B1-4D0E-B86C-ECF0E0DF39B5",
      "1B2DE081-8B04-410A-A75D-FC5FD147CDD4",
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
