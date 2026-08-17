import { api, FRAME_BLURBS, FRAME_LABELS, FRAME_STAGES, type RunState } from "@/lib/api"

/**
 * Every frame the run captured, side by side, at its own size.
 *
 * The count comes from the run, never a hardcoded pair: two on a losing spin, three when it won.
 * The game window is only a few hundred pixels wide, so these PNGs already hold every pixel there
 * is -- scaling them up would invent detail and make a bad OCR read look like a bad screenshot.
 */
export function FramePanel({ run }: { run: RunState }) {
  const frames = FRAME_STAGES.filter((k) => run.frames[k])
  if (!frames.length) return null

  const size = run.spin?.capture_size

  return (
    <section>
      <Heading
        label="Frames"
        note={size ? `${size.replace("x", " x ")}, the game window's own size` : undefined}
      />
      <div
        className={
          frames.length > 2 ? "grid gap-4 sm:grid-cols-3" : "grid gap-4 sm:grid-cols-2"
        }
      >
        {frames.map((which) => (
          <figure key={which} className="min-w-0">
            <figcaption className="mb-2">
              <span className="eyebrow text-numeral">{FRAME_LABELS[which]}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {FRAME_BLURBS[which]}
              </span>
            </figcaption>
            <div className="overflow-auto rounded-sm border border-rule bg-ink/60 p-2">
              <img
                src={api.fileUrl(run.run_id, run.frames[which]!, run.run_id)}
                alt={`The game window: ${FRAME_LABELS[which].toLowerCase()}`}
                className="mx-auto block h-auto max-w-full"
              />
            </div>
          </figure>
        ))}
      </div>
    </section>
  )
}

export function Heading({ label, note }: { label: string; note?: string }) {
  return (
    <div className="mb-3 flex items-baseline gap-3 border-b border-rule pb-2">
      <h3 className="eyebrow text-amber">{label}</h3>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </div>
  )
}
