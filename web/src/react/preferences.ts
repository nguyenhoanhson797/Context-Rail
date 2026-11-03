/* =====================================================================
   UI PREFERENCES - accent, sidebar width, row density and motion

   One module owns the appearance preferences the Settings screen edits and the
   shell restores at startup (E1/E2):

   - every field is a closed enum, so a value that arrives from localStorage can
     never invent a selector, a style or a data attribute;
   - one compatibility key (`ctx-rail.prefs`, the legacy name) with a tolerant
     reader: a missing key, malformed JSON, an unknown enum member or a wrong
     type falls back to that field's default instead of throwing;
   - one apply step writes all four `data-rail-*` attributes on <html>, so the
     stylesheet appendix in styles.css can resolve them on every route (not only
     while Settings is mounted).

   Theme is deliberately NOT here: the shell owns `data-rail-theme` and the theme
   control, and this module never touches it.
   ===================================================================== */
export type Accent = 'indigo' | 'blue' | 'violet' | 'teal' | 'rose' | 'graphite';
export type Side = 'comfortable' | 'compact';
export type Density = 'comfortable' | 'compact';
export type Motion = 'system' | 'reduce';
export interface Prefs {
  accent: Accent;
  side: Side;
  density: Density;
  motion: Motion;
}

/** The browser-local preferences key, unchanged from the legacy shell. */
export const PREF_KEY = 'ctx-rail.prefs';

/** The fixed accent palette: `id` for the preference, `colour` for the swatch paint. */
export const ACCENTS = [
  ['indigo', '#4B5BD6'],
  ['blue', '#2F6CD4'],
  ['violet', '#7348CC'],
  ['teal', '#1A7F7A'],
  ['rose', '#B93D68'],
  ['graphite', '#34332F'],
] as const satisfies readonly (readonly [Accent, string])[];

export const DEFAULT_PREFS: Prefs = { accent: 'indigo', side: 'comfortable', density: 'comfortable', motion: 'system' };

const ACCENT_IDS: readonly Accent[] = ACCENTS.map(([id]) => id);

/** The closed set of motion values: `system` follows the OS, `reduce` forces the calm CSS. */
export const MOTIONS: readonly Motion[] = ['system', 'reduce'];

/**
 * Coerce any stored/unknown value into a complete Prefs object.
 *
 * Per field, never all-or-nothing: one unrecognised value cannot discard the
 * other three, and nothing here can throw. `side`/`density` accept exactly
 * `compact` or `comfortable`; `motion` exactly `reduce` or `system`; `accent`
 * must be one of ACCENTS.
 */
export function normalizePrefs(value: unknown): Prefs {
  const raw = value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
  return {
    accent: ACCENT_IDS.includes(raw.accent as Accent) ? (raw.accent as Accent) : DEFAULT_PREFS.accent,
    side: raw.side === 'compact' ? 'compact' : 'comfortable',
    density: raw.density === 'compact' ? 'compact' : 'comfortable',
    motion: raw.motion === 'reduce' ? 'reduce' : 'system',
  };
}

/** The stored preferences, or the defaults when the key is absent, unreadable or malformed. */
export function readPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (raw !== null) return normalizePrefs(JSON.parse(raw) as unknown);
    // Older UI stores preferences inside ctx-rail.ui, not ctx-rail.prefs.
    const legacy = JSON.parse(localStorage.getItem('ctx-rail.ui') || 'null') as { prefs?: unknown } | null;
    return normalizePrefs(legacy?.prefs);
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

/** Persist preferences under PREF_KEY. A storage that refuses (private mode, full disk) is ignored. */
export function savePrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify(normalizePrefs(prefs)));
  } catch {
    /* preferences only: an unwritable storage must not break the screen */
  }
}

/**
 * Publish preferences to the document as the four `data-rail-*` attributes.
 *
 * Always all four, so the stylesheet rules (`[data-rail-accent]`,
 * `[data-rail-side]`, `[data-rail-density]`, `[data-rail-motion]`) match from the
 * first paint on every route. `root` is injectable for tests.
 */
export function applyPrefs(prefs: Prefs, root: HTMLElement = document.documentElement): void {
  const safe = normalizePrefs(prefs);
  root.dataset.railAccent = safe.accent;
  root.dataset.railSide = safe.side;
  root.dataset.railDensity = safe.density;
  root.dataset.railMotion = safe.motion;
  // Publish the legacy appearance contract as well: both frontends use one palette.
  if (safe.accent === 'indigo') delete root.dataset.accent;
  else root.dataset.accent = safe.accent;
  root.dataset.side = safe.side;
  root.dataset.density = safe.density;
  if (safe.motion === 'reduce') root.dataset.motion = 'reduce';
  else delete root.dataset.motion;
}

/**
 * The shell's startup entry point: read the stored preferences and apply them.
 *
 * Idempotent and never throwing, so an App mount can call it unconditionally.
 * Returns the preferences that were applied (defaults when nothing is stored).
 */
export function restorePreferences(): Prefs {
  const prefs = readPrefs();
  applyPrefs(prefs);
  return prefs;
}
