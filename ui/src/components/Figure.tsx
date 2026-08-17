import type { ReactNode } from "react"

/** A captioned crop, shared by both panels so a caption cannot be styled two ways on one page. */

export function Figure({
  caption,
  blurb,
  children,
}: {
  caption: string
  blurb: string
  children: ReactNode
}) {
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2">
        <span className="eyebrow text-numeral">{caption}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{blurb}</span>
      </figcaption>
      <div className="overflow-auto rounded-sm border border-rule bg-ink/60 p-2">
        {children}
      </div>
    </figure>
  )
}
