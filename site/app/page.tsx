import { ProjectWindow } from "@/components/project-window";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { siteConfig } from "@/lib/site";

export const dynamic = "error";

const capabilities = [
  {
    label: "01",
    title: "Paired by construction",
    body: "Every task and replicate receives each treatment from the same starting state. Treatment order can be randomized deterministically.",
  },
  {
    label: "02",
    title: "Auditable context",
    body: "The canonical trajectory stays append-only. Derived model views record the complete assistant/tool bundles they retain or drop.",
  },
  {
    label: "03",
    title: "Verifiable evidence",
    body: "Traces, patches, reports, evaluator overlays, identities, and checksums ship in a deterministic evidence bundle.",
  },
] as const;

const policies = [
  {
    policy: "none",
    trigger: "Never",
    behavior: "Keep full canonical history until typed overflow.",
  },
  {
    policy: "reactive",
    trigger: "Utilization threshold",
    behavior: "Summarize salient history and retain recent atomic bundles.",
  },
  {
    policy: "periodic",
    trigger: "Fixed turn cadence",
    behavior: "Compact on declared boundaries with emergency handling.",
  },
  {
    policy: "selective",
    trigger: "Context budget",
    behavior: "Select atomic bundles with deterministic budgeted scoring.",
  },
] as const;

const evidence = [
  [
    "Outcomes",
    "Solve rate, governed solve, paired wins/losses/ties, terminal status",
  ],
  [
    "Resources",
    "Tokens, configured-price cost, cache activity, model, tool, and wall time",
  ],
  [
    "Context",
    "Pressure, compaction exposure, compression, source selection, constraints",
  ],
  [
    "Validity",
    "Pair coverage, treatment exposure, drift, duplicates, usage completeness",
  ],
] as const;

const guardrails = [
  "Harness and protocol failures stay in the intention-to-treat denominator.",
  "Provider-reported usage remains separate from local estimates.",
  "Task-level resampling preserves the paired study structure.",
  "Scripted, small, or incomplete panels stay descriptive.",
] as const;

