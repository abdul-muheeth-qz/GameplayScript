# validate — deciding whether it adds up

Stage 3. Checks that the cash meter after a spin is the cash before it, less the bet that was
placed, plus the win this spin paid.

```
current cash = previous cash - bet + win
```

```powershell
python -m server.validate.cli captured_files/<run_id>          # Pass or Fail, with the working
python -m server.validate.cli captured_files/<run_id> --json   # the full verdict object
python -m server.validate.cli captured_files/<run_id> --write  # also write validate.json
```

The folder must be a capture run [extract](../extract/) has already been run over. Exit codes are
`0` pass, `1` fail, `2` no verdict. A Fail is a judgement about the spin; an error means no
judgement was reached, and keeping them apart is what lets a test runner tell them apart.

Needs LM Studio serving the model named in `config.json`'s `validate` section — `/api/health` says
whether it is up and whether the model it is serving is the one asked for.

This was a standalone project (`spin-validator`) that read two files with fixed names and printed
one word. It now reads what extract wrote into the run folder and writes a verdict object with the
arithmetic behind it, because a verdict with nothing under it cannot be argued with.

Three things are decided for a measured reason, all written up in the root
[README](../../README.md#deciding-whether-it-adds-up--validate):

- **One call, no tools, and the model owns every number.** Both sets of meters go up as one JSON
  object and `agent.Verdict` comes back — `working`, `computed_cash`, `difference`, `verdict`.
  Nothing in Python adds these up or compares them; the tolerance is in the prompt and the model
  applies it. That is only survivable on a 7B because of the shape of the schema: `working` is
  declared *before* the numbers, and the amounts are typed `float` rather than `str`. Deleting the
  first costs 6 of 8 verdicts; leaving the second as `str` gets `"logarithmic"` where the sum
  should be. The root README has the table.
- **Which frame each value comes from.** cash and bet from `pre_spin`; cash and win from the *last*
  frame — `win_collected` on a win, `spin_result` on a loss. `pre_spin`'s WIN meter is neither read
  nor sent: it holds the *previous* spin's win, and a value that is not in the prompt is one the
  model cannot reach for.
- **A blank WIN meter is taken as 0.00.** That is the correct reading of an empty meter and what
  every losing spin's result frame looks like; erroring on it meant an ordinary spin could never be
  validated. Cash and bet get no such treatment — a blank cash meter is a failed read, not zero
  credits. Whatever was assumed comes back in `inferred` and is badged in the UI.

Only the three-frame run layout is read. The old `before.json`/`after.json` folders and the
`data/before_spin.json` sample pair were dropped when this was simplified.
