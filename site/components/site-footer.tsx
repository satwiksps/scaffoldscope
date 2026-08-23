import { siteConfig } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="border-t border-white/[0.07] bg-[#070708]">
      <div className="mx-auto flex max-w-7xl flex-col gap-8 px-5 py-10 sm:px-6 md:flex-row md:items-end md:justify-between lg:px-8">
        <div>
          <a
            className="inline-flex items-center gap-2.5 font-semibold tracking-tight"
            href="#top"
          >
            <span
              aria-hidden="true"
              className="grid size-7 place-items-center rounded-md border border-white/15 bg-white/[0.04] font-mono text-[10px] font-bold text-blue-300"
            >
              S:
            </span>
            ScaffoldScope
          </a>
          <p className="mt-3 max-w-sm text-xs leading-5 text-zinc-400">
            Controlled, paired ablations for coding-agent harnesses.
          </p>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-3 text-xs text-zinc-400">
          <FooterLink href={siteConfig.docs.root}>Docs</FooterLink>
          <FooterLink href={siteConfig.docs.configuration}>Configuration</FooterLink>
          <FooterLink href={siteConfig.docs.architecture}>Architecture</FooterLink>
          <FooterLink href={siteConfig.project.security}>Security</FooterLink>
          <FooterLink href={siteConfig.project.contributing}>Contributing</FooterLink>
          <FooterLink href={siteConfig.pypi}>PyPI</FooterLink>
          <FooterLink href={siteConfig.repository}>GitHub</FooterLink>
        </div>
      </div>
      <div className="mx-auto flex max-w-7xl flex-col gap-1 border-t border-white/[0.05] px-5 py-5 font-mono text-[11px] text-zinc-400 sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
        <span>Apache License 2.0</span>
        <span>v{siteConfig.version}, paired and auditable</span>
      </div>
    </footer>
  );
}

function FooterLink({
  children,
  href,
}: Readonly<{
  children: string;
  href: string;
}>) {
  return (
    <a
      className="hover:text-zinc-200"
      href={href}
      rel="noreferrer"
      target="_blank"
    >
      {children}
    </a>
  );
}
