// MapLibre GL with its worker served by this app.
//
// maplibre-gl 6 finds its worker next to its own module through `import.meta.url`.
// The bundler rewrites that to the build machine's file path, so in the browser the
// worker fails to load ("Worker failed to load") and no tiles or lines are drawn.
// `new URL(..., import.meta.url)` here makes the bundler emit the worker file and
// give its served URL, which is set before any map is created.
import * as maplibregl from "maplibre-gl";

const workerUrl = new URL("../../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url);
if (typeof window !== "undefined") maplibregl.setWorkerUrl(workerUrl.href);

export default maplibregl;
