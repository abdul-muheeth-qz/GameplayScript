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

Needs nothing running — no cabinet, no OBS, nothing over the network. The one key read from
`config.json` is `validate.tolerance`, and it has a default.

This was a standalone project (`spin-validator`) that read two files with fixed names and printed
one word. It now reads what extract wrote into the run folder and writes a verdict object with the
arithmetic behind it, because a verdict with nothing under it cannot be argued with.

Two things are decided for a measured reason, both written up in the root
[README](../../README.md#deciding-whether-it-adds-up--validate):

- **The sum is Python's, in exact `Decimal`.** `ledger.judge` works out
  `previous.cash - previous.bet + current.win`, subtracts the cash meter, and passes if what is
  left is within the tolerance. Every term and the tolerance are `Decimal`, so the boundary sits
  exactly on half a cent. The verdict object is `working`, `computed_cash`, `difference`,
  `verdict`, and `working` is that sum written out so it can be checked by eye.
- **Which frame each value comes from.** cash and bet from `pre_spin`; cash and win from the *last*
  frame — `win_collected` on a win, `spin_result` on a loss. `pre_spin`'s WIN meter is not read at
  all: it holds the *previous* spin's win, so reading it there double-counts. That, and not the
  arithmetic, was always the correctness question in this stage.
- **A blank WIN meter is taken as 0.00.** That is the correct reading of an empty meter and what
  every losing spin's result frame looks like; erroring on it meant an ordinary spin could never be
  validated. Cash and bet get no such treatment — a blank cash meter is a failed read, not zero
  credits. Whatever was assumed comes back in `inferred` and is badged in the UI.

Only the three-frame run layout is read. The old `before.json`/`after.json` folders and the
`data/before_spin.json` sample pair were dropped when this was simplified.
