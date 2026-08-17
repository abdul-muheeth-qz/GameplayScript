import type { PaylineResult } from "@/lib/api"
import { Heading } from "@/components/FramePanel"
import { cn } from "@/lib/utils"

/**
 * What the lines paid, stamped the way the meter audit stamps Pass/Fail, so the two verdicts read as
 * the same kind of statement about the same spin.
 *
 * The cross-check table is not decoration: the similarity threshold is the one thing in this stage
 * chosen rather than measured, and two strategies agreeing line for line is the cheapest evidence it
 * is not doing the work. A strategy that could not run says so -- a missing row reads as agreement.
 */

export function PaylineVerdict({ result }: { result: PaylineResult }) {
  const pays = result.lines_paying > 0
  const methods = Object.entries(result.cross_check)
  const ran = methods.filter(([, check]) => check.pays)
  const skipped = methods.filter(([, check]) => check.skipped)

  // The *pixel-only* readings: `cross_check` runs over the inner matcher, so a line the checkpoint
  // rescued reads 0 here while the verdict above pays it. Correct but not self-evident, so the
  // columns say so. Folding the checkpoint in instead would make every hit print "strategies
  // disagree, recalibrate the threshold", which is wrong advice.
  const stops = result.reel_stops
  const pixelsOnly = stops?.status === "on"
  const overrides = (pixelsOnly && stops?.overrides) || 0

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

        <ReelStops result={result} />

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

            {overrides > 0 && stops?.pays_without_checkpoint && (
              <p className="mt-1.5 text-[0.6875rem] leading-relaxed text-amber/90">
                These are the readings <em>before</em> the reel-stop checkpoint, which decided{" "}
                {overrides} ambiguous COMPARE{overrides === 1 ? "" : "s"} on the game's own reel
                stops — so this table pays{" "}
                <span className="tnum">[{stops.pays_without_checkpoint.join(", ")}]</span> where
                the verdict above pays{" "}
                <span className="tnum">
                  [{result.lines.map((l) => l.pays).join(", ")}]
                </span>
                . Agreement here is about the pixels only.
              </p>
            )}

            <table className="mt-3 w-full text-xs">
              <thead>
                <tr className="eyebrow align-bottom text-muted-foreground">
                  <th className="py-1.5 text-left font-semibold">Line</th>
                  {ran.map(([name]) => (
                    <th key={name} className="py-1.5 text-right font-semibold">
                      {name}
                      {pixelsOnly && (
                        <span className="mt-0.5 block text-[0.625rem] font-normal normal-case tracking-normal text-muted-foreground/60">
                          pixels only
                        </span>
                      )}
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

/**
 * The reel-stop checkpoint: what the game's own reel stops said, and what they changed.
 *
 * Shown in all three states, because each changes how the numbers above should be read. **On and
 * having overturned something** means a YES came from symbol names rather than pixels, so the
 * pixel-only pays are printed beside it. **Unavailable** is worth being loudest about: nothing
 * failed, but an ambiguous COMPARE was settled on the pixels alone, which is what this exists to
 * correct. **Off** is stated once and quietly.
 */
function ReelStops({ result }: { result: PaylineResult }) {
  const stops = result.reel_stops
  if (!stops) return null

  if (stops.status === "unavailable") {
    return (
      <div className="mt-8 border-t border-rule pt-6">
        <h4 className="eyebrow text-xs text-amber">Reel-stop checkpoint unavailable</h4>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          An ambiguous COMPARE was decided on the pixels alone. {stops.detail}
        </p>
      </div>
    )
  }

  if (stops.status === "off") {
    return (
      <p className="mt-6 text-center text-[0.6875rem] text-muted-foreground/70">
        Reel-stop checkpoint off — payline.reel_stops.enabled is false
      </p>
    )
  }

  const reached = stops.adjudications ?? []
  const overrides = stops.overrides ?? 0

  return (
    <div className="mt-8 border-t border-rule pt-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h4 className="eyebrow text-xs text-muted-foreground">
          Reel-stop checkpoint — the game's own stops
        </h4>
        <span
          className={cn(
            "eyebrow text-[0.6875rem]",
            overrides ? "text-amber" : "text-muted-foreground/70",
          )}
        >
          {reached.length === 0
            ? "no ambiguous pair"
            : overrides
              ? `${overrides} of ${reached.length} decided against the pixels`
              : `${reached.length} checked, none changed`}
        </span>
      </div>

      <p className="tnum mt-2 text-sm text-numeral/85">
        [{(stops.stops ?? []).join(", ")}]
      </p>
      <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground/70">
        {stops.file} line {stops.line}
        {stops.timestamp && ` · ${stops.timestamp}`}
        {stops.band && ` · band cos ${stops.band[0].toFixed(2)}–${stops.band[1].toFixed(2)}`}
        <br />
        chosen by {stops.matched_by}
      </p>

      {reached.length > 0 && (
        <table className="mt-3 w-full text-xs">
          <tbody>
            {reached.map((adj) => (
              <tr
                key={`${adj.compare[0]}-${adj.compare[1]}`}
                className="border-t border-rule/40"
              >
                <td className="tnum py-1.5 pr-3 text-muted-foreground">
                  {adj.compare[0]} &amp; {adj.compare[1]}
                </td>
                <td className="tnum py-1.5 pr-3 text-right text-muted-foreground/80">
                  cos {adj.similarity.toFixed(4)}
                </td>
                <td className="py-1.5 pr-3 text-right text-numeral/80">
                  {adj.symbols.map((s) => s ?? "—").join(" vs ")}
                </td>
                <td
                  className={cn(
                    "tnum py-1.5 text-right",
                    !adj.decided
                      ? "text-muted-foreground/60"
                      : adj.now
                        ? "text-jade"
                        : "text-vermilion",
                  )}
                >
                  {adj.decided ? (adj.now ? "YES" : "NO") : "not decided"}
                  {adj.decided && adj.was !== adj.now && (
                    <span className="ml-2 text-amber">overturned</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {Boolean(overrides) && stops.pays_without_checkpoint && (
        <p className="tnum mt-3 text-[0.6875rem] text-muted-foreground/70">
          the pixels alone paid [{stops.pays_without_checkpoint.join(", ")}] · with the
          checkpoint [{result.lines.map((l) => l.pays).join(", ")}]
        </p>
      )}
    </div>
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
