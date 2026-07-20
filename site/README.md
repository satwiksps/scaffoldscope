# ScaffoldScope website

The official ScaffoldScope landing page lives entirely in this directory. It is a
native Next.js App Router project with strict TypeScript and Tailwind CSS, isolated
from the Python package and experiment runtime.

The landing route is built from Server Components and prerendered as static content.
There are no analytics, cookies, third-party runtime requests, or application secrets.

## Toolchain

- Node.js 24
- Next.js 16
- React 19
- TypeScript 6
- Tailwind CSS 4

## Local development

```bash
cd site
npm ci --ignore-scripts
npm run check
npm run build
npm run dev
```

The development server starts at `http://localhost:3000`. The production build
must pass lint, generated-route type checking, TypeScript, and Next's static render.

Set `SITE_URL` to the final HTTPS origin when building outside Vercel:

```bash
SITE_URL=https://example.com npm run build
```

PowerShell:

```powershell
$env:SITE_URL = "https://example.com"
npm run build
```

On Vercel, the metadata layer reads `VERCEL_PROJECT_PRODUCTION_URL`. If neither
variable is present, canonical metadata falls back to
`https://scaffoldscope-azure.vercel.app`.

## Vercel deployment

Import `satwiksps/scaffoldscope` and set the project **Root Directory** to
`site`. Keep the **Framework Preset** on Next.js. Vercel then uses Node 24,
the committed npm lockfile, and the native Next build automatically; no custom output
directory is required.

The checked-in `vercel.json` contains only the framework declaration and response
security headers. Git integration can create preview deployments for pull requests
and deploy `main` to production.
