/**
 * The three buttons, and the shapes they hand back.
 *
 * Every call returns the same RunState -- the whole of what is known about one run --
 * rather than just its own stage's output. That is what makes a page reload cheap: the
 * UI holds a run id and asks for the state, and it does not matter whether the answer
 * comes from a button press or from GET /api/runs/<id> a day later.
 */

export type MeterField = {
  value: number | null
  rawtext: string
  confidence: number
  label_matched: string | null
}

export type FrameRecord = {
  image: string
  /** "config:<label>", "dynamic" or "none" -- how the meter bar was found. */
  roi_source: string | null
  roi_crop: string | null
  cash: MeterField
  win: MeterField
  bet: MeterField
}

export type SpinSummary = {
  outcome: string | null
  won: boolean | null
  measured_s: number | null
  terminal_event: string | null
  button: string | null
  capture_size: string | null
  collected_a_pending_win: boolean | null
  final_stops: number[] | null
  video: string | null
  event_count: number
}

export type Verdict = {
  verdict: "pass" | "fail" | "error"
  expected_cash: string | null
  computed_cash: string | null
  difference: string | null
  tolerance: string | null
  record: string | null
  formula: string
  model: string | null
  /** Fields assumed rather than read -- a blank WIN meter is taken as 0.00. */
  inferred: string[]
  message: string
}

export type RunState = {
  run_id: string
  frames: Partial<Record<"before" | "after", string>>
  spin: SpinSummary | null
  extraction: Record<"before" | "after", FrameRecord> | null
  crops: Partial<Record<"before" | "after", string>>
  validation: Verdict | null
}

export type Check = { ok: boolean; detail: string }
export type Health = { ok: boolean; checks: Record<string, Check> }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const reply = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  if (!reply.ok) {
    // Every stage writes its errors as prose naming the setting to fix, and FastAPI
    // puts that in `detail`. Showing the status code instead would throw it away.
    let detail = `${reply.status} ${reply.statusText}`
    try {
      const body = await reply.json()
      if (typeof body?.detail === "string") detail = body.detail
    } catch {
      /* a non-JSON error body; the status line is all there is */
    }
    throw new Error(detail)
  }
  return reply.json() as Promise<T>
}

export const api = {
  health: () => request<Health>("/api/health"),

  capture: (options: { dry_run?: boolean; no_record?: boolean } = {}) =>
    request<RunState>("/api/capture", {
      method: "POST",
      body: JSON.stringify({ dry_run: false, no_record: false, ...options }),
    }),

  extract: (run_id: string) =>
    request<RunState>("/api/extract", { method: "POST", body: JSON.stringify({ run_id }) }),

  validate: (run_id: string) =>
    request<RunState>("/api/validate", { method: "POST", body: JSON.stringify({ run_id }) }),

  run: (run_id: string) => request<RunState>(`/api/runs/${run_id}`),

  runs: () => request<{ runs: string[] }>("/api/runs"),

  /** URL of a file inside a run folder. `bust` forces a reload of a frame that was
   *  overwritten by a later run under the same name. */
  fileUrl: (run_id: string, name: string, bust?: string | number) =>
    `/api/runs/${run_id}/file/${name}${bust ? `?v=${bust}` : ""}`,
}
