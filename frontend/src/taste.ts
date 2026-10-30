/** Taste-card + session persistence plus the offline fallback snapshot.
 *
 * The catalog of record lives server-side (backend/data/*.json, served by
 * GET /api/catalog). The snapshot below mirrors it so the onboarding still
 * renders its full chip tray offline or before the fetch resolves.
 */

export interface Chip {
  label: string;
  category: string;
  entity_id: string;
}

/** One city-dictionary row, mirroring backend/data/cities.json. */
export interface CityEntry {
  name: string;
  country?: string;
  aliases?: string[];
  lat?: number;
  lng?: number;
}

export interface TasteState {
  chips: string[];
  city: string;
  prompt: string;
}

/** What the transcript keeps between reloads. Exchanges ride as opaque
 *  JSON (validated on restore in App.tsx); tokens/labels restore the
 *  onboarding prefill exactly as the user left it. */
export interface TasteSession {
  tokens: string[];
  city: string;
  labelMap: Record<string, string>;
  exchanges: unknown[];
}

/** localStorage key for the taste card. Contains "taste" by contract. */
export const STORAGE_KEY = "elsewhere.taste.v1";

/** localStorage key for the live chat session (results + selection). */
export const SESSION_KEY = "elsewhere.session.v1";

export const CATEGORIES = ["Music", "Food", "Screens", "Vibes"] as const;

/** Preseeded city suggestions, mirroring the backend dictionary highlights. */
export const FALLBACK_CITIES: string[] = [
  "London",
  "Toronto",
  "NYC",
  "Lagos",
  "Nairobi",
  "Berlin",
  "Paris",
  "Tokyo",
  "New York",
  "Dubai",
  "Accra",
  "Cape Town",
  "Singapore",
  "São Paulo",
  "Mexico City",
  "Sydney",
  "Seoul",
  "Istanbul",
  "Amsterdam",
  "Cairo",
];

/** 24 pre-resolved taste chips, mirroring backend/data/chips.json. */
export const FALLBACK_CHIPS: Chip[] = [
  { category: "Music", entity_id: "A80A473D-103A-4B71-8BFB-461D537B35EA", label: "Asake" },
  { category: "Music", entity_id: "4A322BDD-04A3-4369-8234-B38A7FB485D9", label: "Burna Boy" },
  { category: "Music", entity_id: "BDA1B73F-10E7-4D09-AC51-B796CCEC5FF3", label: "Bad Bunny" },
  { category: "Music", entity_id: "AFB6CA3D-9620-4D8B-9958-6727BA40AFA6", label: "BLACKPINK" },
  { category: "Music", entity_id: "578CFC26-B696-449A-9655-3FB270DDA725", label: "Miles Davis" },
  { category: "Music", entity_id: "924C8504-4BD8-4615-AB2D-1C16880A8A62", label: "Arijit Singh" },
  { category: "Food", entity_id: "31F75A7E-CFEF-4841-8171-FE806A84A243", label: "2am shawarma" },
  { category: "Food", entity_id: "CF2E618D-610D-4959-8243-58CD4B93AB78", label: "Jollof rice" },
  { category: "Food", entity_id: "0E650077-B891-45DA-9C8B-9B006CBB7BA1", label: "Late-night ramen" },
  { category: "Food", entity_id: "06A473BA-0EC6-4C14-9909-581B8F42F904", label: "Street tacos" },
  { category: "Food", entity_id: "066EEF18-8658-445E-A591-1D37A7B125E2", label: "Dim sum" },
  { category: "Food", entity_id: "0CCFADD2-D0A4-4E39-A2A5-D6D5BCD87CAF", label: "Slow brunch" },
  { category: "Screens", entity_id: "348A2B47-D933-42B4-935F-EE6C5626FAF3", label: "Nollywood drama" },
  { category: "Screens", entity_id: "67B3C0D5-363A-45D3-A860-A3615B238135", label: "Bollywood musicals" },
  { category: "Screens", entity_id: "FEA96DFD-EE4E-4B0F-81B1-8B88FDBD3376", label: "Anime marathons" },
  { category: "Screens", entity_id: "1FC8268F-0174-40C9-9441-2D19EBB14CAC", label: "K-drama nights" },
  { category: "Screens", entity_id: "43E29A3A-91A8-4091-A46E-B74E50A0C20B", label: "Music documentaries" },
  { category: "Screens", entity_id: "5E30435A-DE6B-4054-91BB-97C5C827AA8A", label: "Late-night cinema" },
  { category: "Vibes", entity_id: "FBCC01ED-BF06-42DC-AA8E-A1084DD63EAE", label: "Rooftop sunsets" },
  { category: "Vibes", entity_id: "7FD742C4-D31A-4CEA-B802-7ADB568C749B", label: "Jazz clubs" },
  { category: "Vibes", entity_id: "C72E64BA-A82B-400F-B798-BB2BB5EA540C", label: "Street markets" },
  { category: "Vibes", entity_id: "A222752B-88B1-4FD2-918B-A43D3D7C45D4", label: "Karaoke nights" },
  { category: "Vibes", entity_id: "57379550-AD3B-4DAA-A021-92EBBC2AF7EE", label: "Quiet bookshops" },
  { category: "Vibes", entity_id: "3B493704-FE09-439F-BEAF-B2C15C9DABFA", label: "Techno nights" },
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

/** Read the live chat session; null when absent/unusable (never throws). */
export function loadSession(): TasteSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<TasteSession>;
    if (!parsed || typeof parsed !== "object") return null;
    return {
      tokens: Array.isArray(parsed.tokens)
        ? parsed.tokens.filter((entry): entry is string => typeof entry === "string")
        : [],
      city: typeof parsed.city === "string" ? parsed.city : "",
      labelMap:
        parsed.labelMap && typeof parsed.labelMap === "object"
          ? (parsed.labelMap as Record<string, string>)
          : {},
      exchanges: Array.isArray(parsed.exchanges) ? parsed.exchanges : [],
    };
  } catch {
    return null;
  }
}

/** Persist the live chat session; storage failure must never break the app. */
export function saveSession(session: TasteSession): void {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    /* private mode / quota: chatting still works without persistence */
  }
}

/** True when a finished result session exists in storage. */
export function hasSession(): boolean {
  const session = loadSession();
  return Array.isArray(session?.exchanges) && session.exchanges.length > 0;
}
