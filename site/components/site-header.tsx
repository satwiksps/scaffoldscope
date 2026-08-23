import { MobileMenu } from "@/components/mobile-menu";
import { siteConfig } from "@/lib/site";

const navigation = [
  { href: "#method", label: "Method" },
  { href: "#treatments", label: "Treatments" },
  { href: "#evidence", label: "Evidence" },
];

export function SiteHeader() {
  return (
    <>
      <a
        className="fixed left-4 top-4 z-[100] -translate-y-24 rounded-md bg-white px-4 py-2 text-sm font-semibold text-zinc-950 transition-transform focus:translate-y-0"
        href="#main"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-50 border-b border-white/[0.07] bg-[#09090b]/90 backdrop-blur-xl">
        <nav
          aria-label="Main navigation"
          className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-6 lg:px-8"
        >
          <a
            aria-label="ScaffoldScope home"
            className="flex items-center gap-2.5 font-semibold tracking-tight"
            href="#top"
          >
            <span>ScaffoldScope</span>
          </a>
          <div className="hidden items-center gap-7 text-sm text-zinc-400 md:flex">
            {navigation.map((item) => (
              <a
                className="transition-colors hover:text-white"
                href={item.href}
                key={item.href}
              >
                {item.label}
              </a>
            ))}
            <a
              className="transition-colors hover:text-white"
              href={siteConfig.docs.root}
              rel="noreferrer"
              target="_blank"
            >
              Docs
            </a>
          </div>
          <div className="flex items-center gap-2">
            <a
              className="hidden h-9 items-center rounded-md border border-white/10 bg-white/[0.035] px-3.5 text-sm font-medium text-zinc-200 transition-colors hover:border-white/20 hover:bg-white/[0.07] sm:inline-flex"
              href={siteConfig.repository}
              rel="noreferrer"
              target="_blank"
            >
              GitHub
            </a>
            <MobileMenu />
          </div>
        </nav>
      </header>
    </>
  );
}
