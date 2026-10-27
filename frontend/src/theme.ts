export type ThemeMode = "system" | "light" | "dark";
const cookieName = "xilogue_theme";

// Theme has no credentials. A host-only cookie survives the app's changing loopback port.
export function readTheme(): ThemeMode {
  try {
    const value = document.cookie.split("; ").find((part) => part.startsWith(`${cookieName}=`))?.split("=")[1];
    return value === "light" || value === "dark" ? value : "system";
  } catch { return "system"; }
}
export function saveTheme(mode: ThemeMode) {
  try { document.cookie = `${cookieName}=${mode}; Path=/; SameSite=Strict; Max-Age=31536000`; }
  catch { /* The current page still uses the chosen theme. */ }
}
