import type { PaylineResult } from "@/lib/api"
import { Heading } from "@/components/FramePanel"
import { cn } from "@/lib/utils"

/**
 * What the lines paid, stamped the way the meter audit stamps Pass/Fail -- so the two
 * verdicts read as the same kind of statement about the same spin.
 *
 * The cross-check table is not decoration. Every number here rests on a similarity
 * threshold, and a threshold is the one thing in this stage that was chosen rather than
 * measured off the frame. Two independent strategies agreeing line for line is the cheapest
 * evidence that the threshold is not doing the work; disagreement means recalibrate before
 * believing the pays. A strategy that could not run says so, because a missing row would
 * read as agreement.
 */

export function PaylineVerdict({ result }: { result: PaylineResult }) {
  const pays = result.lines_paying > 0
  const methods = Object.entries(result.cross_check)
  const ran = methods.filter(([, check]) => check.pays)
  const skipped = methods.filter(([, check]) => check.skipped)

  return (
    <section>
      <Heading
        label="Payline verdict"
        note="COMPARE adjacent cells left to right; the counter is the length of the run from reel 1"
      />

      <div className="rounded-sm border border-rule bg-slab p-6 sm:p-8">
        <div className="mx-auto flex max-w-xl flex-col items-center gap-6">
          <div className="flex items-baseline gap-8">
            <Figure value={String(result.lines_paying)} of={String(result.lines.length)} label="lines pay" />
            {/* `total_pay` is the sum of the paying runs -- the paying symbol count across every
                line that won, not a currency amount. The label says Total Pay because that is
                what the CLI's summary calls it and what the audit trail is read against; the
                unit is spelled out underneath so nobody reads it as money. */}
            <Figure value={String(result.total_pay)} label="total pay" sub="paying symbols" />
          </div>

          <div
            className={cn(
              "eyebrow border-2 px-8 py-3 text-2xl tracking-[0.28em]",
              pays ? "border-jade text-jade" : "border-amber text-amber",
            )}
            role="status"
          >
            {pays ? "Pays" : "No pay"}
          </div>

          <p className="max-w-prose text-center text-sm text-muted-foreground">
            {result.message}
          </p>
        </div>

        {ran.length > 1 && (
          <div className="mt-8 border-t border-rule pt-6">
            <div className="flex items-baseline justify-between gap-4">
              <h4 className="eyebrow text-xs text-muted-foreground">
                Cross-check — do the strategies agree?
              </h4>
              <span
                className={cn(
                  "eyebrow text-[0.6875rem]",
                  result.agreement ? "text-jade" : "text-vermilion",
                )}
              >
                {result.agreement ? "all agree" : "disagree"}
              </span>
            </div>
            <table className="mt-3 w-full text-xs">
              <thead>
                <tr className="eyebrow text-muted-foreground">
                  <th className="py-1.5 text-left font-semibold">Line</th>
                  {ran.map(([name]) => (
                    <th key={name} className="py-1.5 text-right font-semibold">
                      {name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.lines.map((line, i) => {
                  const values = ran.map(([, check]) => check.pays![i])
                  const same = new Set(values).size === 1
                  return (
                    <tr key={line.line} className="border-t border-rule/40">
                      <td className="tnum py-1.5 text-left text-muted-foreground">
                        {line.line}
                      </td>
                      {values.map((value, j) => (
                        <td
                          key={ran[j][0]}
                          className={cn(
                            "tnum py-1.5 text-right",
                            same ? "text-numeral/80" : "text-vermilion",
                          )}
                        >
                          pays {value}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {skipped.length > 0 && (
          <dl className="mt-6 space-y-1.5 border-t border-rule pt-4 text-[0.6875rem] text-muted-foreground/70">
            {skipped.map(([name, check]) => (
              <div key={name} className="flex gap-2">
                <dt className="shrink-0">cross-check “{name}” skipped:</dt>
                <dd className="min-w-0">{check.skipped}</dd>
              </div>
            ))}
          </dl>
        )}

        <p className="mt-6 text-center text-[0.6875rem] text-muted-foreground/70">
          {result.matcher} over {result.backend} embeddings ·{" "}
          {result.geometry.label} geometry for {result.geometry.process}
          {result.geometry.measured_on && `, measured on ${result.geometry.measured_on}`}
        </p>
      </div>
    </section>
  )
}

function Figure({
  value,
  of,
  label,
  sub,
}: {
  value: string
  of?: string
  label: string
  sub?: string
}) {
  return (
    <div className="text-center">
      <p className="tnum text-4xl font-semibold text-white">
        {value}
        {of && <span className="text-xl text-muted-foreground"> / {of}</span>}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{label}</p>
      {sub && <p className="text-[0.625rem] text-muted-foreground/60">{sub}</p>}
    </div>
  )
}
