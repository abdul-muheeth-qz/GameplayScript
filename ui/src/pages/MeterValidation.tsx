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
 * **One step, at request.** The three stages were never independent choices here -- extract was
 * locked until capture finished and validate until extract did -- so `PHASES` chains them on one
 * press, each answer landing on screen as it arrives rather than all three at the end.
 *
 * **The collapse is in the page, not the server**: all three endpoints and CLIs are untouched and
 * still runnable on their own. Only the clicks collapsed.
 *
 * **It resumes rather than re-spinning**, starting at the first stage that has not run: extract does
 * fail on real frames, and making that retry cost another spin of a live cabinet would be paying
 * money to re-read a picture already on disk. `resuming` is what keeps the two from being silent.
 */

type Stage = "capture" | "extract" | "validate"

/** The audit in order: what to call, and what the button says while it is calling it. */
const PHASES: { stage: Stage; label: string }[] = [
  { stage: "capture", label: "Spinning…" },
  { stage: "extract", label: "Reading meters…" },
  { stage: "validate", label: "Checking the ledger…" },
]

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

  /** One stage, against the endpoint it always had. */
  function call(stage: Stage, run_id: string | null): Promise<RunState> {
    if (stage === "capture") return api.capture()
    // Only reachable if the resume rule let a stage start with nothing captured, which it
    // cannot -- but a named error beats a crash if that ever stops being true.
    if (!run_id) throw new Error(`Nothing to ${stage}: capture a spin first.`)
    return stage === "extract" ? api.extract(run_id) : api.validate(run_id)
  }

  /**
   * The whole audit from `from` onwards, one press. The run is handed up after every stage rather
   * than at the end, so the frames land while the meters are still being read. A failed stage stops
   * the ones after it and keeps what the earlier ones returned -- the frames of a spin whose OCR
   * failed are still worth looking at.
   */
  async function audit(from: Stage) {
    setFailed(null)
    setError(null)
    let state = run
    for (const phase of PHASES.slice(PHASES.findIndex((p) => p.stage === from))) {
      setBusy(phase.stage)
      try {
        state = await call(phase.stage, state?.run_id ?? null)
        onRun(state)
      } catch (exc) {
        setFailed(phase.stage)
        setError(exc instanceof Error ? exc.message : String(exc))
        if (phase.stage === "capture") refreshHealth()
        break
      }
    }
    setBusy(null)
  }

  function reset() {
    onRun(null)
    setError(null)
    setFailed(null)
    refreshHealth()
  }

  // The two frames every spin has. A win adds a third, which is not required to call the
  // capture done -- a losing spin never gets one.
  const captured = Boolean(run?.frames.pre_spin && run?.frames.spin_result)
  const extracted = Boolean(run?.extraction)
  const validated = Boolean(run?.validation)

  // Where the button starts. Frames on disk and no verdict over them means the spin already
  // happened -- read them again rather than paying for another one. See the resume note above.
  const from: Stage = !captured ? "capture" : !extracted ? "extract" : !validated ? "validate" : "capture"
  const resuming = captured && !validated

  function status(): StepStatus {
    if (busy) return "running"
    if (failed) return "failed"
    return validated ? "done" : "ready"
  }

  const steps: Step[] = [
    {
      ordinal: "01",
      name: "Meter audit",
      blurb: resuming
        ? "Reads the meters off the frames already captured, then checks that the money adds up. The spin is not repeated."
        : "Presses Repeat Bet on the i-Deck and shoots a frame either side of the spin — and a third after taking the win, if it won — then reads cash, win and bet off every frame and checks that the cash after the spin is the cash before it, plus the win, less the bet.",
      action:
        PHASES.find((p) => p.stage === busy)?.label ??
        (resuming ? "Read and validate" : "Run audit"),
      status: status(),
      // Every stage's own line, in the order they ran. With no rail to hang them off, this is the
      // only place the run says which button was pressed and how far out the ledger was.
      detail: (run?.spin || run?.extraction || run?.validation) && (
        <dl className="space-y-1">
          {run.spin && (
            <>
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
            </>
          )}
          {run.extraction &&
            FRAME_STAGES.filter((k) => run.extraction![k]).map((k) => (
              <Detail key={k} term={FRAME_LABELS[k]} value={run.extraction![k]!.roi_source} />
            ))}
          {run.validation && (
            <>
              <Detail term="Verdict" value={run.validation.verdict} />
              <Detail term="Out by" value={run.validation.difference} />
            </>
          )}
        </dl>
      ),
      onRun: () => audit(from),
    },
  ]

  return (
    <PageShell
      mode={mode}
      onMode={onMode}
      title="Meter audit"
      subtitle=""
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
        Press Run audit to spin the cabinet once, read its meters and check the money.
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
