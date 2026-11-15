# Switchboard landing page

React, TypeScript and Vite source for the static landing page. The production
build lands in `frontend/dist/` (Vercel's default output directory).
The page uses black and white dithered artwork with a small orange brand accent.
It describes the gateway on `main`; it does not call a backend or execute tools.

## Develop and build

Requires Node.js 22.12+.

```bash
cd frontend
npm ci
npm run dev
npm test
npm run build
```

The production build lands in `frontend/dist/`. Serve it with a static server,
for example `python -m http.server 8080 --directory frontend/dist` from the
repository root.
Assets use relative URLs so the build also works under a hosting subdirectory.

The setup dialog provides macOS/Linux and Windows commands for the current
CLI and filesystem MCP server, a link to the repository installation guide,
and clipboard feedback. It explains filesystem access and the dependency
annotation needed for multi-step routes. The workflow dialog and architecture
source selector are illustrative and perform no live execution.

Radix/shadcn supplies dialog focus management, Escape handling and platform
tabs. Interaction tests cover those behaviors, clipboard success/failure/races,
keyboard source selection, and navigation. CSS respects reduced motion and
adapts the architecture diagram to small screens.

The signal sculpture is original procedural SVG artwork generated offline by
`node scripts/generate-signal.mjs`. Its trefoil geometry is shaded and processed
with Floyd–Steinberg dithering. The browser renders one static image; no canvas
animation loop, external image request, or image-processing dependency is used.
