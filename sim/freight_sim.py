"""The $2.1M freight simulator.

Generates a realistic overnight scenario: a demand spike sends 6 AI agents across
3 systems (SAP / Salesforce / ServiceNow) making hundreds of automated decisions.
Two agents repeatedly disagree on the freight mode of the same shipments — one routes
standard, another forces air — and an expedite agent acts on the conflict, racking up
emergency air-freight cost.

Each causal cluster is a trace: forecaster -> planner -> router -> allocator -> logistics
-> expedite, linked by parent_span_id (so Turrion reconstructs the chain from trace), and
every decision references the shipment Entity (so cross-system links reconstruct from
entity refs too). The allocator/logistics conflict seeds the divergence detector (Phase 1).

Usage:
    python -m sim.freight_sim --api http://localhost:8088 --decisions 847
"""
from __future__ import annotations

import argparse
import random
import uuid
from datetime import datetime, timedelta, timezone

# Wed 02:11 UTC — the pitch-deck timestamp.
BASE = datetime(2026, 6, 24, 2, 11, tzinfo=timezone.utc)

AIR_COST = 24800.0       # per expedited shipment
STD_COST = 3100.0


def span() -> str:
    return uuid.uuid4().hex[:16]


def shipment_entity(num: int) -> dict:
    return {
        "type": "shipment",
        "natural_keys": {"shipment_id": f"SHIP-{1000 + num}"},
        "system_of_record": "SAP",
    }


def ev(system, atype, actor, framework, ts, entity, trace, span_id, parent,
       decision=None, payload=None) -> dict:
    return {
        "source_system": system,
        "type": atype,
        "actor_kind": "agent",
        "actor_name": actor,
        "actor_framework": framework,
        "ts": ts.isoformat(),
        "run_external_id": trace,          # one run per causal cluster
        "trace_id": trace,
        "span_id": span_id,
        "parent_span_id": parent,
        "entities": [entity],
        "payload": payload or {},
        "decision": decision,
    }


def build_cluster(num: int, t0: datetime) -> list[dict]:
    """One shipment's worth of cross-system decisions = one trace."""
    trace = uuid.uuid4().hex
    ent = shipment_entity(num)
    out: list[dict] = []
    t = t0

    # 1. Demand forecaster (SAP) bumps forecast
    s1 = span()
    t += timedelta(seconds=random.randint(1, 4))
    out.append(ev("SAP", "decision", "demand_forecaster", "custom", t, ent, trace, s1, None,
                  decision={"action": "raise_forecast", "field": "forecast", "value": "spike",
                            "rationale": "overnight order surge +38%", "confidence": 0.82}))

    # 2. Inventory planner (SAP) flags shortage
    s2 = span()
    t += timedelta(seconds=random.randint(1, 4))
    out.append(ev("SAP", "decision", "inventory_planner", "custom", t, ent, trace, s2, s1,
                  decision={"action": "flag_shortage", "field": "stock_status", "value": "short",
                            "rationale": "projected stockout in 36h", "confidence": 0.77}))

    # 3. Order router (Salesforce) promises a delivery date
    s3 = span()
    t += timedelta(seconds=random.randint(1, 4))
    out.append(ev("Salesforce", "decision", "order_router", "langgraph", t, ent, trace, s3, s2,
                  decision={"action": "promise_delivery", "field": "promise_date", "value": "next_day",
                            "rationale": "honor SLA for tier-1 account", "confidence": 0.9}))

    # 4 & 5. The conflict: allocator says standard, logistics forces air — same shipment, same field.
    s4 = span()
    t += timedelta(seconds=random.randint(1, 3))
    out.append(ev("SAP", "decision", "supplier_allocator", "custom", t, ent, trace, s4, s3,
                  decision={"action": "set_freight_mode", "field": "freight_mode", "value": "standard",
                            "rationale": "cost guardrail: standard within budget", "confidence": 0.71}))
    s5 = span()
    t += timedelta(seconds=random.randint(1, 3))
    out.append(ev("ServiceNow", "decision", "logistics_optimizer", "crewai", t, ent, trace, s5, s3,
                  decision={"action": "set_freight_mode", "field": "freight_mode", "value": "air",
                            "rationale": "meet next-day promise from order_router", "confidence": 0.86}))

    # 6. Expedite agent (ServiceNow) acts on the 'air' decision -> emergency cost
    s6 = span()
    t += timedelta(seconds=random.randint(1, 3))
    out.append(ev("ServiceNow", "decision", "expedite_agent", "custom", t, ent, trace, s6, s5,
                  decision={"action": "book_air_freight", "field": "freight_cost", "value": str(AIR_COST),
                            "rationale": "freight_mode=air requires expedite booking", "confidence": 0.93},
                  payload={"cost_usd": AIR_COST}))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8088")
    ap.add_argument("--decisions", type=int, default=120,
                    help="approx total decisions (6 per shipment cluster)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)

    import httpx  # lazy: lets build_cluster be imported/tested without httpx installed

    clusters = max(1, args.decisions // 6)
    events: list[dict] = []
    t = BASE
    for i in range(clusters):
        events.extend(build_cluster(i, t))
        t += timedelta(seconds=random.randint(0, 3))

    air_total = clusters * AIR_COST
    print(f"Posting {len(events)} events across {clusters} shipments "
          f"({clusters} freight-mode conflicts). Modeled emergency spend: ${air_total:,.0f}")

    posted = 0
    with httpx.Client(base_url=args.api, timeout=30) as client:
        client.get("/healthz").raise_for_status()
        for e in events:
            client.post("/events", json=e).raise_for_status()
            posted += 1
            if posted % 100 == 0:
                print(f"  ...{posted}/{len(events)}")

    print(f"Done. {posted} events ingested. {clusters} agent conflicts seeded "
          f"(allocator=standard vs logistics=air on the same shipments).")
    print(f"Try:  curl {args.api}/runs")

if __name__ == "__main__":
    main()

