# validate — deciding whether it adds up

Stage 3. Checks that the cash meter after a spin is the cash before it, plus the win that was
standing, less the bet that was placed.

```
Cₙ = Cₙ₋₁ + Wₙ₋₁ - Bₙ₋₁
```

```powershell
python -m server.validate.cli captured_files/<run_id>       # Pass or Fail
python -m server.validate.cli server/validate/data --json    # the sample records, full verdict object
```

Exit codes are `0` pass, `1` fail, `2` no verdict. A Fail is a judgement about the spin; an error
means no judgement was reached, and keeping them apart is what lets a test runner tell them apart.

Needs LM Studio serving the model named in `config.json`'s `validate` section — `/api/health` says
whether it is up and whether the model it is serving is the one asked for.

This was a standalone project (`spin-validator`) that read two files with fixed names and printed
one word. It now reads whatever [extract](../extract/) wrote into the run folder and writes a
verdict object with the arithmetic behind it, because a verdict with nothing under it cannot be
argued with. The old `data/before_spin.json` layout still works and is kept as a fixture.

Two things changed for a measured reason, both written up in the root
[README](../README.md#deciding-whether-it-adds-up--validate):

- **The agent has no tools and the model owns the verdict.** It gets both records, adds, compares
  and answers one word. Python's `cash + win - bet` fills the ledger for the UI but never
  overrides that answer. This has a measured cost, and it is severe: on the local qwen2.5-7b,
  **0/6** — inverted, deterministically, so a spin that adds up perfectly reports Fail and a
  nonsense pair reports Pass. The lineage on the same model, all in `git log`: 12/12 with a
  `cash_after_spin` tool, 10/12 tool-less showing its working, 5/12 tool-less returning a number
  with Python comparing, 0/6 here. The root [README](../README.md#the-model-owns-the-verdict-and-on-this-model-it-is-measurably-wrong)
  has the table. Whenever Python's sum and the model's word disagree, `message` says so — that
  note is the only warning a verdict was reached wrongly.
- **A blank WIN meter is taken as 0.00.** That is the correct reading of an empty meter and what
  most before-frames look like; erroring on it meant an ordinary spin could never be validated.
  Cash and bet get no such treatment. Whatever was assumed comes back in `inferred`.

Python still does the comparison, within half a cent as `Decimal`. That was never the model's job.
