import { api, type RunState } from "@/lib/api"

/**
 * The two frames, side by side, at their own size.
 *
 * The game runs in a portrait window a few hundred pixels wide, so these PNGs already
 * hold every pixel there is. Scaling them up would only invent detail and make a bad
 * OCR read look like a bad screenshot, so they are shown at natural width and allowed
 * to scroll. The size is read off the run rather than written here -- it changes with
 * the game window, and a caption that lies about it is worse than no caption.
 */
export function FramePanel({ run }: { run: RunState }) {
  const frames = (["before", "after"] as const).filter((k) => run.frames[k])
  if (!frames.length) return null

  const size = run.spin?.capture_size

  return (
    <section>
      <Heading
        label="Frames"
        note={size ? `${size.replace("x", " x ")}, the game window's own size` : undefined}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        {frames.map((which) => (
          <figure key={which} className="min-w-0">
            <figcaption className="eyebrow mb-2 text-numeral">
              {which === "before" ? "Before the spin" : "After the spin"}
            </figcaption>
            <div className="overflow-auto rounded-sm border border-rule bg-ink/60 p-2">
              <img
                src={api.fileUrl(run.run_id, run.frames[which]!, run.run_id)}
                alt={`The game window ${which} the spin`}
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
