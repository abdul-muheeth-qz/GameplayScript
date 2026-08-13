import type { ReactNode } from "react"

/**
 * A captioned crop: what it is, why it is worth looking at, and the pixels.
 *
 * Shared by the panel that shows the frame being read and the one that holds the crops, so a
 * caption cannot end up styled two ways in two places on the same page.
 */

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
