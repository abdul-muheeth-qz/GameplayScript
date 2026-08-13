import { useCallback, useEffect, useState } from "react"
import { AlertCircle } from "lucide-react"

import { api, type Health, type PaylineSource, type RunState } from "@/lib/api"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Detail, PageShell, type Mode } from "@/components/PageShell"
import { PaylineImages } from "@/components/PaylineImages"
import { PaylineVerdict } from "@/components/PaylineVerdict"
import { ReelGrid } from "@/components/ReelGrid"
import { StepRail, type Step, type StepStatus } from "@/components/StepRail"

/**
 * The payline audit: read the reel grid off an image and walk the lines.
 *
 * **It does not spin.** The image is whatever the server says it would read -- the newest
 * capture's `spin_result` by default, or a supplied `payline.image` when one is configured --
 * and it is fetched and shown on load, so the page opens on the picture it is about to judge
 * rather than on an empty state and a button. Capturing belongs to the Meter Validation tab;
 * duplicating it here put OBS, the i-Deck and a three-minute wait in front of an audit that
 * needs none of them.
 *
 * Two steps, and the first is optional in practice: validating cuts the tiles itself if they
 * are not there, so this is one click when you trust the crop and two when you want to check
 * the contact sheet first. That check is worth keeping a button for -- every similarity number
 * below it is meaningless if the crop is half a cell out.
 */

type Stage = "tiles" | "payline"

export function PaylineValidation({
  mode,
  onMode,
  run,
  onRun,
}: {
  mode: Mode
  onMode: (mode: Mode) => void
  run: RunState | null
  onRun: (run: RunState | null) => void
}) {
  const [health, setHealth] = useState<Health | null>(null)
  const [source, setSource] = useState<PaylineSource | null>(null)
  const [busy, setBusy] = useState<Stage | null>(null)
  const [failed, setFailed] = useState<Stage | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshHealth = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  useEffect(refreshHealth, [refreshHealth])

  // What the server says it would read: the source label, and whether there is anything to read
  // at all. Adopting the newest run is App's job, not this page's -- see App.tsx for why the
  // URL has to settle first. This is only for what gets said and shown.
  useEffect(() => {
    api.paylineSource().then(setSource).catch(() => setSource(null))
  }, [])

  async function step(stage: Stage, call: () => Promise<RunState>) {
    setBusy(stage)
    setFailed(null)
    setError(null)
    try {
      onRun(await call())
    } catch (exc) {
      setFailed(stage)
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(null)
    }
  }

  function statusOf(stage: Stage, unlocked: boolean, done: boolean): StepStatus {
    if (busy === stage) return "running"
    if (failed === stage) return "failed"
    if (done) return "done"
    return unlocked && !busy ? "ready" : "locked"
  }

  const tiled = Boolean(run?.payline_tiles)
  const validated = Boolean(run?.payline)
  // Something to read: the run on screen has a frame, or the server named a readable source.
  const readable =
    Boolean(run?.frames.spin_result) || Boolean(source?.readable)

  // The reel geometry is per-game and never falls back to another game's numbers, so a missing
  // block is worth saying before a button is pressed rather than after.
  const geometry = health?.checks?.payline

  const steps: Step[] = [
    {
      ordinal: "01",
      name: "Reels",
      blurb: "Crops the reel window out of the image and cuts it into one tile per cell. Check the contact sheet before trusting anything below it.",
      action: busy === "tiles" ? "Cutting…" : "Cut the reels",
      status: statusOf("tiles", readable, tiled),
      detail: run?.payline_tiles && (
        <dl className="space-y-1">
          <Detail term="Cells" value={String(run.payline_tiles.cells)} />
          <Detail term="Reels" value={run.payline_tiles.reels_size} />
          <Detail term="Tile" value={run.payline_tiles.tile_size} />
          <Detail term="Geometry" value={run.payline_tiles.geometry.label} />
        </dl>
      ),
      onRun: () => step("tiles", () => api.paylineTiles(run?.run_id)),
    },
    {
      ordinal: "02",
      name: "Paylines",
      blurb: "Embeds every tile, decides which cells hold the same symbol, and counts each line's run from reel 1.",
      action: busy === "payline" ? "Matching…" : "Validate paylines",
      status: statusOf("payline", readable, validated),
      detail: run?.payline && (
        <dl className="space-y-1">
          <Detail
            term="Pays"
            value={`${run.payline.lines_paying} of ${run.payline.lines.length} lines`}
          />
          <Detail term="Total" value={String(run.payline.total_pay)} />
          <Detail term="Matching" value={run.payline.matcher} />
          <Detail
            term="Agree"
            value={
              run.payline.agreement === null
                ? null
                : run.payline.agreement
                  ? "all strategies"
                  : "no — recalibrate"
            }
          />
        </dl>
      ),
      onRun: () => step("payline", () => api.payline(run?.run_id)),
    },
  ]

  return (
    <PageShell
      mode={mode}
      onMode={onMode}
      title="Payline audit"
      subtitle="does the grid pay its lines"
      runId={run?.run_id}
      health={health}
      rail={<StepRail steps={steps} />}
    >
      {geometry && !geometry.ok && (
        <Alert className="border-amber/50 bg-amber/5">
          <AlertCircle className="size-4 text-amber" />
          <AlertTitle className="eyebrow text-amber">No reel geometry</AlertTitle>
          <AlertDescription className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground">
            {geometry.detail}
          </AlertDescription>
        </Alert>
      )}

      {source && !source.readable && (
        <Alert className="border-amber/50 bg-amber/5">
          <AlertCircle className="size-4 text-amber" />
          <AlertTitle className="eyebrow text-amber">Nothing to read</AlertTitle>
          <AlertDescription className="text-xs leading-relaxed text-muted-foreground">
            {source.detail}. Capture a spin from the Meter Validation tab, or set{" "}
            <span className="tnum">payline.image</span> in config.json to an image to validate.
          </AlertDescription>
        </Alert>
      )}

      {error && (
        <Alert className="border-vermilion/50 bg-vermilion/5">
          <AlertCircle className="size-4 text-vermilion" />
          <AlertTitle className="eyebrow text-vermilion">
            {failed === "payline" ? "Payline validation" : "Cutting the reels"} did not finish
          </AlertTitle>
          <AlertDescription className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground">
            {error}
          </AlertDescription>
        </Alert>
      )}

      {(run || source?.supplied) && (
        <PaylineImages
          run={run}
          tiles={run?.payline_tiles ?? null}
          result={run?.payline ?? null}
          source={source}
        />
      )}

      {run?.payline && <PaylineVerdict result={run.payline} />}
      {run?.payline && <ReelGrid run_id={run.run_id} result={run.payline} />}

      {/* Both audits' results live in the same run folder and neither gates the other, so the
          meter side's answer is worth a line once it exists -- and a spin whose capture timed
          out is worth flagging, because the game log then has nothing to say about the outcome
          and this reading is the only evidence there is. */}
      {run?.payline && <Alongside run={run} />}
    </PageShell>
  )
}

