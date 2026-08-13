import type { ReactNode } from "react"

import {
  api,
  type PaylineResult,
  type PaylineSource,
  type PaylineTiles,
  type RunState,
} from "@/lib/api"
import { Heading } from "@/components/FramePanel"

/**
 * The image being validated, shown before anything has been run, and the crops beside it as
 * the steps produce them.
 *
 * The frame comes first and unprompted because "which picture is this verdict about?" is the
 * first question a payline result raises, and this page never captures -- so there is always an
 * answer available on load. The caption says which source it is, plainly: the newest capture's
 * `spin_result`, or a supplied `payline.image`.
 *
 * **A supplied image is a supported mode, not an error**, so it reads as a statement rather
 * than a warning. It is the whole point of `payline.image`: validating a chosen screenshot
 * while real captures sit on disk, which otherwise would mean emptying `captured_files/` first.
 * It still has to be *stated* — a stale setting auditing the wrong picture is invisible in the
 * pixels — but stating it in red would make the intended case look broken.
 *
 * **The contact sheet is the one to look at first when a number looks wrong.** Every similarity
 * in this stage is meaningless if the crop is half a cell out, and the sheet shows that
 * immediately where a table of cosines never would.
 */

export function PaylineImages({
  run,
  tiles,
  result,
  source,
}: {
  run: RunState | null
  tiles: PaylineTiles | null
  result: PaylineResult | null
  source: PaylineSource | null
}) {
  const files = result?.files ?? tiles?.files
  const record = result ?? tiles

  // The record's own account wins once something has run -- it is what actually happened.
  // Before that, `source` is the server's statement of what it would read.
  const supplied = record
    ? record.image_source.startsWith("payline.image")
    : Boolean(source?.supplied)
  const name = record?.image ?? source?.source ?? "the configured image"
  const frame = run?.frames.spin_result

  // A supplied image lives outside the run folder, so it has its own route; a captured frame is
  // served out of the folder it belongs to.
  const imageUrl = supplied
    ? api.paylineImageUrl(record?.image ?? source?.detail ?? "")
    : run && frame
      ? api.fileUrl(run.run_id, frame, run.run_id)
      : null

  if (!imageUrl && !files) return null

  const size = record?.frame_size ?? run?.spin?.capture_size
  const note = [
    size && `${size.replace("x", " x ")} image`,
    record?.reels_size && `${record.reels_size.replace("x", " x ")} reel window`,
  ]
    .filter(Boolean)
    .join(", ")

  return (
    <section>
      <Heading label="Validating this image" note={note || undefined} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Figure
          caption={name}
          blurb={
            supplied
              ? "a supplied image — payline.image in config.json"
              : "the latest spin result, as captured"
          }
        >
          {imageUrl ? (
            <img
              src={imageUrl}
              alt="The image the paylines are read from"
              className="mx-auto block h-auto max-h-[34rem] w-auto max-w-full"
            />
          ) : (
            <p className="px-4 py-10 text-center text-xs text-muted-foreground">
              {source?.detail}
            </p>
          )}
        </Figure>

        <div className="space-y-4">
          {run && files?.reels && (
            <Figure caption="Reel window" blurb="the region this geometry crops to">
              <img
                src={api.fileUrl(run.run_id, files.reels, run.run_id)}
                alt="The cropped reel window"
                className="block h-auto w-full"
              />
            </Figure>
          )}
          {run && files?.contact_sheet && (
            <Figure
              caption="Contact sheet"
              blurb="check this first — a crop half a cell out invalidates every number below"
            >
              <img
                src={api.fileUrl(run.run_id, files.contact_sheet, run.run_id)}
                alt="Every cell of the grid, labelled"
                className="block h-auto w-full"
              />
            </Figure>
          )}
          {!files && (
            <div className="flex h-full min-h-40 items-center justify-center rounded-sm border border-dashed border-rule px-6 py-10">
              <p className="text-center text-xs text-muted-foreground">
                Cut the reels to see the reel window and the contact sheet.
              </p>
            </div>
          )}
        </div>
      </div>

      {run && files?.annotated?.summary && (
        <div className="mt-4">
          <Figure
            caption="Every paying line"
            blurb="the paying run drawn on the cells it was read from"
          >
            <img
              src={api.fileUrl(run.run_id, files.annotated.summary, run.run_id)}
              alt="The reel window with each paying line drawn on it"
              className="block h-auto w-full"
            />
          </Figure>
        </div>
      )}
    </section>
  )
}

function Figure({
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
