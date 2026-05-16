/** A titled card used for the analytics that surround the video. */
export function StatPanel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col rounded-lg border border-neutral-800 bg-neutral-900/60 p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        {title}
      </h2>
      <div className="flex-1 text-sm text-neutral-200">{children}</div>
    </div>
  );
}

/** A single big-number metric. */
export function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between border-b border-neutral-800 py-1.5 last:border-0">
      <span className="text-neutral-400">{label}</span>
      <span className="font-mono text-base font-semibold text-emerald-400">
        {value}
      </span>
    </div>
  );
}
