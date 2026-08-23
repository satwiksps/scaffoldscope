const fixedFields = ["model", "task", "evaluator", "budget", "replicate"] as const;

const evidence = [
  ["Plan", "declared cells and identities"],
  ["Traces", "prompts, tool calls, and usage"],
  ["Outcomes", "patches, status, and evaluator result"],
  ["Bundle", "report and checksummed archive"],
] as const;

export function ProjectWindow() {
  return (
    <figure className="overflow-hidden rounded-xl border border-white/10 bg-[#0c0c0f] shadow-[0_32px_100px_rgba(0,0,0,0.55)]">
      <figcaption className="sr-only">
        ScaffoldScope paired experiment design
      </figcaption>
      <div className="flex h-12 items-center justify-between border-b border-white/[0.07] bg-[#111114] px-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-6 shrink-0 place-items-center rounded border border-white/10 bg-white/[0.03] font-mono text-[9px] font-bold text-blue-300">
            SS
          </span>
          <span className="truncate text-xs font-medium text-zinc-300">
            Paired experiment plan
          </span>
          <span className="hidden text-xs text-zinc-400 sm:inline">/</span>
          <span className="hidden font-mono text-[11px] text-zinc-400 sm:inline">
            my-study/experiment.json
          </span>
        </div>
        <span className="shrink-0 font-mono text-[10px] text-blue-300">
          PAIRED DESIGN
        </span>
      </div>

      <div className="grid lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3 sm:px-5">
            <span className="font-mono text-[11px] text-zinc-400">
              task-001 · replicate 1
            </span>
            <span className="font-mono text-[10px] text-zinc-400">
              identical starting state
            </span>
          </div>

          <div className="p-4 sm:p-5">
            <div className="rounded-md border border-white/[0.08] bg-black/20 px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-blue-300">
                  Shared controls
                </span>
                <span className="font-mono text-[10px] text-zinc-400">
                  model · task · evaluator · budget · replicate
                </span>
              </div>
            </div>

            <div className="my-2 flex justify-center" aria-hidden="true">
              <span className="h-5 w-px bg-white/15" />
            </div>

            <div className="grid gap-px overflow-hidden rounded-md border border-white/[0.08] bg-white/[0.08] sm:grid-cols-2">
              <article className="bg-[#0b0b0e] p-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400">
                    A · Control
                  </span>
                  <span className="font-mono text-[9px] text-zinc-500">
                    cell 1
                  </span>
                </div>
                <p className="mt-4 font-mono text-xs text-zinc-200">
                  context_policy = <span className="text-blue-300">&quot;none&quot;</span>
                </p>
                <p className="mt-2 text-xs leading-5 text-zinc-400">
                  Retain the full canonical history until typed overflow.
                </p>
              </article>
              <article className="bg-[#0b0b0e] p-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-blue-300">
                    B · Treatment
                  </span>
                  <span className="font-mono text-[9px] text-zinc-500">
                    cell 2
                  </span>
                </div>
                <p className="mt-4 font-mono text-xs text-zinc-200">
                  context_policy = <span className="text-blue-300">&quot;reactive&quot;</span>
                </p>
                <p className="mt-2 text-xs leading-5 text-zinc-400">
                  Compact history at the declared utilization threshold.
                </p>
              </article>
            </div>

            <div className="my-2 flex justify-center" aria-hidden="true">
              <span className="h-5 w-px bg-white/15" />
            </div>

            <div className="rounded-md border border-blue-400/20 bg-blue-400/[0.035] px-4 py-3">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-blue-300">
                  Compare paired cells
                </span>
                <span className="text-xs text-zinc-400">
                  one declared harness field changed
                </span>
              </div>
            </div>
          </div>

          <div className="border-t border-white/[0.07] bg-black/20 px-4 py-4 font-mono text-[11px] sm:px-5 sm:text-xs">
            <div className="flex min-w-0 gap-3">
              <span className="select-none text-blue-300">$</span>
              <code className="truncate text-zinc-300">
                scaffoldscope run my-study/experiment.json
              </code>
            </div>
            <p className="mt-2 text-zinc-400">
              Execute every treatment from the same task and replicate state.
            </p>
          </div>
        </div>

        <aside className="border-t border-white/[0.07] bg-[#0a0a0d] lg:border-l lg:border-t-0">
          <div className="flex h-12 items-center justify-between border-b border-white/[0.07] px-5">
            <span className="text-xs font-medium text-zinc-300">
              Experiment contract
            </span>
            <span className="font-mono text-[9px] uppercase tracking-wider text-blue-300">
              one change
            </span>
          </div>
          <div className="p-5">
            <div>
              <p className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">
                Held fixed
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {fixedFields.map((field) => (
                  <span
                    className="rounded border border-white/[0.08] bg-white/[0.025] px-2 py-1 font-mono text-[10px] text-zinc-400"
                    key={field}
                  >
                    {field}
                  </span>
                ))}
              </div>
            </div>

            <dl className="mt-5 divide-y divide-white/[0.07] border-y border-white/[0.07]">
              {evidence.map(([term, description]) => (
                <div className="py-3" key={term}>
                  <dt className="font-mono text-[10px] font-semibold text-zinc-200">
                    {term}
                  </dt>
                  <dd className="mt-1 text-[11px] leading-5 text-zinc-400">
                    {description}
                  </dd>
                </div>
              ))}
            </dl>

            <div className="mt-5 border-l-2 border-blue-400/50 pl-3">
              <p className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">
                Question answered
              </p>
              <p className="mt-1.5 text-xs leading-5 text-zinc-400">
                Did the declared harness treatment change outcomes, resource use,
                or context behavior on the same work?
              </p>
            </div>
          </div>
        </aside>
      </div>
    </figure>
  );
}
