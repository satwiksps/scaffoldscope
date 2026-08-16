/* eslint-disable @next/next/no-html-link-for-pages -- Plain anchors avoid shipping the client router for a static landing page. */
import { siteConfig } from "@/lib/site";

const documentation = [
  ["Operator guide", siteConfig.docs.operator],
  ["Configuration", siteConfig.docs.configuration],
  ["Architecture", siteConfig.docs.architecture],
  ["Experiment design", siteConfig.docs.experimentDesign],
  ["Result schema", siteConfig.docs.results],
] as const;

const project = [
  ["GitHub", siteConfig.repository],
  ["PyPI", siteConfig.pypi],
  ["Releases", siteConfig.repository + "/releases"],
  ["Scope & limitations", siteConfig.project.scope],
  ["Contributing", siteConfig.project.contributing],
  ["Security", siteConfig.project.security],
] as const;

export function SiteFooter() {
  return (
    <footer className="border-t border-line py-14">
      <div className="mx-auto grid max-w-[1200px] gap-12 px-5 sm:px-6 md:grid-cols-[1.5fr_1fr_1fr]">
        <div>
          <a
            className="inline-flex items-center gap-3 font-semibold text-ink"
            href="/#overview"
          >
            <span
              aria-hidden="true"
              className="flex size-8 items-center justify-center rounded-md border border-line-strong bg-surface font-mono text-sm"
            >
              S<span className="text-mint">:</span>
            </span>
            ScaffoldScope
          </a>
          <p className="mt-4 max-w-sm text-sm leading-6 text-muted">
            Controlled, paired ablations for coding-agent harnesses.
          </p>
          <p className="mt-6 text-xs text-dim">
            Apache-2.0 · © 2026 ScaffoldScope contributors
          </p>
        </div>
        <FooterList heading="Documentation" links={documentation} />
        <FooterList heading="Project" links={project} />
      </div>
    </footer>
  );
}

function FooterList({
  heading,
  links,
}: Readonly<{
  heading: string;
  links: readonly (readonly [string, string])[];
}>) {
  return (
    <div>
      <h2 className="text-sm font-semibold text-ink">{heading}</h2>
      <ul className="mt-4 space-y-3">
        {links.map(([label, href]) => (
          <li key={href}>
            <a
              className="text-sm text-muted transition-colors hover:text-ink"
              href={href}
            >
              {label}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
