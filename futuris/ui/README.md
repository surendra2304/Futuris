# FUTURIS UI

React + Vite + TypeScript + Tailwind console for the FUTURIS API, served by the
API itself at `/ui` (the committed `dist/` build is mounted by
`SPAStaticFiles`).

- `src/App.tsx` — single-page universe console: health, peer probes, and a
  provenance-labelled forecast workspace (every forecast carries an
  `evidence_class` badge: live / derived / synthetic / demo).
- `src/api/client.ts` — typed API client; reads the API key from
  `localStorage` (`futuris_api_key`) and parses the `{"error": {...}}` envelope.
- `src/pages/` — multi-page components (forecasts, calibration, outcomes,
  subscriptions, ecosystem) written for a routed console. They are not wired
  into `main.tsx` yet; `react-router-dom` is installed for when they are.

Build (requires `npm ci` first):

```bash
npm run build    # tsc -b && vite build  ->  dist/
```

The dev server proxies `/v1` to `http://127.0.0.1:8000` (`vite.config.ts`).
