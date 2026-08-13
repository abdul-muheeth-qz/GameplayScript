import { useCallback, useEffect, useState } from "react"
import { AlertCircle } from "lucide-react"

import { api, type Health, type PaylineSource, type RunState } from "@/lib/api"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Detail, PageShell, type Mode } from "@/components/PageShell"
import { PaylineDetails } from "@/components/PaylineDetails"
import { PaylineImages } from "@/components/PaylineImages"
import { PaylineVerdict } from "@/components/PaylineVerdict"
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
 * **One step, at request.** There used to be two buttons -- cut the reels, then validate -- and
 * the first was already optional: `runner.validate_paylines` cuts the tiles itself whenever they
 * are missing, or were cut from a different image or geometry (`_tiles_are_current`), so the
 * single call does the crop and the matching and writes both `tiles.json` and `payline.json`.
 * Removing the button removed a click, not a stage.
 *
 * What the two buttons were *for* was making the contact sheet unavoidable -- every similarity
 * number here is meaningless if the crop is half a cell out, and that sheet is the only thing
 * that shows it. It is now in the disclosure below the verdict instead of in front of it. The
 * pipeline is still two steps and still says so: `payline/tiles.json` is its own output, and
 * `python -m server.payline.cli --tiles-only` and `POST /api/payline/tiles` both still stop
 * after the crop. Only the page collapsed them.
 */

type Stage = "payline"

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

  const validated = Boolean(run?.payline)
  // Something to read: the run on screen has a frame, or the server named a readable source.
  const readable =
    Boolean(run?.frames.spin_result) || Boolean(source?.readable)

  // The reel geometry is per-game and never falls back to another game's numbers, so a missing
  // block is worth saying before a button is pressed rather than after.
  const geometry = health?.checks?.payline

  // One step. The crop's own facts stay on it beside the verdict's, because they are the
  // numbers that say *which pixels* the pays were read from -- with no separate reels step to
  // report them, dropping them would leave the geometry unstated anywhere on the page.
  const steps: Step[] = [
    {
      ordinal: "01",
      name: "Paylines",
      blurb: "Crops the reel window out of the image and cuts it into one tile per cell, then embeds every tile, decides which cells hold the same symbol, and counts each line's run from reel 1.",
      action: busy === "payline" ? "Validating…" : "Validate paylines",
      status: statusOf("payline", readable, validated),
      detail: (run?.payline || run?.payline_tiles) && (
        <dl className="space-y-1">
          {run.payline_tiles && (
            <>
              <Detail term="Cells" value={String(run.payline_tiles.cells)} />
              <Detail term="Reels" value={run.payline_tiles.reels_size} />
              <Detail term="Tile" value={run.payline_tiles.tile_size} />
              <Detail term="Geometry" value={run.payline_tiles.geometry.label} />
            </>
          )}
          {run.payline && (
            <>
              <Detail
                term="Pays"
                value={`${run.payline.lines_paying} of ${run.payline.lines.length} lines`}
              />
              <Detail term="Total pay" value={String(run.payline.total_pay)} />
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
            </>
          )}
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
      subtitle=""
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
            Payline validation did not finish
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

      {/* The crops and the lines, folded into one disclosure under the verdict. It renders on
          whatever exists, so a folder whose tiles were cut on their own -- by the CLI's
          --tiles-only, or POST /api/payline/tiles -- still shows its contact sheet here with
          no verdict above it. */}
      {run && (
        <PaylineDetails
          run={run}
          tiles={run.payline_tiles ?? null}
          result={run.payline ?? null}
        />
      )}

    </PageShell>
  )
}
