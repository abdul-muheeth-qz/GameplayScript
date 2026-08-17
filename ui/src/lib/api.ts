/**
 * The buttons, and the shapes they hand back.
 *
 * Every call returns the same RunState -- the whole of what is known about one run --
 * rather than just its own stage's output. That is what makes a page reload cheap: the
 * UI holds a run id and asks for the state, and it does not matter whether the answer
 * comes from a button press or from GET /api/runs/<id> a day later.
 *
 * Two audits share that state. The meter one reads every frame's CASH/WIN/BET and checks
 * that the money adds up; the payline one reads the reel grid off `spin_result` and walks
 * the lines. They share the capture and nothing else.
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
  /** Whether this spin's win was taken on the glass, which is what makes a third frame. */
  win_collected: boolean | null
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
  /** Fields assumed rather than read -- a blank WIN meter is taken as 0.00. */
  inferred: string[]
  message: string
  /** Which record file each part of the sum came from. */
  sources: Record<"cash_and_bet" | "win" | "final", string> | null
  /** The frame stages this verdict was reached over, in order. */
  stages: string[]
}

/**
 * The stages a run can hold, in order. Two on a losing spin; three when it won, because a
 * win is not in the cash meter until it is collected -- `win_collected` is the frame taken
 * after TAKE WIN was clicked on the glass, and it is the one the ledger closes against.
 *
 * Mirrors `server/frames.py`. Nothing here may assume a fixed pair.
 */
export const FRAME_STAGES = ["pre_spin", "spin_result", "win_collected"] as const
export type FrameStage = (typeof FRAME_STAGES)[number]

export const FRAME_LABELS: Record<FrameStage, string> = {
  pre_spin: "Before the spin",
  spin_result: "Spin result",
  win_collected: "Win collected",
}

/** What each frame is evidence of, shown under its label. */
export const FRAME_BLURBS: Record<FrameStage, string> = {
  pre_spin: "the cash it started from, and the bet",
  spin_result: "what the spin paid -- not yet in the cash meter",
  win_collected: "the win taken on the glass, now paid in",
}

/* -- the payline audit ----------------------------------------------------
 *
 * The second audit over the same capture. It reads `spin_result`'s pixels rather than its
 * meters: crop the reel window, cut it into cells, decide which cells hold the same symbol,
 * and walk each payline left to right counting the matching run.
 *
 * Mirrors `server/payline/report.py`. Neither audit gates the other -- a run may hold one
 * verdict, both or neither, and they can disagree, which is information rather than a bug.
 */

/** One COMPARE, and why it answered the way it did. */
export type PaylineStep = {
  compare: [string, string]
  similarity: number
  match: boolean
  /** "cos 0.9963 >= 0.9000", or "GROUP_1 vs GROUP_4" -- the rule that decided it. */
  detail: string
  /** Set only on the pairs the reel-stop checkpoint reached: an ambiguous cosine settled (or
   *  explicitly not settled) on the game's own reel stops. Null on every other pair. */
  checkpoint: string | null
}

export type PaylineLine = {
  line: number
  name: string
  cells: string[]
  /** The length of the matching run starting at reel 1. Below 2 the line does not pay. */
  pays: number
  wins: boolean
  winning_cells: string[]
  /** Symbol names, only when the matcher can name one (the `library` method). */
  symbols: string[]
  message: string
  /** The cell that ended the run, or null when the line ran to the end. */
  broken_at: string | null
  steps: PaylineStep[]
}

export type PaylineGeometry = {
  process: string
  label: string
  measured_on: string | null
  grid: string
  reels_roi: number[]
  inner_margin_frac: number
}

/** `{threshold: {pays: [...]}, cluster: {skipped: "why"}}` -- a strategy that could not run
 *  says so, because an omitted row would read as agreement. */
export type PaylineCrossCheck = Record<string, { pays?: number[]; skipped?: string }>

export type PaylineFiles = {
  reels?: string
  contact_sheet?: string
  tiles?: Record<string, string>
  line_details?: string
  similarity_matrix?: string
  annotated?: Record<string, string>
}

/** One pair the reel-stop checkpoint looked at, whether or not it changed anything. */
export type PaylineAdjudication = {
  compare: [string, string]
  similarity: number
  /** The two symbol names the reel stops gave, either of which may be null. */
  symbols: (string | null)[]
  /** False when it abstained -- a mystery symbol, or no stops at all. */
  decided: boolean
  was: boolean
  now: boolean
  note: string
}

