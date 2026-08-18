"""The payline logic, exactly as specified in Payline.xlsx.

    COMPARE cell1 & cell2
        IF NO  -> STOP                                  (line does not pay)
        IF YES -> INITIALIZE PAY COUNTER AT 2
    then for each following adjacent pair:
        IF YES -> INCREMENT PAY COUNTER BY 1
        IF NO  -> DISPLAY "LINE n PAYS {PAY_COUNTER_VALUE}"   and stop
    DISPLAY "LINE n PAYS {PAY_COUNTER_VALUE}"

Left to right, adjacent pairs only, no skipping, so the counter is the length of the matching run
starting at reel 1.

**Pure logic over a matcher, knowing nothing about images** -- which is what lets
test_paylines.py verify it without any pixels, and makes it the one genuinely unit-testable module
here. Change the vision layer freely; leave this alone.
"""

from dataclasses import dataclass, field
from typing import List

from .matcher import Decision


@dataclass
class LineResult:
    line_id: int
    name: str
    cells: List[str]
    pays: int
    steps: List[Decision] = field(default_factory=list)
    stopped_at: int = -1  # index of the pair that broke the run, -1 = ran to the end

    @property
    def wins(self):
        return self.pays >= 2

    @property
    def winning_cells(self):
        """The cells covered by the paying run."""
        return self.cells[: self.pays] if self.wins else []

    @property
    def message(self):
        """The literal string the payline asks to display."""
        return f"LINE {self.line_id} PAYS {self.pays}"


def evaluate_line(line, matcher):
    """Evaluate one payline definition ({'id', 'name', 'cells'})."""
    line_id = line["id"]
    name = line.get("name", "")
    cells = [c.upper() for c in line["cells"]]

    steps = []

    # --- first pair: IF NO THEN STOP ------------------------------------
    first = matcher.compare(cells[0], cells[1])
    steps.append(first)

    if not first.match:
        return LineResult(line_id, name, cells, pays=0, steps=steps, stopped_at=0)

    # --- INITIALIZE PAY COUNTER AT 2 -----------------------------------
    counter = 2

    # --- remaining adjacent pairs --------------------------------------
    for i in range(1, len(cells) - 1):
        decision = matcher.compare(cells[i], cells[i + 1])
        steps.append(decision)

        if not decision.match:
            return LineResult(
                line_id, name, cells, pays=counter, steps=steps, stopped_at=i
            )

        counter += 1

    return LineResult(line_id, name, cells, pays=counter, steps=steps, stopped_at=-1)


def evaluate_all(geometry, matcher):
    """Evaluate every payline the geometry defines, in order."""
    return [evaluate_line(line, matcher) for line in geometry.paylines]


def total_pays(results):
    """How many lines paid, and the total number of paying symbols."""
    winners = [r for r in results if r.wins]
    return len(winners), sum(r.pays for r in winners)
