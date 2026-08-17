import type { ReactNode } from "react"
import { RotateCcw } from "lucide-react"

import type { Health } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { HealthStrip } from "@/components/HealthStrip"
import { cn } from "@/lib/utils"

/**
 * The chrome both audits share: the header, the mode switch, the health strip and the step rail.
 *
 * It exists so the two pages cannot drift apart -- they are two readings of one spin, not two
 * products. Everything audit-specific is in `rail` and `children`. The run id is deliberately not
 * printed any more; if it needs to be visible again it belongs here, not in either page.
 */

export type Mode = "meter" | "payline"

export const MODES: { id: Mode; label: string; blurb: string }[] = [
  { id: "meter", label: "Meter Validation", blurb: "does the money add up" },
  { id: "payline", label: "Payline Validation", blurb: "does the grid pay its lines" },
]

export function PageShell({
  mode,
  onMode,
  title,
  subtitle,
  health,
  onReset,
  rail,
  children,
}: {
  mode: Mode
  onMode: (mode: Mode) => void
  title: string
  subtitle: string
  health: Health | null
  /** Absent until there is a run to clear. */
  onReset?: () => void
  rail: ReactNode
  children: ReactNode
}) {
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <header className="border-b border-rule">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-baseline justify-between gap-4 px-6 pt-4">
          <div className="flex items-baseline gap-4">
            <h1 className="eyebrow text-base text-numeral">{title}</h1>
            <p className="text-xs text-muted-foreground">{subtitle}</p>
          </div>
          <div className="flex items-center gap-6">
            <HealthStrip health={health} />
            {onReset && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onReset}
                className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-numeral"
              >
                <RotateCcw className="size-3" aria-hidden />
                New run
              </Button>
            )}
          </div>
        </div>

        {/* Tabs rather than a dropdown, because there are two and both should be readable
            without a click -- and because which one you are in changes what every button
            below does. */}
        <nav className="mx-auto max-w-[1400px] px-6" aria-label="Which audit">
          <ul className="-mb-px flex gap-1">
            {MODES.map((entry) => {
              const active = entry.id === mode
              return (
                <li key={entry.id}>
                  <button
                    type="button"
                    onClick={() => onMode(entry.id)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "group flex items-baseline gap-2 border-b-2 px-3 pt-4 pb-2.5 transition-colors",
                      active
                        ? "border-b-amber text-numeral"
                        : "border-b-transparent text-muted-foreground hover:border-b-rule hover:text-numeral",
                    )}
                  >
                    <span className="eyebrow text-sm">{entry.label}</span>
                    <span className="hidden text-xs text-muted-foreground/70 sm:inline">
                      {entry.blurb}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </nav>
      </header>

      <main className="mx-auto grid max-w-[1400px] gap-8 px-6 py-8 lg:grid-cols-[19rem_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-8 lg:self-start">{rail}</div>
        <div className="min-w-0 space-y-10">{children}</div>
      </main>
    </div>
  )
}

/** One term and its value, as both audits' step details show them. */
export function Detail({ term, value }: { term: string; value?: string | null }) {
  if (!value) return null
  return (
    <div className="flex gap-2">
      <dt className="w-16 shrink-0 text-muted-foreground/70">{term}</dt>
      <dd className="tnum min-w-0 text-numeral/80">{value}</dd>
    </div>
  )
}
