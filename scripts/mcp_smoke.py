#!/usr/bin/env python3
"""Free smoke test for the flight MCP server over stdio.

Default run never sends a model request:
  - ANTHROPIC_API_KEY is replaced by a dummy value, so no real key reaches the child.
  - MCP_SPEND_CAP_USD=0, so an on-topic ask_analyst is refused by the spend cap before any request.
  - An off-topic ask_analyst must be refused by the local guard.
Pass --paid-ask to send ONE real on-topic question with the real key (costs money; needs approval).

Usage: python scripts/mcp_smoke.py [--db PATH] [--paid-ask]
Exit 0 = every check passed. Writes smoke_mcp_report.json next to this repo root.
"""
from __future__ import annotations

import argparse, asyncio, json, os, sys, tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {"list_carriers", "airport_reliability", "route_compare", "carrier_ranking", "ask_analyst"}
DUMMY_KEY = "smoke-test-dummy-key-no-network"


def payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else {"raw": sc}
    for c in result.content:
        if getattr(c, "text", None):
            try:
                return json.loads(c.text)
            except ValueError:
                return {"raw": c.text}
    return {}


async def run(db: str, paid: bool) -> dict:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    env["PYTHONPATH"] = str(ROOT / "src")
    env["FLIGHT_DB_PATH"] = db
    env["FLIGHT_DATA_DIR"] = tempfile.mkdtemp(prefix="mcp-smoke-")  # fresh spend ledger
    env["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "") if paid else DUMMY_KEY
    env["MCP_SPEND_CAP_USD"] = "0.10" if paid else "0"
    env["MCP_USD_PER_CALL"] = "0.05"
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)[:200]))

    params = StdioServerParameters(command=sys.executable, args=["-m", "flight_analyst.mcp_server"], env=env, cwd=str(ROOT))
    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                check("five tools listed", names == EXPECTED_TOOLS, sorted(names))
                no_sql = not any("sql" in n.lower() for n in names)
                check("no raw SQL tool", no_sql, sorted(names))
                res = await s.list_resources()
                uris = {str(x.uri) for x in res.resources}
                check("schema resource listed", "flight://schema" in uris, sorted(uris))
                body = (await s.read_resource("flight://schema")).contents[0]
                check("schema resource readable", len(getattr(body, "text", "")) > 50, getattr(body, "text", "")[:60])

                p = payload(await s.call_tool("list_carriers", {}))
                sky = next((c for c in p.get("carriers", []) if c.get("airline_code") == "OO"), {})
                check("list_carriers ok", p.get("ok") and p.get("carriers"), len(p.get("carriers", [])))
                check("SkyWest labeled", "several" in str(sky.get("brand", "")).lower(), sky.get("brand"))

                p = payload(await s.call_tool("airport_reliability", {"airport": "DFW"}))
                check("airport_reliability DFW", p.get("ok") and (p.get("flights") or 0) > 1000, f"{p.get('on_time_pct')}% n={p.get('flights')}")

                p = payload(await s.call_tool("route_compare", {"origin": "DFW", "dest": "ORD", "carriers": ["AA", "UA"]}))
                both = [x for x in p.get("rows", []) if x.get("label") == "both-directions"]
                check("route_compare has both-directions rows", p.get("ok") and both, len(both))

                p = payload(await s.call_tool("carrier_ranking", {"metric": "on_time_rate", "scope": "systemwide"}))
                check("carrier_ranking systemwide", p.get("ok") and p.get("rows"), (p.get("rows") or [{}])[0])

                p = payload(await s.call_tool("ask_analyst", {"question": "What is the best pizza in Dallas?"}))
                check("off-topic guard refuses pizza", p.get("refused") is True and "outside" in str(p.get("error", "")).lower(), p.get("error"))

                p = payload(await s.call_tool("ask_analyst", {"question": "Which airline is most on time at DFW?"}))
                if paid:
                    check("paid ask_analyst answered", p.get("ok"), (p.get("answer") or p.get("error") or "")[:120])
                else:
                    blocked = p.get("refused") is True and "cap" in str(p.get("error", "")).lower()
                    check("on-topic ask blocked by $0 cap (no request sent)", blocked, p.get("error"))
    return {"paid": paid, "checks": [{"check": n, "pass": ok, "detail": d} for n, ok, d in checks],
            "passed": all(ok for _, ok, _ in checks)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("FLIGHT_DB_PATH", str(ROOT / "data" / "flights.duckdb")))
    ap.add_argument("--paid-ask", action="store_true")
    a = ap.parse_args()
    if not Path(a.db).exists():
        print(f"database not found: {a.db}", file=sys.stderr)
        return 2
    rep = asyncio.run(run(a.db, a.paid_ask))
    (ROOT / "smoke_mcp_report.json").write_text(json.dumps(rep, indent=2, default=str))
    for c in rep["checks"]:
        print(("PASS " if c["pass"] else "FAIL ") + c["check"] + " | " + c["detail"])
    print("ALL PASS" if rep["passed"] else "FAILED")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
