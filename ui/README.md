# ui — two audits, one page

The browser half. One screen, two tabs over **one** captured spin: Meter Validation (does the money
add up) and Payline Validation (does the grid pay the lines it claims). React 19 + Vite + Tailwind 4
+ shadcn/ui, talking to the FastAPI app in [`server/`](../server/) over `/api`.

It holds no state of its own beyond which tab is showing and which run is open. Every endpoint
returns the whole `RunState`, so a reload, a second tab, or a `?run=<id>&mode=payline` link
rebuilds the page out of the run folder on disk.

```powershell
npm install
npm run dev        # http://localhost:5173, proxying /api to the API on :8000
npm run build      # tsc -b && vite build -> ui/dist, which `python -m server` then serves itself
npm run lint       # oxlint
```

Run the API alongside it (`python -m server`, from the repository root). In development they are
two origins and `/api` is proxied across; after `npm run build` they are one, because the server
serves `ui/dist` and there is no proxy left in the picture.

## What is where

```
src/
  App.tsx                 which audit is showing, which run is open. Nothing else
  pages/
    MeterValidation.tsx   capture -> extract -> validate, chained on one button
    PaylineValidation.tsx one button, and the image it is about to judge shown before it
  components/
    PageShell.tsx         the chrome both audits share: header, tab switch, health, step rail
    StepRail.tsx          the steps down the left, and what a running/failed one looks like
    HealthStrip.tsx       one dot per thing that has to be up, reason on hover
    FramePanel.tsx        every frame the run captured, at its own size -- two or three of them
    MeterReadout.tsx      CASH/WIN/BET as read, with Tesseract's confidence per value
    LedgerVerdict.tsx     the arithmetic as the hero: three meters down a column, ruled off
    PaylineVerdict.tsx    what the lines paid, and the cross-check table under it
    PaylineImages.tsx     the image being validated, and the crops as the steps make them
    PaylineDetails.tsx    the disclosure below the verdict: reel window, contact sheet, lines
    PaylineLines.tsx      one line at a time -- its path, its COMPAREs, its annotated image
    Figure.tsx            a captioned crop, so a caption cannot be styled two ways
    ui/                   shadcn primitives (new-york, neutral), added by the CLI
  lib/
    api.ts                every endpoint, and the TypeScript shape of everything they return
    utils.ts              `cn`
  index.css               the theme: the palette, the two faces, and the small utilities
```

`@/` resolves to `src/` (both [vite.config.ts](vite.config.ts) and
[tsconfig.app.json](tsconfig.app.json) — they have to agree).

## `lib/api.ts` is the contract, and it mirrors the server

Every type in there has a counterpart in Python, and the comments say which:
`FRAME_STAGES` mirrors `server/frames.py`, `Verdict` mirrors `validate/runner.py`, `PaylineResult`
mirrors `payline/report.py`. When a record grows a field, both sides change — there is no codegen
and no schema, so the mirror is kept by hand and by reading those files.

Two things about it are load-bearing:

- **Every call returns `RunState`**, not just its own stage's output. That is what makes a reload
  free, and it is why the page can put each stage's answer on screen as it arrives.
- **An error's prose is kept.** Every Python stage writes its failures as a sentence naming the
  setting to fix, and FastAPI puts that in `detail`; `request` reads `detail` and throws it,
  because showing the status code instead throws the only useful part away.

## Both audits are one button, and neither is a merged endpoint

Each page chains the stages itself — `MeterValidation.PHASES` calls `/api/capture`, `/api/extract`
and `/api/validate` in order on one press; the payline page calls `/api/payline`, which cuts the
tiles itself when they are missing or stale. **The collapse is in the page, not the server**: all
of those endpoints still exist separately, and so do the CLIs. Chaining in the browser is also what
lets the frames appear while the meters are still being read.

- **The meter button resumes; it does not re-spin.** A run whose frames are already on disk starts
  at the first stage that has not run, because OCR can fail on real frames and retrying by spinning
  a live cabinet again would be paying money to re-read a picture already on disk. The button's
  label says which of the two it is about to do. "New run" clears the run.
- **A failed stage stops the ones after it and keeps what the earlier ones returned.** The frames of
  a spin whose OCR failed are still worth looking at.
- **The payline tab never captures.** Deliberately no spin button: the audit reads an image, and
  capturing one is the meter tab's job. It opens on the frame it is about to judge instead.

## The palette and the fonts are sampled, and bundled

`index.css` is the cabinet's own meter strip: the deep indigo the bar is drawn on (`--color-ink`,
`--color-slab`), the periwinkle its digits are set in (`--color-numeral`), the amber of the bracket
ticks either side of each meter (`--color-amber`). Archivo for text, Azeret Mono for every number.

Both faces are bundled through `@fontsource-variable` rather than fetched from a CDN: the cabinet
is not guaranteed to have internet, and a font that silently falls back changes the alignment of
every meter column. The mono face is not styling either — money in columns has to line up, so
`tabular-nums` and a fixed digit width are the requirement, which is what the `.tnum` utility is.

## The proxy timeout is not a default

`vite.config.ts` sets 300 s on the `/api` proxy. A capture can run to the 180 s backstop with a
40 s OBS launch in front of it, and the default would report a network error for a spin that is
still perfectly healthy.

## Further reading

The full account of both audits — what the frames mean, why a winning spin needs three of them,
what the reel-stop checkpoint is for, and every measurement behind every rule — is in the
[root README](../README.md). [ui/CLAUDE.md](CLAUDE.md) is the short list of rules to keep in mind
while changing this folder; the reasoning for each is in the components' own docstrings, which are
worth reading before editing one.
