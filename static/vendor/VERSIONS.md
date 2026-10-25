# Self-hosted frontend files (no CDN at run time)

| File | Version | Source | Licence |
|---|---|---|---|
| `vendor/htmx-2.0.11.min.js` | htmx 2.0.11 | https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/dist/htmx.min.js | BSD 2-Clause (`LICENSE-htmx.txt`) |
| `vendor/alpine-3.17.4.min.js` | Alpine.js 3.17.4 | https://cdn.jsdelivr.net/npm/alpinejs@3.17.4/dist/cdn.min.js | MIT (`LICENSE-alpine.txt`) |
| `templates/components/icon.html` | Lucide 1.52.0 (inline SVG) | https://cdn.jsdelivr.net/npm/lucide-static@1.52.0/icons/ | ISC (`LICENSE-lucide.txt`) |
| `fonts/InterVariable.woff2` | Inter 4.1 | https://github.com/rsms/inter/releases/tag/v4.1 | SIL Open Font License 1.1 (`fonts/LICENSE-Inter.txt`) |
| `css/app.css` | built by Tailwind CSS v4.3.3 (standalone CLI) | `python manage.py tailwind build` from `frontend/tailwind/app.css` | MIT |
| `img/favicon-32.png`, `img/apple-touch-icon-180.png`, `img/logo-96.png` | — | `docs/design/logo.png` cropped, centred on a #22378F square, resized (Pillow, LANCZOS; ASSUMPTIONS 256) | project logo |

To upgrade htmx or Alpine: download the new file with its version in the name, change the `<script>` in
`templates/base.html` and this table, then run the tests.
