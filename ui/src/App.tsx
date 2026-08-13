import { useCallback, useEffect, useRef, useState } from "react"

import { api, type RunState } from "@/lib/api"
import type { Mode } from "@/components/PageShell"
import { MeterValidation } from "@/pages/MeterValidation"
import { PaylineValidation } from "@/pages/PaylineValidation"

/**
 * Two audits over one spin, and the shell that switches between them.
 *
 * All this holds is which audit is showing and which run is open. Everything else lives on the
 * server: every endpoint returns the whole RunState, so a reload, a second tab or a
 * `?run=<id>&mode=payline` link rebuilds the page from the run folder alone.
 *
 * **The run is held here rather than inside each page** so switching audits keeps the spin you
 * are looking at. One capture, two readings -- a spin captured on the meter tab can have its
 * paylines read without spinning again, which is the point of joining them at all.
 *
 * **Adopting the latest spin is decided here, not in the payline page**, because only this
 * component knows whether the URL named a run. The payline audit never captures, so with no run
 * open it reads the newest one; but a `?run=<id>` link has to win, and since `remember` writes
 * the open run back into the URL after every step, a page that adopted "the latest" on its own
 * mount could not tell a deliberate link from its own leftovers. Waiting for the URL load to
 * settle first removes the race rather than papering over it.
 *
 * No router: two modes and a run id fit in the query string, and the whole app is one screen.
 */

function isMode(value: string | null): value is Mode {
  return value === "meter" || value === "payline"
}

export default function App() {
  const params = useRef(new URLSearchParams(window.location.search))
  const [mode, setMode] = useState<Mode>(() =>
    isMode(params.current.get("mode")) ? (params.current.get("mode") as Mode) : "meter",
  )
  const [run, setRun] = useState<RunState | null>(null)
  // False until a `?run=` in the address bar has been fetched (or failed), so nothing else
  // races it into place.
  const [urlSettled, setUrlSettled] = useState(!params.current.get("run"))

  useEffect(() => {
    const id = params.current.get("run")
    if (!id) return
    api
      .run(id)
      .then(setRun)
      .catch(() => undefined)
      .finally(() => setUrlSettled(true))
  }, [])

  // The payline audit reads the newest capture when nothing is open. Runs on entering the tab
  // as well as on load, so switching to it from a fresh meter tab lands on the last spin.
  useEffect(() => {
    if (!urlSettled || mode !== "payline" || run) return
    api
      .paylineSource()
      .then((source) => {
        if (source.state) setRun(source.state)
      })
      .catch(() => undefined)
  }, [urlSettled, mode, run])

  const remember = useCallback((next: RunState | null) => {
    setRun(next)
    const url = new URL(window.location.href)
    if (next) url.searchParams.set("run", next.run_id)
    else url.searchParams.delete("run")
    window.history.replaceState(null, "", url)
  }, [])

  function chooseMode(next: Mode) {
    setMode(next)
    const url = new URL(window.location.href)
    // "meter" is the default, so it stays out of the URL and a bare link opens it.
    if (next === "meter") url.searchParams.delete("mode")
    else url.searchParams.set("mode", next)
    window.history.replaceState(null, "", url)
  }

  const Page = mode === "payline" ? PaylineValidation : MeterValidation
  return <Page mode={mode} onMode={chooseMode} run={run} onRun={remember} />
}
