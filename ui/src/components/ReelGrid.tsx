import { api, type PaylineLine, type PaylineResult } from "@/lib/api"
import { Heading } from "@/components/FramePanel"
import { cn } from "@/lib/utils"

/**
 * The grid as it was read, and every COMPARE behind each line's verdict.
 *
 * The tiles themselves are the grid, not a rendering of it. A symbol name would be shorter,
 * but only the `library` matcher can produce one and it needs reference art nobody has cut
 * yet -- and more importantly, the pixels are the evidence. If the crop is half a cell out
 * this is where it shows, and a table of names would hide exactly that.
 *
 * Each line's row shows its cells in path order with the paying run marked, so the "left to
 * right, adjacent pairs, no skipping" rule is visible rather than asserted: line 4 reading
 * E11 E22 E33 E24 E15 is a V, and a reader can check it against the grid above.
 */

const LINE_TONES = [
  "text-jade",
  "text-amber",
  "text-numeral",
  "text-amber",
  "text-jade",
]

function tone(line: PaylineLine) {
  return line.wins ? LINE_TONES[(line.line - 1) % LINE_TONES.length] : "text-muted-foreground"
}

export function ReelGrid({ run_id, result }: { run_id: string; result: PaylineResult }) {
  const [rows, reels] = result.geometry.grid.split("x").map(Number)
  const tiles = result.files.tiles
  const labels = result.symbol_grid ?? {}

  return (
    <section>
      <Heading
        label="The grid"
        note={`${result.geometry.grid} cells cut from a ${result.reels_size} reel window`}
      />

      <div className="rounded-sm border border-rule bg-slab p-3">
        <div
          className="grid gap-1.5"
          style={{ gridTemplateColumns: `repeat(${reels}, minmax(0, 1fr))` }}
        >
          {Array.from({ length: rows }, (_, r) =>
            Array.from({ length: reels }, (_, c) => {
              const cell = `E${r + 1}${c + 1}`
              const path = tiles?.[cell]
              return (
                <figure key={cell} className="min-w-0">
                  <div className="overflow-hidden rounded-xs border border-rule bg-ink/60">
                    {path ? (
                      <img
                        src={api.fileUrl(run_id, path, run_id)}
                        alt={`Cell ${cell}`}
                        className="block aspect-square h-auto w-full object-contain"
                      />
                    ) : (
                      <div className="aspect-square" />
                    )}
                  </div>
                  <figcaption className="mt-1 flex items-baseline justify-between gap-1 px-0.5">
                    <span className="tnum text-[0.625rem] text-muted-foreground">{cell}</span>
                    {labels[cell] && (
                      <span className="eyebrow truncate text-[0.5625rem] text-numeral/70">
                        {labels[cell]}
                      </span>
                    )}
                  </figcaption>
                </figure>
              )
            }),
          )}
        </div>
      </div>

      <div className="mt-6 space-y-3">
        {result.lines.map((line) => (
          <LineRow key={line.line} line={line} />
        ))}
      </div>
    </section>
  )
}

function LineRow({ line }: { line: PaylineLine }) {
  const paying = new Set(line.winning_cells)
  return (
    <div
      className={cn(
        "rounded-sm border bg-slab px-4 py-3",
        line.wins ? "border-rule" : "border-rule/50",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex items-baseline gap-3">
          <span className={cn("eyebrow text-sm", tone(line))}>{line.message}</span>
          <span className="text-xs text-muted-foreground">{line.name}</span>
        </div>
        {line.symbols.length > 0 && (
          <span className="eyebrow text-[0.6875rem] text-numeral/70">
            {line.symbols.join(", ")}
          </span>
        )}
      </div>

      {/* The path, in order, with the paying run filled and the cell that broke it ruled
          out. This is the whole rule in one line of type. */}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {line.cells.map((cell, i) => (
          <span key={cell} className="flex items-center gap-1">
            {i > 0 && <span className="text-muted-foreground/40">›</span>}
            <span
              className={cn(
                "tnum rounded-xs border px-1.5 py-0.5 text-[0.6875rem]",
                paying.has(cell)
                  ? "border-jade/60 bg-jade/10 text-jade"
                  : cell === line.broken_at
                    ? "border-vermilion/50 bg-vermilion/5 text-vermilion"
                    : "border-rule/60 text-muted-foreground",
              )}
            >
              {cell}
            </span>
          </span>
        ))}
      </div>

      <table className="mt-3 w-full text-xs">
        <tbody>
          {line.steps.map((step, i) => (
            <tr key={`${step.compare[0]}-${step.compare[1]}`} className="border-t border-rule/40">
              <td className="tnum py-1.5 pr-3 text-muted-foreground">
                COMPARE {step.compare[0]} &amp; {step.compare[1]}
              </td>
              <td
                className={cn(
                  "tnum py-1.5 pr-3 text-right",
                  step.match ? "text-jade" : "text-vermilion",
                )}
              >
                {step.match ? "YES" : "NO"}
              </td>
              <td className="tnum py-1.5 pr-3 text-right text-muted-foreground/80">
                {step.detail}
              </td>
              <td className="py-1.5 text-right text-[0.6875rem] text-muted-foreground/60">
                {!step.match
                  ? i === 0
                    ? "stop"
                    : "run ends"
                  : i === 0
                    ? "counter = 2"
                    : "counter +1"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
