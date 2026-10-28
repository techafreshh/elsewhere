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
      </section>
    </main>
  );
}
