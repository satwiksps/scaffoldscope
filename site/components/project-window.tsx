const artifacts = [
  ["manifest.json", "experiment identity"],
  ["plan.jsonl", "paired trial plan"],
  ["episodes.jsonl", "aggregate outcomes"],
  ["report.html", "readable report"],
  ["trials/", "per-trial evidence"],
] as const;

export function ProjectWindow() {
  return (
    <figure className="overflow-hidden rounded-xl border border-white/10 bg-[#0c0c0f] shadow-[0_32px_100px_rgba(0,0,0,0.55)]">
      <figcaption className="sr-only">
        Illustrative ScaffoldScope offline experiment report
      </figcaption>
      <div className="flex h-12 items-center justify-between border-b border-white/[0.07] bg-[#111114] px-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-6 shrink-0 place-items-center rounded border border-white/10 bg-white/[0.03] font-mono text-[9px] font-bold text-blue-300">
            SS
          </span>
          <span className="truncate text-xs font-medium text-zinc-300">
            Experiment report
          </span>
          <span className="hidden text-xs text-zinc-400 sm:inline">/</span>
          <span className="hidden font-mono text-[11px] text-zinc-400 sm:inline">
            runs/offline-quickstart-1b212f14
          </span>
        </div>
        <span className="flex shrink-0 items-center gap-2 font-mono text-[10px] text-emerald-300">
          <i
            aria-hidden="true"
            className="size-1.5 rounded-full bg-emerald-400"
          />
          RUN COMPLETE
        </span>
      </div>

      <div className="grid lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3 sm:px-5">
            <span className="font-mono text-[11px] text-zinc-400">
              offline-quickstart
            </span>
            <span className="font-mono text-[10px] text-zinc-400">
              illustrative demo output
            </span>
          </div>

          <div className="grid gap-px border-b border-white/[0.07] bg-white/[0.07] sm:grid-cols-3">
            <div className="bg-[#0b0b0e] px-5 py-5">
              <strong className="block font-mono text-lg font-semibold text-zinc-100">
                12 / 12
              </strong>
              <span className="mt-1 block text-[11px] text-zinc-400">
                recorded trials
              </span>
            </div>
            <div className="bg-[#0b0b0e] px-5 py-5">
              <strong className="block font-mono text-lg font-semibold text-zinc-100">
                3 / 3
              </strong>
              <span className="mt-1 block text-[11px] text-zinc-400">
                paired blocks
              </span>
            </div>
            <div className="bg-[#0b0b0e] px-5 py-5">
              <strong className="block font-mono text-lg font-semibold text-zinc-100">
                27,477
              </strong>
              <span className="mt-1 block text-[11px] text-zinc-400">
                recorded tokens
              </span>
            </div>
          </div>

          <div className="overflow-x-auto px-4 py-5 font-mono text-[11px] leading-6 sm:px-5 sm:py-7 sm:text-xs">
            <div className="flex min-w-[620px] gap-3">
              <span className="select-none text-blue-300">$</span>
              <code className="text-zinc-300">
                scaffoldscope status runs/offline-quickstart-1b212f14
              </code>
            </div>
            <div className="mt-3 min-w-[620px] space-y-1 text-zinc-400">
              <p>
                Progress: <span className="text-emerald-300">12 / 12 (100.0%)</span>;
                remaining: 0
              </p>
              <p>
                Paired blocks: <span className="text-emerald-300">3 / 3 (100.0%)</span>
              </p>
              <p>Outcomes: context_overflow=3, resolved=9</p>
            </div>
          </div>

          <div className="border-t border-white/[0.07] bg-black/20 px-4 py-4 font-mono text-[11px] sm:px-5 sm:text-xs">
            <div className="flex min-w-0 gap-3">
              <span className="select-none text-blue-300">$</span>
              <code className="truncate text-zinc-300">
                scaffoldscope check runs/offline-quickstart-1b212f14
              </code>
            </div>
            <p className="mt-2 text-emerald-300">
              PASS: result bundle is complete and internally consistent.
            </p>
          </div>
        </div>

        <aside className="border-t border-white/[0.07] bg-[#0a0a0d] lg:border-l lg:border-t-0">
          <div className="flex h-12 items-center justify-between border-b border-white/[0.07] px-5">
            <span className="text-xs font-medium text-zinc-300">
              Generated evidence
            </span>
            <span className="grid size-5 place-items-center rounded bg-white/[0.06] font-mono text-[10px] text-zinc-400">
              5
            </span>
          </div>
          <div className="p-5">
            <ul className="divide-y divide-white/[0.07]">
              {artifacts.map(([name, description]) => (
                <li className="py-3 first:pt-1" key={name}>
                  <p className="font-mono text-xs text-zinc-200">{name}</p>
                  <p className="mt-1 text-[11px] text-zinc-400">
                    {description}
                  </p>
                </li>
              ))}
            </ul>
            <div className="mt-4 border-l-2 border-amber-400/50 pl-3">
              <p className="font-mono text-[9px] uppercase tracking-wider text-amber-300">
                Study warnings
              </p>
              <p className="mt-1.5 font-mono text-[10px] leading-5 text-zinc-400">
                SCRIPTED_PROVIDER, LOW_TASK_COUNT, LOW_POWER
              </p>
            </div>
          </div>
        </aside>
      </div>
    </figure>
  );
}
