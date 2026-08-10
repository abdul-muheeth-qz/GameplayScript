import type { ReactNode } from "react"
import { Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type StepStatus = "locked" | "ready" | "running" | "done" | "failed"

export type Step = {
  /** Numbered because the order is real: nothing can be read off a frame that has not
   *  been captured, and nothing can be judged before it has been read. */
  ordinal: string
  name: string
  /** What pressing the button does, in the same words as the button itself. */
  blurb: string
  action: string
  status: StepStatus
  /** One line of result, shown under the step once it has one. */
  detail?: ReactNode
  onRun: () => void
}

const DOT: Record<StepStatus, string> = {
  locked: "bg-rule",
  ready: "bg-amber",
  running: "bg-amber animate-pulse",
  done: "bg-jade",
  failed: "bg-vermilion",
}

export function StepRail({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex flex-col">
      {steps.map((step, i) => {
        const active = step.status === "ready" || step.status === "running"
        return (
          <li
            key={step.name}
            className={cn(
              "relative border-l-2 py-6 pl-6 pr-4 transition-colors",
              active ? "border-l-amber" : "border-l-rule",
              i > 0 && "border-t border-t-rule/60",
            )}
          >
            <span
              className={cn(
                "absolute -left-[5px] top-8 size-2 rounded-full transition-colors",
                DOT[step.status],
              )}
              aria-hidden
            />
            <div className="flex items-baseline gap-3">
              <span
                className={cn(
                  "tnum text-xs font-semibold",
                  active ? "text-amber" : "text-muted-foreground",
                )}
              >
                {step.ordinal}
              </span>
              <h2
                className={cn(
                  "eyebrow",
                  step.status === "locked" ? "text-muted-foreground" : "text-numeral",
                )}
              >
                {step.name}
              </h2>
            </div>

            <p className="mt-2 max-w-[24ch] text-sm leading-snug text-muted-foreground">
              {step.blurb}
            </p>

            {/* A step that has already run keeps its button -- re-running is allowed --
                but stops competing with the one that is actually next. */}
            <Button
              onClick={step.onRun}
              disabled={step.status === "locked" || step.status === "running"}
              className={cn(
                "mt-4 w-full justify-center rounded-sm font-semibold tracking-wide disabled:opacity-35",
                step.status === "done" || step.status === "failed"
                  ? "border border-rule bg-transparent text-muted-foreground hover:border-amber/60 hover:bg-transparent hover:text-amber"
                  : "bg-amber text-ink hover:bg-amber/85",
              )}
            >
              {step.status === "running" && (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              )}
              {step.action}
            </Button>

            {step.detail && (
              <div className="mt-3 text-xs leading-relaxed text-muted-foreground">
                {step.detail}
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}
