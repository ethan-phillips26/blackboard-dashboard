// Light, dark, or whatever the machine is set to. The choice is a browser
// preference rather than server state — it belongs to the screen you are
// reading on, not to the account.

const KEY = "theme";
export const THEMES = ["light", "dark", "system"];

const query = () => window.matchMedia("(prefers-color-scheme: dark)");

export function stored() {
  try {
    const saved = localStorage.getItem(KEY);
    return THEMES.includes(saved) ? saved : "system";
  } catch {
    return "system";
  }
}

/** What "system" currently resolves to. */
export function resolve(choice) {
  return choice === "system" ? (query().matches ? "dark" : "light") : choice;
}

export function apply(choice) {
  document.documentElement.dataset.theme = resolve(choice);
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    // A browser with storage blocked still gets the theme for this session.
  }
}

/** Follow the OS while the choice is "system"; returns an unsubscribe. */
export function watch(choice, onChange) {
  if (choice !== "system") return () => {};
  const mq = query();
  const handler = () => onChange(resolve("system"));
  mq.addEventListener("change", handler);
  return () => mq.removeEventListener("change", handler);
}
