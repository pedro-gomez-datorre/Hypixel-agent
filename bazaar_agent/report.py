"""Rule-based review of the current candidates: no LLM, no API key, no cost.

Runs the same checks the Claude analyst would reach for: is the margin a sudden spike compared to its
own history, is there enough history, how did past recommendations of this item turn out.
"""
from statistics import median

from . import trades
from .agent import BazaarAgent
from .strategy import Pick


def margin_series(agent: BazaarAgent, name: str) -> list[float]:
    tax = agent.cfg.tax
    return [(a * (1 - tax) - b) / b for _, b, a in agent.history.get(name, []) if b]


def review(agent: BazaarAgent, p: Pick) -> tuple[str, list[str]]:
    """Returns (verdict, reasons). Verdict is 'flip', 'watch' or 'skip'."""
    cfg, notes, skip, watch = agent.cfg, [], False, False
    series = margin_series(agent, p.name)
    if len(series) < 6:
        watch = True
        notes.append(f"only {len(series)} snapshots of history (~{len(series) * cfg.snapshot_interval // 60} min)")
    else:
        typical = median(series[:-1])
        if typical > 0 and p.margin_pct > 2.5 * typical:
            skip = True
            notes.append(f"margin {p.margin_pct:.1%} is {p.margin_pct / typical:.1f}x its usual {typical:.1%}: "
                         "looks like a spike or manipulation")
        elif p.margin_pct < 0.5 * typical:
            watch = True
            notes.append(f"margin {p.margin_pct:.1%} is well below its usual {typical:.1%}")
        else:
            notes.append(f"margin {p.margin_pct:.1%} in line with its usual {typical:.1%}")
    if p.margin_pct > cfg.max_margin * 0.6:
        watch = True
        notes.append("very wide spread, check the order book in-game before committing")
    r = agent.memory.get(p.name)
    if r.trades >= 3:
        rate = r.wins / r.trades
        notes.append(f"track record {r.wins}/{r.trades} ({rate:.0%}) judged an hour later")
        if rate < 0.4:
            skip = True
    else:
        notes.append("no meaningful track record yet")
    if p.p_win is not None:
        notes.append(f"model p(win) {p.p_win:.0%}")
        if p.p_win < 0.5:
            watch = True
    if p.risk >= 60:
        watch = True
        notes.append(f"high risk score {p.risk:.0f}")
    return ("skip" if skip else "watch" if watch else "flip"), notes


def render(agent: BazaarAgent, picks: list[Pick]) -> str:
    pr = agent.predictor
    lines = [f"Model: {'active, ' + str(pr.summary()) if pr else 'not active (using the heuristic score)'}", ""]
    for p in picks:
        verdict, notes = review(agent, p)
        lines.append(f"[{verdict.upper():5}] {p.name}: buy order {p.bid:,.1f} -> sell offer {p.ask:,.1f}, "
                     f"profit/unit {p.ppu:,.0f}, ~{p.qty:,.0f} units/day, risk {p.risk:.0f}")
        lines += [f"        - {n}" for n in notes]
    real = trades.realization(agent.store, agent.cfg.tax) if hasattr(agent, "store") else None
    if real:
        lines += ["", f"Your logged trades realize ~{real[0]:.0%} of the predicted margin (median of {real[1]}): "
                      "expect less than the profit/day estimates above."]
    return "\n".join(lines)
