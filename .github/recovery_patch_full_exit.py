from pathlib import Path

path = Path("src/gquant/strategy/planner.py")
text = path.read_text()
old = '''                for s in dropped:\n                    if s in {o["symbol"] for o in pending_orders if o["action"] == "sell"}:\n                        continue\n                    pending_orders.append(\n'''
new = '''                for s in dropped:\n                    pending_orders.append(\n'''
if text.count(old) != 1:
    raise SystemExit("expected planner full-exit masking pattern exactly once")
path.write_text(text.replace(old, new))
