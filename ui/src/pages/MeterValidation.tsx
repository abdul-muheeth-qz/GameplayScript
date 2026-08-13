import { useCallback, useEffect, useState } from "react"
import { AlertCircle } from "lucide-react"

import {
  api,
  FRAME_LABELS,
  FRAME_STAGES,
  type Health,
  type RunState,
} from "@/lib/api"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { FramePanel } from "@/components/FramePanel"
import { LedgerVerdict } from "@/components/LedgerVerdict"
import { MeterReadout } from "@/components/MeterReadout"
import { Detail, PageShell, type Mode } from "@/components/PageShell"
import { StepRail, type Step, type StepStatus } from "@/components/StepRail"

/**
 * The meter audit: capture a spin, read the meters off the frames, check the money.
 *
 * Lifted out of App.tsx unchanged when the payline audit was added -- same three steps, same
 * gating, same endpoints. The only difference is that the header and the step rail now come
 * from PageShell, so the two audits cannot drift apart.
 */

type Stage = "capture" | "extract" | "validate"

export function MeterValidation({
  mode,
  onMode,
  run,
  onRun,
}: {
  mode: Mode
  onMode: (mode: Mode) => void
  /** Held by App, so switching audits keeps the run you are looking at. */
  run: RunState | null
  onRun: (run: RunState | null) => void
}) {
  const [health, setHealth] = useState<Health | null>(null)
  const [busy, setBusy] = useState<Stage | null>(null)
  const [failed, setFailed] = useState<Stage | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshHealth = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  useEffect(refreshHealth, [refreshHealth])

  async function step(stage: Stage, call: () => Promise<RunState>) {
    setBusy(stage)
    setFailed(null)
    setError(null)
    try {
      onRun(await call())
    } catch (exc) {
      setFailed(stage)
      setError(exc instanceof Error ? exc.message : String(exc))
      if (stage === "capture") refreshHealth()
    } finally {
      setBusy(null)
    }
  }

  function reset() {
    onRun(null)
    setError(null)
    setFailed(null)
    refreshHealth()
  }

  function statusOf(stage: Stage, unlocked: boolean, done: boolean): StepStatus {
    if (busy === stage) return "running"
    if (failed === stage) return "failed"
    if (done) return "done"
    return unlocked && !busy ? "ready" : "locked"
  }

  // The two frames every spin has. A win adds a third, which is not required to call the
  // capture step done -- a losing spin never gets one.
  const captured = Boolean(run?.frames.pre_spin && run?.frames.spin_result)
  const extracted = Boolean(run?.extraction)
  const validated = Boolean(run?.validation)

  const steps: Step[] = [
    {
      ordinal: "01",
      name: "Capture",
      blurb: "Opens OBS, presses Repeat Bet on the i-Deck, and shoots a frame either side of the spin — and a third after taking the win, if it won.",
      action: busy === "capture" ? "Spinning…" : "Start",
      status: statusOf("capture", true, captured),
      detail: run?.spin && (
        <dl className="space-y-1">
          <Detail term="Outcome" value={run.spin.outcome} />
          <Detail
            term="Took"
            value={run.spin.measured_s === null ? null : `${run.spin.measured_s}s`}
          />
          <Detail term="Ended on" value={run.spin.terminal_event ?? "a timeout"} />
          <Detail term="Pressed" value={run.spin.button} />
          {run.spin.won && (
            <Detail
              term="Win"
              value={run.spin.win_collected ? "taken on the glass" : "left on the offer"}
            />
          )}
        </dl>
      ),
      onRun: () => step("capture", () => api.capture()),
    },
    {
      ordinal: "02",
      name: "Extract",
      blurb: "Crops every frame to the meter strip and reads cash, win and bet off them.",
      action: busy === "extract" ? "Reading…" : "Extract",
      status: statusOf("extract", captured, extracted),
      detail: run?.extraction && (
        <dl className="space-y-1">
          {FRAME_STAGES.filter((k) => run.extraction![k]).map((k) => (
            <Detail key={k} term={FRAME_LABELS[k]} value={run.extraction![k]!.roi_source} />
          ))}
        </dl>
      ),
      onRun: () => run && step("extract", () => api.extract(run.run_id)),
    },
    {
      ordinal: "03",
      name: "Validate",
      blurb: "Checks that the cash after the spin is the cash before it, plus the win, less the bet.",
      action: busy === "validate" ? "Checking…" : "Validate",
      status: statusOf("validate", extracted, validated),
      detail: run?.validation && (
        <dl className="space-y-1">
          <Detail term="Verdict" value={run.validation.verdict} />
          <Detail term="Model" value={run.validation.model} />
        </dl>
      ),
      onRun: () => run && step("validate", () => api.validate(run.run_id)),
    },
  ]

  return (
    <PageShell
      mode={mode}
      onMode={onMode}
      title="Meter audit"
      subtitle="does the money add up"
      runId={run?.run_id}
      health={health}
      onReset={run ? reset : undefined}
      rail={<StepRail steps={steps} />}
    >
      {error && (
        <Alert className="border-vermilion/50 bg-vermilion/5">
          <AlertCircle className="size-4 text-vermilion" />
          <AlertTitle className="eyebrow text-vermilion">
            {failed} did not finish
          </AlertTitle>
          <AlertDescription className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground">
            {error}
          </AlertDescription>
        </Alert>
      )}

      {!run && !busy && <Empty />}

      {busy === "capture" && !captured && <CaptureWaiting />}

      {run && captured && <FramePanel run={run} />}
      {run && <MeterReadout run={run} />}
      {run?.validation && <LedgerVerdict verdict={run.validation} />}
    </PageShell>
  )
}

function Empty() {
  return (
    <div className="rounded-sm border border-dashed border-rule px-8 py-16 text-center">
      <p className="text-sm text-muted-foreground">
        Press Start to spin the cabinet once and capture it.
      </p>
      <p className="mt-2 text-xs text-muted-foreground/70">
        Or open a past run with <span className="tnum">?run=&lt;folder name&gt;</span>.
      </p>
    </div>
  )
}

function CaptureWaiting() {
  return (
    <section>
      <div className="mb-3 flex items-baseline gap-3 border-b border-rule pb-2">
        <h3 className="eyebrow text-amber">Frames</h3>
        <p className="text-xs text-muted-foreground">
          waiting for the game's log to say the spin is over — an ordinary spin is about
          3 seconds, a Hold &amp; Spin runs to a minute
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Skeleton className="h-96 rounded-sm bg-slab" />
        <Skeleton className="h-96 rounded-sm bg-slab" />
      </div>
    </section>
  )
}
