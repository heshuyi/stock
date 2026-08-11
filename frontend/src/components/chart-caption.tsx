/** Title + short note explaining what a chart is for. */
export function ChartCaption({ title, note }: { title: string; note: string }) {
  return (
    <div className="mb-2 shrink-0">
      <p className="text-sm font-medium text-ink/70">{title}</p>
      <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-ink/55 sm:line-clamp-none">
        {note}
      </p>
    </div>
  );
}
