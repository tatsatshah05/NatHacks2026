// Accessibility settings (OP-307): the storage keys, and the tiny script that applies
// text size, motion and theme to <html> before the first paint, so a page never
// flashes at the wrong size or in the wrong theme. It has no imports and no React, so
// the root layout (a server component) can put it in the page as a plain string.

/** Speech keeps the key OP-303 introduced: "on" or "off", on when missing. */
export const SPEAK_STORAGE_KEY = "openpath.speak";
/** Every other setting, as one JSON object. */
export const SETTINGS_STORAGE_KEY = "openpath.settings";

/**
 * Runs in <head> before the body is parsed. It sets `data-text`, `data-motion` and
 * `data-theme` on <html> from storage and leaves them off for the defaults. Any
 * failure (blocked storage, bad JSON) leaves the defaults, which render correctly.
 * Keep it in step with `applySettings` in settings.ts.
 */
export const SETTINGS_BOOT_SCRIPT = `(function(){try{var d=document.documentElement,s=JSON.parse(localStorage.getItem(${JSON.stringify(
  SETTINGS_STORAGE_KEY
)})||"{}")||{};if(s.textSize==="large"||s.textSize==="larger")d.setAttribute("data-text",s.textSize);if(s.motion==="reduce"||s.motion==="full")d.setAttribute("data-motion",s.motion);if(s.theme==="light"||s.theme==="dark")d.setAttribute("data-theme",s.theme)}catch(e){}})();`;
