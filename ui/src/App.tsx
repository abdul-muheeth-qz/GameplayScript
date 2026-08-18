import { useCallback, useEffect, useRef, useState } from "react"

import { api, type RunState } from "@/lib/api"
import type { Mode } from "@/components/PageShell"
import { MeterValidation } from "@/pages/MeterValidation"
import { PaylineValidation } from "@/pages/PaylineValidation"

/**
 * Two audits over one spin, and the shell that switches between them.
 *
 * All this holds is which audit is showing and which run is open; everything else is on the server,
 * so a reload or a `?run=<id>&mode=payline` link rebuilds the page from the run folder alone.
 *
 * **The run is held here rather than in either page**, so switching audits keeps the spin you are
 * looking at -- one capture, two readings, which is the point of joining them.
 *
 * **Adopting the latest spin is decided here too**, because only this component knows whether the
 * URL named a run: `remember` writes the open run back into `?run=`, so a page adopting "the latest"
 * on its own mount could not tell a deliberate link from its own leftovers. Waiting for the URL to
 * settle removes that race rather than papering over it.
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
