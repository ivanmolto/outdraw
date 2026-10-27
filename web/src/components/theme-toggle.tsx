"use client";

// House lights: the light/dark switch in the header. The choice is kept in localStorage
// and applied before paint by the inline script in app/layout.tsx (THEME_SCRIPT), so a
// reload never flashes the other palette; with no choice stored the system setting wins.
import { useSyncExternalStore } from "react";

export const THEME_KEY = "outdraw:theme";
type Theme = "light" | "dark";

/** Runs in <head> before the first paint: reads the stored choice onto <html>. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
  THEME_KEY,
)});if(t==="light"||t==="dark"){document.documentElement.dataset.theme=t}}catch(e){}})();`;

/** The theme on screen: the stored choice, else the system's. */
function current(): Theme {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** The theme is external state (the <html> attribute and the system setting), read on the
 * client only: the server can't know the stored choice, so it renders the switch unlabelled
 * by theme and the client fills it in. */
const CHANGE = "outdraw:theme-change";
function subscribe(onChange: () => void) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", onChange);
  window.addEventListener(CHANGE, onChange);
  return () => {
    mq.removeEventListener("change", onChange);
    window.removeEventListener(CHANGE, onChange);
  };
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, current, () => null);

  const next: Theme = theme === "dark" ? "light" : "dark";
  const flip = () => {
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Private mode: the choice lasts for the page only.
    }
    window.dispatchEvent(new Event(CHANGE));
  };

  return (
    <button
      type="button"
      onClick={flip}
      aria-label={theme ? `Switch to ${next} theme` : "Switch theme"}
      aria-pressed={theme === "dark"}
      title="House lights"
      data-testid="theme-toggle"
      data-theme-icon={theme === "dark" ? "sun" : "moon"}
      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-line text-ink hover:bg-surface-2"
    >
      {theme === "dark" ? (
        // Lights down: a sun, to bring them up.
        <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4" fill="none">
          <circle cx="8" cy="8" r="3" fill="currentColor" />
          <path
            d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        // Lights up (or not yet known): a moon, to bring them down.
        <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor">
          <path d="M9.5 1.5a6.5 6.5 0 1 0 5 10.3 5.5 5.5 0 0 1-5-10.3z" />
        </svg>
      )}
    </button>
  );
}
