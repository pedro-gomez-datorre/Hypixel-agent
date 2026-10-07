"""Claude as an analyst on top of the quantitative agent.

The ML/heuristic layer proposes candidates; Claude investigates them with read-only tools
(price history, track record, model status) and writes a short, evidence-based recommendation.
It advises only: nothing here places orders.
"""
import json
import logging
from datetime import datetime, timezone

from .agent import BazaarAgent
from .strategy import Pick

log = logging.getLogger(__name__)
MAX_TURNS = 10

SYSTEM = """You are the analyst for a Hypixel SkyBlock Bazaar order-flipping agent \
(buy order at the bid, later sell offer at the ask, minus tax).
A quantitative layer already proposed candidates with margin, daily volume, risk (0-100, lower is safer) \
and, when available, p_win: a model's probability that the margin survives one hour.
Use your tools to check candidates before recommending: look at price history for manipulation, \
spikes or collapsing margins, and at the track record of past recommendations.
Rules:
- Only state numbers that came from a tool result. If data is missing or thin, say so.
- Be skeptical: very wide spreads, sudden jumps, low flow or few samples are warning signs.
- You advise only. Never claim an order was placed.
Final answer in {language}: at most 3 picks, best first. For each: item, why (2-3 lines with the key \
numbers), main risk, and a clear verdict (flip / watch / skip). End with one line on overall market conditions \
or on how reliable this analysis is (history length, model status)."""

TOOLS = [
    {"name": "get_candidates",
     "description": "Current top flip candidates ranked by the quantitative score: margin, profit/unit, "
                    "estimated units/day, profit/day, risk 0-100, p_win (may be null).",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_item_history",
     "description": "Recent price snapshots (bid=buy-order price, ask=sell-offer price) for one item, oldest first, "
                    "about 5 minutes apart, with the net margin % at each point.",
     "input_schema": {"type": "object",
                      "properties": {"item": {"type": "string"},
                                     "points": {"type": "integer", "description": "How many snapshots, max 120"}},
                      "required": ["item"], "additionalProperties": False}},
    {"name": "get_track_record",
     "description": "How past recommendations of this item turned out (judged one hour later).",
     "input_schema": {"type": "object", "properties": {"item": {"type": "string"}},
                      "required": ["item"], "additionalProperties": False}},
    {"name": "get_model_status",
     "description": "Whether the learned win-probability model is active and how well it validated.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
]


def _pick_dict(p: Pick) -> dict:
    return {"item": p.name, "bid": round(p.bid, 1), "ask": round(p.ask, 1), "profit_per_unit": round(p.ppu, 1),
            "margin_pct": round(p.margin_pct * 100, 2), "est_units_per_day": int(p.qty),
            "est_profit_per_day": int(p.profit_day), "risk": round(p.risk),
            "p_win": None if p.p_win is None else round(p.p_win, 2)}


class Analyst:
    def __init__(self, agent: BazaarAgent, picks: list[Pick], client=None):
        self.agent, self.picks = agent, picks
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError("the analyst needs: pip install anthropic") from e
            client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY or an `ant auth login` profile
        self.client = client

    # --- tools (read-only) ---
    def run_tool(self, name: str, args: dict) -> str:
        cfg, store = self.agent.cfg, self.agent.store
        if name == "get_candidates":
            out = [_pick_dict(p) for p in self.picks]
        elif name == "get_item_history":
            item = str(args.get("item", "")).upper()
            n = max(1, min(int(args.get("points", 48)), 120))
            rows = store.recent_prices(item, n)
            out = [{"t": datetime.fromtimestamp(t, timezone.utc).strftime("%m-%d %H:%M"), "bid": round(b, 1),
                    "ask": round(a, 1), "margin_pct": round((a * (1 - cfg.tax) - b) / b * 100, 2) if b else None}
                   for t, b, a in rows]
            if not out:
                return json.dumps({"error": f"no stored history for {item}"})
        elif name == "get_track_record":
            r = self.agent.memory.get(str(args.get("item", "")).upper())
            out = {"judged": r.trades, "wins": r.wins, "losses": r.losses,
                   "avg_margin_pct_after_1h": round(r.avg_margin_pct * 100, 2)}
        elif name == "get_model_status":
            pr = self.agent.predictor
            out = pr.summary() if pr else {"active": False,
                                           "note": "no validated model yet; ranking uses the heuristic only"}
        else:
            raise ValueError(f"unknown tool {name}")
        return json.dumps(out)

    def run(self) -> str:
        cfg = self.agent.cfg
        messages = [{"role": "user", "content": "Analyze the current candidates and give me your recommendation."}]
        system = SYSTEM.format(language=cfg.analyst_language)
        for _ in range(MAX_TURNS):
            resp = self.client.messages.create(
                model=cfg.analyst_model, max_tokens=8000, system=system, tools=TOOLS,
                output_config={"effort": "medium"}, messages=messages)
            if resp.stop_reason == "refusal":
                return "(the model declined to answer)"
            messages.append({"role": "assistant", "content": resp.content})  # keep thinking blocks intact
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                return "\n".join(b.text for b in resp.content if b.type == "text").strip()
            results = []
            for c in calls:
                try:
                    results.append({"type": "tool_result", "tool_use_id": c.id,
                                    "content": self.run_tool(c.name, c.input)})
                except Exception as e:  # report the failure to the model instead of crashing
                    results.append({"type": "tool_result", "tool_use_id": c.id,
                                    "content": f"tool error: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})
        return "(analysis stopped: too many tool rounds)"
