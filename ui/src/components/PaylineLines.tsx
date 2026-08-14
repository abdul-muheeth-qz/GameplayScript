import { Fragment, useState } from "react"

import { api, type PaylineLine, type PaylineResult } from "@/lib/api"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

/**
 * One payline at a time: its path, every COMPARE behind its verdict, and the line drawn on the
 * pixels it was read from.
 *
 * The lines are behind a dropdown rather than stacked, because five COMPARE tables is more
 * scrolling than reading -- and the picker states every line's verdict in its own row, so
 * choosing one is not blind. The selected line's cells are shown in path order with the paying
 * run marked, so the "left to right, adjacent pairs, no skipping" rule is visible rather than
 * asserted: line 4 reading E11 E22 E33 E24 E15 is a V, and a reader can check it against the
 * annotated image below it.
 *
 * **The evidence that the crop landed is `annotated_line{n}.png` and the contact sheet, not a
 * grid of tiles.** This used to open on all 15 cells for that reason; they are gone at request,
 * and what carries the argument now is the annotated image here -- the drawn path over the cells
 * it was read from -- plus the contact sheet `PaylineImages` puts above every number on the
 * page. Both show a crop half a cell out immediately, which is the one thing a table of cosines
 * can never do. If the tiles ever come back, that is the reason they were there.
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

export function PaylineLines({ run_id, result }: { run_id: string; result: PaylineResult }) {
  const [picked, setPicked] = useState<number | null>(null)
  // Clamped against the record rather than reset by an effect: a new run's lines are new
  // objects, and a line id the previous verdict had is not guaranteed to be in this one.
  const line = result.lines.find((l) => l.line === picked) ?? result.lines[0]

  if (!line) return null

  return (
    <section>
      <div className="flex flex-wrap items-center gap-3">
        <span className="eyebrow shrink-0 text-xs text-muted-foreground">Line</span>
        <div className="min-w-0 flex-1 sm:max-w-md">
          <Select value={String(line.line)} onValueChange={(value) => setPicked(Number(value))}>
            <SelectTrigger aria-label="Which payline to show">
              <SelectValue>
                <LineLabel line={line} />
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {result.lines.map((option) => (
                <SelectItem key={option.line} value={String(option.line)}>
                  <LineLabel line={option} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="mt-3">
        <LineDetail run_id={run_id} line={line} result={result} />
      </div>
    </section>
  )
}

/** One line as it reads in the picker and on the trigger: its verdict, then its shape. */
function LineLabel({ line }: { line: PaylineLine }) {
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <span className={cn("eyebrow shrink-0 text-[0.6875rem]", tone(line))}>{line.message}</span>
      <span className="truncate text-xs text-muted-foreground">{line.name}</span>
    </span>
  )
}

function LineDetail({
  run_id,
  line,
  result,
}: {
  run_id: string
  line: PaylineLine
  result: PaylineResult
}) {
  const paying = new Set(line.winning_cells)
  const annotated = result.files.annotated?.[`line${line.line}`]

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
            <Fragment key={`${step.compare[0]}-${step.compare[1]}`}>
              <tr className="border-t border-rule/40">
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
              {/* A pair the cosine could not settle. It gets its own row rather than being
                  squeezed into `detail`, because "which of these YESes came from the pixels
                  and which from the game's own reel stops" is the first thing to ask of a
                  line that only pays because the checkpoint fired. */}
              {step.checkpoint && (
                <tr>
                  <td colSpan={4} className="pb-1.5 pl-4 text-[0.6875rem] text-amber/90">
                    ↳ {step.checkpoint}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>

      {annotated && (
        <figure className="mt-4 border-t border-rule/40 pt-3">
          <figcaption className="mb-2 text-xs text-muted-foreground">
            This line drawn on the reel window it was read from
          </figcaption>
          <div className="overflow-auto rounded-sm border border-rule bg-ink/60 p-2">
            <img
              src={api.fileUrl(run_id, annotated, run_id)}
              alt={`${line.name} drawn on the reel window`}
              className="block h-auto w-full"
            />
          </div>
        </figure>
      )}
    </div>
  )
}
