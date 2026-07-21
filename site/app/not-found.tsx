/* eslint-disable @next/next/no-html-link-for-pages -- Plain anchors avoid shipping the client router for a static landing page. */
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main
        className="mx-auto flex min-h-[65vh] max-w-[1200px] items-center px-5 py-24 sm:px-6"
        id="main-content"
      >
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.15em] text-mint">
            404
          </p>
          <h1 className="mt-4 text-4xl font-semibold tracking-tight text-ink sm:text-5xl">
            This path is not part of the plan.
          </h1>
          <p className="mt-5 max-w-xl text-lg leading-8 text-muted">
            The page may have moved. Return to the project overview or open the
            repository documentation.
          </p>
          <a
            className="mt-8 inline-flex min-h-11 items-center rounded-md bg-ink px-5 text-sm font-semibold text-canvas hover:bg-mint-soft"
            href="/"
          >
            Back to ScaffoldScope
          </a>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
