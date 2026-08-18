import type { Health } from "@/lib/api"
import { cn } from "@/lib/utils"

/** One dot per thing that has to be up. OBS may be down -- capture opens it -- so it is amber. */
const OPTIONAL = new Set(["obs"])

export function HealthStrip({ health }: { health: Health | null }) {
  if (!health) {
    return <span className="text-xs text-muted-foreground">checking…</span>
  }

  return (
    <div className="flex items-center gap-4">
      {Object.entries(health.checks).map(([name, check]) => (
        <span
          key={name}
          title={check.detail}
          className="flex cursor-help items-center gap-1.5"
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              check.ok ? "bg-jade" : OPTIONAL.has(name) ? "bg-amber" : "bg-vermilion",
            )}
            aria-hidden
          />
          <span className="eyebrow text-muted-foreground">{name}</span>
        </span>
      ))}
    </div>
  )
}
