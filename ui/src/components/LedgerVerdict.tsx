import type { Verdict } from "@/lib/api"
import { Heading } from "@/components/FramePanel"
import { cn } from "@/lib/utils"

/**
 * The whole product answers one arithmetic question, so the arithmetic is the hero:
 * the three meters worked down a column, ruled off, and compared against what the
 * cabinet actually showed afterwards -- the shape of the audit slip this replaces.
 */

const STAMP = {
  pass: { word: "Pass", tone: "text-jade border-jade" },
  fail: { word: "Fail", tone: "text-vermilion border-vermilion" },
  error: { word: "No verdict", tone: "text-amber border-amber" },
} as const

function amount(value: string | null) {
  if (value === null) return "—"
  const n = Number(value)
  if (Number.isNaN(n)) return value
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function Row({
  sign,
  label,
  value,
  assumed,
  result,
}: {
  sign?: string
  label: string
  value: string
  assumed?: boolean
  /** The two lines below the rule: what the sum came to, and what the cabinet showed.
   *  Set brighter than the inputs, because those two are the comparison being made. */
  result?: boolean
}) {
  return (
    <div className="flex items-baseline gap-4">
      <span className="tnum w-4 shrink-0 text-right text-lg text-muted-foreground">
        {sign ?? ""}
      </span>
      <span
        className={cn(
          "tnum flex-1 text-right text-2xl sm:text-3xl",
          result ? "font-semibold text-white" : "text-numeral/85",
        )}
      >
        {value}
      </span>
      <span
        className={cn(
          "flex w-40 shrink-0 items-baseline gap-2 text-xs",
          result ? "text-numeral" : "text-muted-foreground",
        )}
      >
        {label}
        {assumed && (
          <span
            className="eyebrow rounded-xs border border-amber/50 px-1 py-px text-[0.5625rem] text-amber"
            title="The meter read blank, which means nothing was won, so it was taken as zero."
          >
            assumed
          </span>
        )}
      </span>
    </div>
  )
}

export function LedgerVerdict({ verdict }: { verdict: Verdict }) {
  const stamp = STAMP[verdict.verdict]
  const [cash, win, bet] = (verdict.record ?? ",,").split(",")
  const assumed = new Set(verdict.inferred)
  const difference = verdict.difference === null ? null : Number(verdict.difference)

  return (
    <section>
      <Heading label="Verdict" note={verdict.formula} />

      <div className="rounded-sm border border-rule bg-slab p-6 sm:p-8">
        {verdict.record ? (
          <div className="mx-auto max-w-xl space-y-2">
            <Row value={amount(cash)} label="cash before" />
            <Row sign="+" value={amount(win)} label="win standing" assumed={assumed.has("win")} />
            <Row sign="−" value={amount(bet)} label="bet placed" assumed={assumed.has("bet")} />

            {/* The rule draws itself once, the way a subtotal line gets struck. */}
            <div className="flex items-center gap-4 py-1">
              <span className="w-4 shrink-0" />
              <span className="h-px flex-1 origin-right animate-[var(--rule-draw)] bg-numeral/60 [--rule-draw:rule-draw_450ms_ease-out]" />
              <span className="w-40 shrink-0" />
            </div>

            <Row result value={amount(verdict.computed_cash)} label="should be" />
            <Row result value={amount(verdict.expected_cash)} label="the meter read" />

            {difference !== null && difference !== 0 && (
              <div className="flex items-baseline gap-4 pt-1">
                <span className="w-4 shrink-0" />
                <span className="tnum flex-1 text-right text-sm text-vermilion">
                  out by {amount(String(Math.abs(difference)))}
                </span>
                <span className="w-40 shrink-0 text-xs text-muted-foreground">
                  tolerance {verdict.tolerance}
                </span>
              </div>
            )}
          </div>
        ) : (
          <p className="text-center text-sm text-muted-foreground">
            The meters could not be read as numbers, so there was nothing to add up.
          </p>
        )}

        <div className="mt-8 flex flex-col items-center gap-3">
          <div
            className={cn(
              "eyebrow border-2 px-8 py-3 text-2xl tracking-[0.28em]",
              stamp.tone,
            )}
            role="status"
          >
            {stamp.word}
          </div>
          {/* Only when the sum above isn't there to speak for itself. With a record on
              screen the message says the same thing a second time in worse words. */}
          {!verdict.record && verdict.message && (
            <p className="max-w-prose text-center text-sm text-muted-foreground">
              {verdict.message}
            </p>
          )}
          {verdict.model && (
            <p className="text-[0.6875rem] text-muted-foreground/70">
              arithmetic checked by {verdict.model}
            </p>
          )}
        </div>
      </div>
    </section>
  )
}