export default function Home() {
  const structuredData = {
    "@context": "https://schema.org",
    "@type": "SoftwareSourceCode",
    name: siteConfig.name,
    description: siteConfig.description,
    codeRepository: siteConfig.repository,
    license:
      siteConfig.repository + "/blob/v" + siteConfig.version + "/LICENSE",
    programmingLanguage: "Python",
    runtimePlatform: "Python 3.10+",
  };

  return (
    <>
      <SiteHeader />
      <main id="main-content">
        <section
          className="border-b border-line pb-20 pt-16 sm:pb-24 sm:pt-20 lg:pb-28 lg:pt-24"
          id="overview"
        >
          <div className="mx-auto grid max-w-[1200px] items-center gap-14 px-5 sm:px-6 lg:grid-cols-[0.82fr_1.18fr] lg:gap-16">
            <div>
              <p className="font-mono text-xs font-medium uppercase tracking-[0.16em] text-mint">
                Open-source evaluation tooling
              </p>
              <h1 className="mt-6 max-w-2xl text-[2.8rem] font-semibold leading-[1.04] tracking-[-0.045em] text-ink sm:text-6xl lg:text-[4rem]">
                Controlled experiments for coding-agent harnesses.
              </h1>
              <p className="mt-7 max-w-xl text-lg leading-8 text-muted">
                Hold the model, tasks, and budget constant while you compare one
                context, tool, or instruction treatment at a time. Inspect every
                trial and publish verifiable evidence.
              </p>
              <div className="mt-9 flex flex-wrap gap-3">
                <a
                  className="inline-flex min-h-12 items-center rounded-md bg-ink px-5 text-sm font-semibold text-canvas transition-colors hover:bg-mint-soft"
                  href="#quickstart"
                >
                  Get started
                </a>
                <a
                  className="inline-flex min-h-12 items-center rounded-md border border-line-strong bg-surface px-5 text-sm font-semibold text-ink transition-colors hover:border-muted"
                  href={siteConfig.repository}
                >
                  View on GitHub
                </a>
              </div>
              <p className="mt-8 font-mono text-[11px] leading-6 text-dim">
                Python 3.10+, no runtime dependencies, Apache-2.0
              </p>
            </div>
            <ProjectWindow />
          </div>
        </section>

        <section
          className="scroll-mt-20 border-b border-line py-20 sm:py-24"
          id="method"
        >
          <div className="mx-auto max-w-[1200px] px-5 sm:px-6">
            <div className="grid gap-10 lg:grid-cols-2 lg:gap-20">
              <div>
                <SectionLabel>Why controlled ablations</SectionLabel>
                <h2 className="mt-5 max-w-xl text-3xl font-semibold tracking-[-0.03em] text-ink sm:text-4xl">
                  Know which part of the harness changed the result.
                </h2>
              </div>
              <div>
                <p className="text-lg leading-8 text-muted">
                  Agent evaluations often change the model, prompt, tools,
                  context manager, retry policy, and budget together. That
                  produces a score without clean attribution. ScaffoldScope
                  turns harness choices into declared treatments inside paired
                  task and replicate blocks.
                </p>
                <div className="mt-8 flex flex-wrap items-center gap-x-2 border-y border-line py-4 font-mono text-xs leading-6 text-muted">
                  <span className="text-ink">
                    Same model. Same tasks. Same budget.
                  </span>{" "}
                  <span className="text-mint">One declared treatment.</span>
                </div>
              </div>
            </div>

            <div className="mt-16 grid gap-10 md:grid-cols-3">
              {capabilities.map((item) => (
                <article
                  className="border-t border-line-strong pt-6"
                  key={item.label}
                >
                  <p className="font-mono text-xs text-dim">{item.label}</p>
                  <h3 className="mt-5 text-lg font-semibold text-ink">
                    {item.title}
                  </h3>
                  <p className="mt-3 text-sm leading-6 text-muted">{item.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section
          className="scroll-mt-20 border-b border-line py-20 sm:py-24"
          id="treatments"
        >
          <div className="mx-auto max-w-[1200px] px-5 sm:px-6">
            <div className="grid gap-12 lg:grid-cols-[0.72fr_1.28fr] lg:gap-20">
              <div>
                <SectionLabel>Treatment surface</SectionLabel>
                <h2 className="mt-5 text-3xl font-semibold tracking-[-0.03em] text-ink sm:text-4xl">
                  Declare the treatment. Pin everything else.
                </h2>
                <p className="mt-5 text-base leading-7 text-muted">
                  Start with four built-in context policies, then vary the tool
                  surface or treatment instructions. Versioned plugins can add
                  policies and providers without escaping experiment identity.
                </p>
                <a
                  className="mt-6 inline-flex min-h-11 items-center text-sm font-semibold text-mint hover:text-mint-soft"
                  href={siteConfig.docs.configuration}
                >
                  Configuration reference
                </a>
              </div>
              <div className="overflow-hidden rounded-lg border border-line">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] border-collapse text-left">
                    <caption className="sr-only">
                      Built-in context-management policies
                    </caption>
                    <thead className="border-b border-line bg-elevated font-mono text-[10px] uppercase tracking-[0.14em] text-dim">
                      <tr>
                        <th className="w-[22%] px-5 py-3 font-medium" scope="col">
                          Policy
                        </th>
                        <th className="w-[30%] px-5 py-3 font-medium" scope="col">
                          Trigger
                        </th>
                        <th className="px-5 py-3 font-medium" scope="col">
                          Behavior
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {policies.map((row) => (
                        <tr
                          className="border-b border-line bg-surface last:border-b-0"
                          key={row.policy}
                        >
                          <th className="px-5 py-5 text-left font-normal" scope="row">
                            <code className="font-mono text-sm font-medium text-mint">
                              {row.policy}
                            </code>
                          </th>
                          <td className="px-5 py-5 text-sm text-ink">
                            {row.trigger}
                          </td>
                          <td className="px-5 py-5 text-sm leading-6 text-muted">
                            {row.behavior}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="border-t border-line bg-elevated px-5 py-4 text-xs leading-5 text-dim">
                  Invariant: assistant actions and tool observations are retained
                  or dropped as one atomic bundle.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section
          className="scroll-mt-20 border-b border-line py-20 sm:py-24"
          id="workflow"
        >
          <div className="mx-auto max-w-[1200px] px-5 sm:px-6">
            <SectionLabel>How it works</SectionLabel>
            <h2 className="mt-5 max-w-2xl text-3xl font-semibold tracking-[-0.03em] text-ink sm:text-4xl">
              Raw history and model context are separate.
            </h2>
            <p className="mt-5 max-w-3xl text-base leading-7 text-muted">
              A policy derives a model-facing view without mutating the canonical
              trajectory. The agent uses only the declared tools, a fixed
              evaluator checks the workspace, and each worker writes its own
              evidence before aggregates are rebuilt.
            </p>
            <div className="mt-10 rounded-lg border border-line bg-surface px-5 py-5">
              <p className="whitespace-normal font-mono text-xs leading-6 text-muted">
                <span className="text-ink">Config and tasks.</span>{" "}
                Paired plan. Agent and context policy. Evaluator.{" "}
                <span className="text-mint">Report and evidence bundle.</span>
              </p>
            </div>

            <div
              className="mt-16 grid scroll-mt-24 gap-12 lg:grid-cols-[0.82fr_1.18fr] lg:gap-20"
              id="quickstart"
            >
              <div>
                <h3 className="text-2xl font-semibold tracking-tight text-ink">
                  Run the core local pipeline without an API key.
                </h3>
                <p className="mt-4 text-base leading-7 text-muted">
                  The starter uses a deterministic scripted provider to validate
                  planning, execution, traces, and reporting. It is a workflow
                  test, not a model benchmark.
                </p>
                <div className="mt-7 flex flex-wrap gap-x-6 gap-y-2">
                  <a
                    className="text-sm font-semibold text-mint hover:text-mint-soft"
                    href={siteConfig.docs.operator}
                  >
                    Operator guide
                  </a>
                  <a
                    className="text-sm font-semibold text-mint hover:text-mint-soft"
                    href={siteConfig.docs.architecture}
                  >
                    Architecture
                  </a>
                </div>
              </div>
              <div className="rounded-lg border border-line bg-surface p-5 sm:p-6">
                <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-7 text-muted">
                  <code>
                    <span className="text-dim">$</span>{" "}
                    <span className="text-ink">
                      python -m pip install scaffoldscope
                    </span>
                    {"\n\n"}
                    <span className="text-dim">$</span>{" "}
                    <span className="text-mint">scaffoldscope init</span>{" "}
                    my-study --name my-study{"\n"}
                    <span className="text-dim">$</span>{" "}
                    <span className="text-mint">scaffoldscope validate</span>{" "}
                    my-study/experiment.json{"\n"}
                    <span className="text-dim">$</span>{" "}
                    <span className="text-mint">scaffoldscope budget</span>{" "}
                    my-study/experiment.json{"\n"}
                    <span className="text-dim">$</span>{" "}
                    <span className="text-mint">scaffoldscope run</span>{" "}
                    my-study/experiment.json
                  </code>
                </pre>
              </div>
            </div>
          </div>
        </section>

        <section
          className="scroll-mt-20 border-b border-line py-20 sm:py-24"
          id="evidence"
        >
          <div className="mx-auto max-w-[1200px] px-5 sm:px-6">
            <div className="grid gap-12 lg:grid-cols-[0.72fr_1.28fr] lg:gap-20">
              <div>
                <SectionLabel>Reports and guardrails</SectionLabel>
                <h2 className="mt-5 text-3xl font-semibold tracking-[-0.03em] text-ink sm:text-4xl">
                  One report. Four evidence layers.
                </h2>
                <p className="mt-5 text-base leading-7 text-muted">
                  The report keeps performance, resource use, context behavior,
                  and validity diagnostics together without collapsing them into
                  one score.
                </p>
                <a
                  className="mt-6 inline-flex min-h-11 items-center text-sm font-semibold text-mint hover:text-mint-soft"
                  href={siteConfig.docs.results}
                >
                  Read the result schema
                </a>
              </div>
              <div>
                <dl className="divide-y divide-line border-y border-line">
                  {evidence.map(([term, description]) => (
                    <div
                      className="grid gap-2 py-5 sm:grid-cols-[0.4fr_1.6fr] sm:gap-8"
                      key={term}
                    >
                      <dt className="font-mono text-xs font-medium text-ink">
                        {term}
                      </dt>
                      <dd className="text-sm leading-6 text-muted">
                        {description}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            </div>

            <div className="mt-16 rounded-lg border border-line bg-surface p-6 sm:p-8">
              <div className="grid gap-8 lg:grid-cols-[0.6fr_1.4fr]">
                <div>
                  <p className="font-mono text-xs uppercase tracking-[0.14em] text-mint">
                    Scientific guardrails
                  </p>
                  <a
                    className="mt-5 inline-flex text-sm font-semibold text-ink hover:text-mint"
                    href={siteConfig.docs.experimentDesign}
                  >
                    Experiment-design contract
                  </a>
                </div>
                <ul className="grid gap-x-10 gap-y-5 sm:grid-cols-2">
                  {guardrails.map((item) => (
                    <li
                      className="border-l border-line-strong pl-4 text-sm leading-6 text-muted"
                      key={item}
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </section>

        <section className="border-b border-line py-20 sm:py-24">
          <div className="mx-auto grid max-w-[1200px] gap-12 px-5 sm:px-6 lg:grid-cols-2 lg:gap-20">
            <article>
              <SectionLabel>SWE-bench interoperability</SectionLabel>
              <h2 className="mt-5 text-3xl font-semibold tracking-[-0.03em] text-ink">
                Generate with ScaffoldScope. Grade with the official harness.
              </h2>
              <p className="mt-5 text-base leading-7 text-muted">
                Import downloaded task rows, run every treatment and replicate
                cell, and export a uniquely identified prediction file for each
                cell. Official SWE-bench grading remains the correctness
                authority; results return as immutable overlays.
              </p>
              <a
                className="mt-6 inline-flex min-h-11 items-center text-sm font-semibold text-mint hover:text-mint-soft"
                href={siteConfig.docs.swebench}
              >
                SWE-bench workflow
              </a>
            </article>
            <article className="border-t border-line-strong pt-7 lg:border-l lg:border-t-0 lg:pl-12 lg:pt-0">
              <SectionLabel>Project maturity</SectionLabel>
              <h2 className="mt-5 text-3xl font-semibold tracking-[-0.03em] text-ink">
                ScaffoldScope is alpha.
              </h2>
              <p className="mt-5 text-base leading-7 text-muted">
                Version {siteConfig.version} has a tested core evidence
                contract, but it does not carry a 1.0 stability promise or an
                adequately powered paid-model headline result. Use it to design
                and audit experiments today.
              </p>
              <div className="mt-6 flex flex-wrap gap-x-6 gap-y-2">
                <a
                  className="text-sm font-semibold text-mint hover:text-mint-soft"
                  href={siteConfig.release}
                >
                  v{siteConfig.version} release
                </a>
              </div>
            </article>
          </div>
        </section>
      </main>
      <SiteFooter />
      <script
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(structuredData).replaceAll("<", "\\u003c"),
        }}
        type="application/ld+json"
      />
    </>
  );
}

function SectionLabel({ children }: Readonly<{ children: string }>) {
  return (
    <p className="font-mono text-xs font-medium uppercase tracking-[0.15em] text-mint">
      {children}
    </p>
  );
}
