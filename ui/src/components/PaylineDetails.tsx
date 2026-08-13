import { useState } from "react"
import { ChevronDown } from "lucide-react"

import { api, type PaylineResult, type PaylineTiles, type RunState } from "@/lib/api"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Figure } from "@/components/Figure"
import { PaylineLines } from "@/components/PaylineLines"
import { cn } from "@/lib/utils"

/**
 * Everything behind the verdict, folded into one disclosure below it: the reel window this
 * geometry cropped to, the contact sheet of every cell, and the line picker with each line's
 * COMPARE trail and annotated image.
 *
 * They are together because they answer one question in sequence -- did the crop land, and
 * then what did the lines do with it -- and the verdict above is the answer a reader wants
 * first. Folding them keeps the page short enough that the verdict is not below the fold.
 *
 * **The cost is that the contact sheet is now one click away rather than in front of you**, and
 * that sheet is the only thing that shows a crop half a cell out -- the check every similarity
 * number on the page depends on. It is also the only place that check now lives: the page used
 * to reach it by making "cut the reels" a button of its own, and that button is gone, so this
 * disclosure carries the whole weight of it. Hence the trigger naming what is inside rather
 * than saying "Details", and staying a single click from the verdict. If a wrong crop is ever
 * believed because nobody opened this, defaulting it open is the fix.
 */

export function PaylineDetails({
  run,
  tiles,
  result,
}: {
  run: RunState
  tiles: PaylineTiles | null
  result: PaylineResult | null
}) {
  const [open, setOpen] = useState(false)
  const files = result?.files ?? tiles?.files
  const record = result ?? tiles

  // Nothing to fold away until at least one step has produced something.
  if (!files && !result) return null

  const summary = [
    files?.reels && "reel window",
    files?.contact_sheet && "contact sheet",
    result && `${result.lines.length} paylines`,
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <section>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger
          className={cn(
            "flex w-full items-center justify-between gap-4 rounded-sm border border-rule bg-slab px-5 py-4 text-left outline-none transition-colors hover:border-rule/80 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
            open && "rounded-b-none",
          )}
        >
          <span className="min-w-0">
            <span className="eyebrow block text-numeral">
              Reels, contact sheet and the lines
            </span>
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {open
                ? "the crop every number above was read from, and each line's COMPARE trail"
                : `open to check the crop and walk each line — ${summary}`}
            </span>
          </span>
          <ChevronDown
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
            aria-hidden
          />
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="space-y-6 rounded-b-sm border border-t-0 border-rule bg-slab/40 px-5 py-5">
            {files ? (
              <div className="grid gap-4 lg:grid-cols-2">
                {files.reels && (
                  <Figure
                    caption="Reel window"
                    blurb={
                      record?.reels_size
                        ? `the region this geometry crops to — ${record.reels_size.replace("x", " x ")}`
                        : "the region this geometry crops to"
                    }
                  >
                    <img
                      src={api.fileUrl(run.run_id, files.reels, run.run_id)}
                      alt="The cropped reel window"
                      className="block h-auto w-full"
                    />
                  </Figure>
                )}
                {files.contact_sheet && (
                  <Figure
                    caption="Contact sheet"
                    blurb="check this first — a crop half a cell out invalidates every number above"
                  >
                    <img
                      src={api.fileUrl(run.run_id, files.contact_sheet, run.run_id)}
                      alt="Every cell of the grid, labelled"
                      className="block h-auto w-full"
                    />
                  </Figure>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Validate the paylines to see the reel window and the contact sheet.
              </p>
            )}

            {result && <PaylineLines run_id={run.run_id} result={result} />}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </section>
  )
}