/**
 * The checkpoint that reads the game's own `BaseGameReelStops` out of the telemetry log and
 * maps them through the reel strips, so an ambiguous COMPARE is decided on symbol *names*
 * rather than on pixels.
 *
 * `status` is "on", "off" (disabled in config) or "unavailable" (configured on, but the
 * telemetry or the spreadsheet could not be read -- `detail` says which). Unavailable is not a
 * failure: the audit still has a complete pixel reading. It is reported because a checkpoint
 * that silently did nothing leaves the answer it would have corrected on screen.
 */
export type PaylineReelStops = {
  status: "on" | "off" | "unavailable"
  detail?: string
  /** One stop per reel, left to right, straight out of the telemetry. */
  stops?: number[]
  timestamp?: string | null
  game_id?: string | null
  file?: string
  line?: number
  folder?: string
  entries?: number
  /** How this entry was chosen -- by the frame's own time, or as a reported fallback. */
  matched_by?: string
  frame_time?: string | null
  /** [low, high): below the match threshold, above clearly-different. */
  band?: [number, number]
  strips?: string
  strip_lengths?: Record<string, number>
  symbol_grid?: Record<string, string>
  adjudications?: PaylineAdjudication[]
  /** How many COMPAREs it decided against the pixels. 0 means it agreed everywhere it looked. */
  overrides?: number
  /** What the pixels alone paid, line for line -- so the cross-check table below the verdict
   *  cannot look like it contradicts it. */
  pays_without_checkpoint?: number[]
}

export type PaylineResult = {
  verdict: "pays" | "no pay"
  image: string
  /** "spin_result of this run", or the configured fallback image it read instead. */
  image_source: string
  backend: string
  method: string
  matcher: string
  geometry: PaylineGeometry
  reels_size: string | null
  tile_size: string | null
  frame_size: string | null
  symbol_grid: Record<string, string>
  lines_paying: number
  total_pay: number
  lines: PaylineLine[]
  /** Absent on records written before the checkpoint existed. */
  reel_stops?: PaylineReelStops
  cross_check: PaylineCrossCheck
  /** Do every strategy that ran agree, line for line? null when only one ran. */
  agreement: boolean | null
  files: PaylineFiles
  message: string
}

/**
 * Which frame the payline audit would read next, before anything has been run.
 *
 * `run_id` is null when no capture on disk holds a spin_result -- then `source` names the
 * configured fallback image, or says there is nothing at all. `state` rides along so the page
 * can show the frame without a second request.
 */
export type PaylineSource = {
  /** The run folder results land in — the newest capture holding a spin_result. */
  run_id: string | null
  /** True when `payline.image` is set and so overrides the captured frame. */
  supplied: boolean
  /** False when there is nothing to read at all, or the supplied path is missing. */
  readable: boolean
  /** Short label: "spin_result.png", or "payline.image (demo_spin.png)". */
  source: string
  /** The long form — the run id, or the full configured path. */
  detail: string
  state: RunState | null
}

/** What the tiles step recorded -- present as soon as the reels have been cut. */
export type PaylineTiles = {
  image: string
  image_source: string
  frame_size: string | null
  reels_size: string
  tile_size: string
  cells: number
  geometry: PaylineGeometry
  files: PaylineFiles
}

export type RunState = {
  run_id: string
  frames: Partial<Record<FrameStage, string>>
  spin: SpinSummary | null
  extraction: Partial<Record<FrameStage, FrameRecord>> | null
  crops: Partial<Record<FrameStage, string>>
  validation: Verdict | null
  payline_tiles: PaylineTiles | null
  payline: PaylineResult | null
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

  /** Which image the payline audit would read next, without running anything. */
  paylineSource: () => request<PaylineSource>("/api/payline/source"),

  /** URL of the configured `payline.image` — the one source that lives outside a run folder. */
  paylineImageUrl: (bust?: string | number) =>
    `/api/payline/image${bust ? `?v=${bust}` : ""}`,

  /** `run_id` omitted means the newest capture holding a spin_result. */
  paylineTiles: (run_id?: string) =>
    request<RunState>("/api/payline/tiles", {
      method: "POST",
      body: JSON.stringify(run_id ? { run_id } : {}),
    }),

  payline: (run_id?: string) =>
    request<RunState>("/api/payline", {
      method: "POST",
      body: JSON.stringify(run_id ? { run_id } : {}),
    }),

  run: (run_id: string) => request<RunState>(`/api/runs/${run_id}`),

  runs: () => request<{ runs: string[] }>("/api/runs"),

  /** URL of a file inside a run folder. `bust` forces a reload of a frame that was
   *  overwritten by a later run under the same name. */
  fileUrl: (run_id: string, name: string, bust?: string | number) =>
    `/api/runs/${run_id}/file/${name}${bust ? `?v=${bust}` : ""}`,
}
