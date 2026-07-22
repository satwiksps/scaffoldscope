/* eslint-disable @next/next/no-html-link-for-pages -- Plain anchors avoid shipping the client router for a static landing page. */
import { siteConfig } from "@/lib/site";

const navigation = [
  { href: "/#method", label: "Method" },
  { href: "/#treatments", label: "Treatments" },
  { href: "/#workflow", label: "Workflow" },
  { href: "/#evidence", label: "Evidence" },
];

export function SiteHeader() {
  return (
    <>
      <a
        className="fixed left-4 top-3 z-50 -translate-y-20 rounded-md bg-mint px-4 py-2 text-sm font-semibold text-canvas transition-transform focus:translate-y-0"
        href="#main-content"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-line bg-canvas/95 backdrop-blur-sm">
        <div className="mx-auto flex h-16 max-w-[1200px] items-center justify-between px-5 sm:px-6">
          <a
            aria-label="ScaffoldScope home"
            className="flex min-h-11 items-center gap-3 font-semibold tracking-tight text-ink"
            href="/#overview"
          >
            <span
              aria-hidden="true"
              className="flex size-8 items-center justify-center rounded-md border border-line-strong bg-surface font-mono text-sm"
            >
              S<span className="text-mint">:</span>
            </span>
            <span>ScaffoldScope</span>
          </a>
          <nav
            aria-label="Primary navigation"
            className="hidden items-center gap-7 md:flex"
          >
            {navigation.map((item) => (
              <a
                className="text-sm text-muted transition-colors hover:text-ink"
                href={item.href}
                key={item.href}
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-2 sm:gap-3">
            <a
              className="inline-flex min-h-11 items-center rounded-md px-3 text-sm font-medium text-muted transition-colors hover:text-ink"
              href={siteConfig.repository}
            >
              GitHub
            </a>
            <a
              className="hidden min-h-11 items-center rounded-md bg-ink px-4 text-sm font-semibold text-canvas transition-colors hover:bg-mint-soft sm:inline-flex"
              href="/#quickstart"
            >
              Get started
            </a>
          </div>
        </div>
      </header>
    </>
  );
}