function Alongside({ run }: { run: RunState }) {
  const payline = run.payline!
  const meter = run.validation
  const timedOut = run.spin && !run.spin.terminal_event
  const gameSaidWon = run.spin?.won

  if (!meter && !timedOut && (gameSaidWon === undefined || gameSaidWon === null)) return null

  return (
    <div className="rounded-sm border border-rule bg-slab/60 px-5 py-4">
      <h4 className="eyebrow text-xs text-muted-foreground">Alongside</h4>
      <dl className="mt-2 grid gap-x-8 gap-y-1.5 text-xs sm:grid-cols-2">
        <Pair
          term="This audit"
          value={`${payline.lines_paying} of ${payline.lines.length} lines pay`}
        />
        {gameSaidWon !== undefined && gameSaidWon !== null && (
          <Pair term="The game's log" value={gameSaidWon ? "a win" : "no win"} />
        )}
        {meter && (
          <Pair
            term="Meter audit"
            value={
              meter.verdict === "pass"
                ? "Pass"
                : meter.verdict === "fail"
                  ? `Fail, out by ${meter.difference}`
                  : "No verdict"
            }
          />
        )}
        {run.extraction?.spin_result && (
          <Pair
            term="WIN meter read"
            value={
              run.extraction.spin_result.win.value === null
                ? "blank"
                : run.extraction.spin_result.win.value.toFixed(2)
            }
          />
        )}
      </dl>
      {timedOut && (
        <p className="mt-3 text-xs text-amber">
          This capture ended on a timeout rather than a terminal event, so the game's log never
          reported an outcome for it — “no win” above means “nothing was seen”, not “nothing was
          won”. The grid on the frame is the only evidence there is.
        </p>
      )}
    </div>
  )
}

function Pair({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-32 shrink-0 text-muted-foreground/70">{term}</dt>
      <dd className="tnum min-w-0 text-numeral/85">{value}</dd>
    </div>
  )
}
