# CLAUDE.md — ui/

Guidance for working inside `ui/`. The app-wide document is [../CLAUDE.md](../CLAUDE.md) and it is
the authority: **every measurement lives there, not here.** This file is the rules that are about
the browser half specifically. When a rule below has a number behind it, the number is in the root
[README.md](../README.md) once, and this points at it rather than repeating it.

Read the docstring at the top of a component before changing it. Nearly every one records a
decision that was made at request or bought with a bug, and they are short.

## What this is

React 19 + Vite 8 + Tailwind 4 + shadcn/ui (new-york, neutral, lucide). One screen, two tabs over
one captured spin. TypeScript throughout; `@/` is `src/`. No router, no state library, no data
layer — two modes and a run id fit in the query string, and every endpoint returns the whole
`RunState`.

```powershell
npm install
npm run dev      # :5173, /api proxied to :8000 -- run `python -m server` alongside it
npm run build    # tsc -b && vite build -> ui/dist, which the API then serves
npm run lint     # oxlint. This is the only check; there are no tests
```

`npm run build` runs `tsc -b` first, so **a type error fails the build** — that is the gate on this
folder, and it is worth running after a change rather than trusting `dev`'s HMR.

## The server owns the state, and that is not a style preference

`App.tsx` holds two things: which audit is showing, and which run is open. Everything else is on
disk in a run folder, and every endpoint returns the whole `RunState`. Don't introduce a store, a
cache, or a reducer over the run: a reload, a second tab and a `?run=<id>` link from last week all
have to rebuild identically, and they do because there is nothing to rebuild *from* except the
server's answer.

Three consequences that are load-bearing:

- **The run lives in `App`, not in either page**, so switching tabs keeps the spin you are looking
  at. One capture, two readings — a spin captured on the meter tab can have its paylines read
  without spinning again, which is the entire reason the two audits are one app.
- **Adopting "the newest capture" is `App`'s decision, not the payline page's.** Only `App` knows
  whether the URL named a run, and `remember` writes the open run back into `?run=` after every
  step — so a page adopting the latest on its own mount could not tell a deliberate link from its
  own leftovers. `urlSettled` is that race removed rather than papered over; don't move the adopt
  into `PaylineValidation`.
- **`mode: "meter"` stays out of the URL** so a bare link opens the default.

## `lib/api.ts` mirrors Python by hand

There is no codegen. Each type names the module it mirrors — `FRAME_STAGES` ↔ `server/frames.py`,
`Verdict` ↔ `validate/runner.py`, `PaylineResult`/`PaylineReelStops` ↔ `payline/report.py`,
`PaylineDenom` ↔ `payline/denoms.py`. Change
one side and change the other in the same edit, and read the Python before guessing at a shape.

- **Never assume a fixed pair of frames.** A losing spin captures two and a winning one three — the
  third is the meter after TAKE WIN, and it is the frame the ledger closes against. Iterate
  `FRAME_STAGES` against `run.frames`; a hardcoded before/after is wrong on every win.
- **Keep `detail`.** `request` throws the server's own prose because each stage writes its errors as
  a sentence naming the key to fix. Replacing that with a friendlier generic message deletes the
  only actionable part.
- **A skipped thing is reported, never omitted.** `PaylineCrossCheck` carries `{skipped: "why"}`
  and `PaylineReelStops.status` can be `"unavailable"`. Don't filter those rows out of a table — a
  missing row reads as agreement, which is the opposite of what happened.
- **`denom: null` is a statement, not an absence.** It means the game's *base* five lines were
  walked — there is no `server/denom.json`, or the game declares no denominations — so
  `PaylineVerdict`'s `Denom` block renders in both states, amber when there is none. A page that
  stayed silent would present five lines as the whole paytable, when 1c pays forty. Same rule as the
  unavailable checkpoint, one section up.
- **`denom.disagreement` renders in vermilion and is not a footnote.** `denom.json` holds one value
  for the whole cabinet while the game logs one per spin, so a file left behind reads a $2.00 spin as
  1c and walks 39 lines over it. The file still decides — that is the design — and this is the only
  place a reader would find out. `agrees_with_log: true` is shown too: agreement stated as plainly as
  disagreement is what distinguishes a checked value from an unchecked one.

## The two pages are one step each, and the collapse belongs here

`MeterValidation.PHASES` chains `/api/capture` → `/api/extract` → `/api/validate` on one press;
the payline page calls `/api/payline`, which cuts its own tiles when they are missing or stale.
**There is no combined endpoint and there should not be one** — every stage is still its own call,
its own CLI and its own artefact, and chaining in the page is what lets each answer land on screen
as it arrives instead of all three at the end.

- **Resume, don't re-spin.** The meter button starts at the first stage that has not run. Making a
  failed OCR retry cost another spin of a live cabinet would be paying money to re-read a picture
  already on disk. `resuming` is what keeps the two behaviours from being silent.
- **A failed stage stops the ones after it and keeps the earlier ones' results.**
- **The payline tab has no spin button, deliberately.** It reads an image; capturing one is the
  meter tab's job, and putting OBS, the i-Deck and a 180 s backstop in front of a stage whose only
  dependencies are Pillow and numpy is what that duplication cost. It opens on the image it is
  about to judge instead.
- **`PaylineDetails` is closed by default and its trigger names the reel window and the contact
  sheet.** That disclosure is the only place the crop can be checked, and every similarity number
  on the page is meaningless if the crop is half a cell out. If a wrong crop is ever believed
  because nobody opened it, **default it open** — do not put the second button back.

## The chrome is shared so the audits cannot drift

`PageShell` owns the header, the tab switch, the health strip and the rail slot. The two pages are
two readings of one spin, not two products; the moment each owned its own header they would start
disagreeing about spacing and about what a running step looks like. Anything audit-specific goes in
`rail` and `children`. If the run id needs to be on screen again, it goes in `PageShell`.

**Health gating is per-page and deliberately not shared.** The meter audit needs no reel geometry,
so `/api/health`'s `ok` covers config and tesseract only, and `checks.payline` is reported without
gating anything.

## Styling

- **The palette is sampled off the cabinet's meter strip** and the two faces are bundled through
  `@fontsource-variable`, not fetched. The cabinet may have no internet, and a font that silently
  falls back changes the alignment of every meter column.
- **Every number is money, so every number is mono and tabular** (`.tnum`). This is a correctness
  requirement, not a look.
- shadcn primitives live in `components/ui/` and come from the CLI — prefer adding one over
  hand-rolling, and keep hand edits to them minimal so a re-add doesn't clobber logic.
- `Figure` exists so a caption cannot be styled two ways on one page. Use it.

## Things that are not in this folder

The API's own rules, the run folder contract, the config files (`server/config.json`,
`server/game_config.json`) and the captures (`server/captured_files/`) are all under
[`server/`](../server/) — see [server/CLAUDE.md](../server/CLAUDE.md). `ui/dist` is the one thing
the server reaches outside its own package for, which is why `server/settings.py` keeps a `ROOT`
anchor at all.
