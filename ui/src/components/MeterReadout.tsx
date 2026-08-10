import { api, type FrameRecord, type MeterField, type RunState } from "@/lib/api"
import { Heading } from "@/components/FramePanel"
import { cn } from "@/lib/utils"

const FIELDS = ["cash", "win", "bet"] as const

/** Tesseract's own per-token confidence. The thresholds are where a value stops being
 *  worth trusting without looking at the crop underneath it. */
function confidenceTone(field: MeterField) {
  if (field.value === null) return "text-muted-foreground"
  if (field.confidence >= 85) return "text-jade"
  if (field.confidence >= 60) return "text-amber"
  return "text-vermilion"
}

function money(value: number | null) {
  if (value === null) return "—"
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function FrameRecordCard({
  run,
  which,
  record,
}: {
  run: RunState
  which: "before" | "after"
  record: FrameRecord
}) {
  const crop = run.crops[which]
  return (
    <div className="min-w-0 rounded-sm border border-rule bg-slab">
      <div className="flex items-baseline justify-between border-b border-rule px-4 py-3">
        <h4 className="eyebrow text-numeral">
          {which === "before" ? "Before the spin" : "After the spin"}
        </h4>
        {/* Which route found the meter bar. When a value looks wrong this is the first
            thing to read: a configured box that missed falls back to detection, and
            detection returning most of the screen is what loses the BET meter. */}
        <span className="tnum text-[0.6875rem] text-muted-foreground">
          {record.roi_source ?? "unknown"}
        </span>
      </div>

      {crop && (
        <div className="border-b border-rule bg-ink/60 p-2">
          <img
            src={api.fileUrl(run.run_id, crop, run.run_id)}
            alt={`The strip of the ${which} frame that was read`}
            className="mx-auto block h-auto w-full max-w-full"
          />
        </div>
      )}

      <table className="w-full text-sm">
        <thead>
          <tr className="eyebrow text-muted-foreground">
            <th className="px-4 pt-3 pb-1 text-left font-semibold">Meter</th>
            <th className="px-4 pt-3 pb-1 text-right font-semibold">Value</th>
            <th className="px-4 pt-3 pb-1 text-right font-semibold">Read as</th>
            <th className="px-4 pt-3 pb-1 text-right font-semibold">Conf.</th>
          </tr>
        </thead>
        <tbody>
          {FIELDS.map((name) => {
            const field = record[name]
            return (
              <tr key={name} className="border-t border-rule/50">
                <td className="eyebrow px-4 py-2.5 text-left text-numeral">{name}</td>
                <td
                  className={cn(
                    "tnum px-4 py-2.5 text-right text-base",
                    field.value === null ? "text-muted-foreground" : "text-numeral",
                  )}
                >
                  {money(field.value)}
                </td>
                <td className="tnum px-4 py-2.5 text-right text-xs text-muted-foreground">
                  {field.rawtext || (field.value === null ? "blank" : "—")}
                </td>
                <td className={cn("tnum px-4 py-2.5 text-right text-xs", confidenceTone(field))}>
                  {field.value === null ? "—" : `${Math.round(field.confidence)}%`}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function MeterReadout({ run }: { run: RunState }) {
  if (!run.extraction) return null
  return (
    <section>
      <Heading
        label="Meters"
        note="cropped to the meter strip, then read with Tesseract"
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <FrameRecordCard run={run} which="before" record={run.extraction.before} />
        <FrameRecordCard run={run} which="after" record={run.extraction.after} />
      </div>
    </section>
  )
}
