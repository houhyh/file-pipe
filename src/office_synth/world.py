from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from .schemas import Fact, SeedBlueprint, WorldInstance, money
from .storage import identifier


def instantiate(seed: SeedBlueprint, instance: int, config: dict) -> WorldInstance:
    instance_seed = int(identifier("n", [seed.id, config["run"]["random_seed"], instance]).split("_")[1], 16)
    rng = random.Random(instance_seed)
    start = date(2026, 1, 1) + timedelta(days=rng.randint(0, 90))
    invoice_date = start + timedelta(days=12)
    days = rng.choice([15, 30, 45])
    changed_days = days + 15
    event_date = invoice_date + timedelta(days=1)
    items = []
    for i, product in enumerate(["Packaging boxes", "Safety gloves", "Label rolls"], 1):
        ordered = rng.randint(20, 100)
        received = ordered - rng.randint(0, 3)
        unit = money(Decimal(rng.randint(100, 9000)) / 100)
        items.append({"line_id": f"line_{i}", "product": product, "ordered": ordered, "received": received,
                      "invoiced": ordered, "unit_price": str(unit),
                      "ordered_amount": str(money(unit * ordered)),
                      "payable_amount": str(money(unit * received))})
    total = sum((money(i["payable_amount"]) for i in items), Decimal("0.00"))
    invoiced = sum((money(i["ordered_amount"]) for i in items), Decimal("0.00"))
    balance = money(invoiced - total)
    workspace_id = identifier("workspace", [seed.id, instance_seed])
    buyer = f"Example Meridian {rng.randint(100,999)} Ltd (fictional)"
    supplier = f"Example Cedar {rng.randint(100,999)} Supplies (fictional)"
    facts = []

    def fact(key, value, kind="string", unit="", inputs=None, calculation=None, since=None, until=None):
        facts.append(Fact(fact_id=key, type=kind, value=value, unit=unit, valid_from=str(since or start),
                          valid_to=str(until) if until else None, input_fact_ids=inputs or [], calculation=calculation))

    fact("buyer", buyer)
    fact("supplier", supplier)
    fact("order_id", f"PO-{workspace_id[-8:].upper()}")
    fact("invoice_id", f"INV-{workspace_id[-8:].upper()}")
    fact("order_date", str(start), "date")
    fact("receipt_date", str(start + timedelta(days=10)), "date")
    fact("invoice_date", str(invoice_date), "date")
    fact("payment_days_v1", days, "integer", "days", until=event_date if config["generation"]["enable_event"] and instance == 0 else None)
    fact("due_date_v1", str(invoice_date + timedelta(days=days)), "date", inputs=["invoice_date", "payment_days_v1"], calculation="invoice_date + payment_days_v1")
    for item in items:
        for key in ("ordered", "received", "invoiced", "unit_price", "payable_amount"):
            fid = f"{item['line_id']}.{key}"
            fact(fid, item[key], "decimal" if key.endswith(("price", "amount")) else "integer", "USD" if key.endswith(("price", "amount")) else "units",
                 inputs=[f"{item['line_id']}.received", f"{item['line_id']}.unit_price"] if key == "payable_amount" else [],
                 calculation="received * unit_price" if key == "payable_amount" else None)
    fact("invoice_total", str(invoiced), "decimal", "USD")
    fact("payable_total", str(total), "decimal", "USD", inputs=[f"{i['line_id']}.payable_amount" for i in items], calculation="sum(received * unit_price)")
    fact("held_total", str(balance), "decimal", "USD", inputs=["invoice_total", "payable_total"], calculation="invoice_total - payable_total")
    rules = [{"id": "terms_v1", "version": "v1", "days": days, "valid_from": str(start), "valid_to": None, "basis": "synthetic_choice"}]
    events = []
    if config["generation"]["enable_event"] and instance == 0:
        rules[0]["valid_to"] = str(event_date)
        rules.append({"id": "terms_v2", "version": "v2", "days": changed_days, "valid_from": str(event_date), "valid_to": None, "basis": "synthetic_choice"})
        fact("payment_days_v2", changed_days, "integer", "days", since=event_date)
        fact("due_date_v2", str(invoice_date + timedelta(days=changed_days)), "date", inputs=["invoice_date", "payment_days_v2"], calculation="invoice_date + payment_days_v2", since=event_date)
        events.append({"id": "terms_change", "type": "payment_terms_change", "effective_at": str(event_date),
                       "old_rule": "terms_v1", "new_rule": "terms_v2", "scope": "Outstanding unpaid invoice by mutual agreement", "affected_invoice": f"INV-{workspace_id[-8:].upper()}"})
    return WorldInstance(id=workspace_id, seed_id=seed.id, instance_seed=instance_seed,
                         role=seed.dimensions["role"], entities=[{"id": "buyer", "name": buyer}, {"id": "supplier", "name": supplier}],
                         relationships=[{"from": "buyer", "to": "supplier", "type": "purchases_from"}],
                         transactions=items, rules=rules, events=events, time_range=[str(start), str(invoice_date + timedelta(days=changed_days))],
                         facts=facts, business_invariants=["received <= ordered", "payable + held = invoiced", "payable = sum(received * unit_price)", "half-open rule validity intervals", "pay only received quantities"],
                         group_key=seed.family_key, input_refs=[seed.id])


def world_errors(world: WorldInstance) -> list[str]:
    errors = []
    ledger = world.ledger()
    total = Decimal("0")
    invoice_total = Decimal("0")
    if len(ledger) != len(world.facts):
        errors.append("duplicate fact IDs")
    for line in world.transactions:
        if not 0 <= line["received"] <= line["ordered"] or line["invoiced"] != line["ordered"]:
            errors.append(f"quantity mismatch: {line['line_id']}")
        expected = money(money(line["unit_price"]) * line["received"])
        if expected != money(line["payable_amount"]):
            errors.append(f"line calculation: {line['line_id']}")
        total += expected
        ordered_amount = money(money(line["unit_price"]) * line["invoiced"])
        invoice_total += ordered_amount
        if ordered_amount != money(line["ordered_amount"]):
            errors.append(f"invoice line calculation: {line['line_id']}")
        for key in ("ordered", "received", "invoiced", "unit_price", "payable_amount"):
            if str(ledger.get(f"{line['line_id']}.{key}")) != str(line[key]):
                errors.append(f"ledger transaction mismatch: {line['line_id']}.{key}")
    if money(total) != money(ledger["payable_total"]):
        errors.append("payable total")
    if money(invoice_total) != money(ledger["invoice_total"]):
        errors.append("invoice total")
    if money(ledger["payable_total"]) + money(ledger["held_total"]) != money(ledger["invoice_total"]):
        errors.append("balance")
    for rule in world.rules:
        suffix = rule["version"]
        if ledger[f"payment_days_{suffix}"] != rule["days"]:
            errors.append(f"rule ledger mismatch: {suffix}")
        due = date.fromisoformat(ledger["invoice_date"]) + timedelta(days=rule["days"])
        if str(due) != ledger[f"due_date_{suffix}"]:
            errors.append(f"due date: {suffix}")
    for previous, current in zip(world.rules, world.rules[1:]):
        if previous["valid_to"] != current["valid_from"] or previous["valid_from"] >= current["valid_from"]:
            errors.append("rule validity overlap or gap")
    if not ledger["order_date"] <= ledger["receipt_date"] <= ledger["invoice_date"]:
        errors.append("order/receipt/invoice chronology")
    rules_by_id = {r["id"]: r for r in world.rules}
    for event in world.events:
        old, new = rules_by_id.get(event["old_rule"]), rules_by_id.get(event["new_rule"])
        if not old or not new or old["valid_to"] != event["effective_at"] or new["valid_from"] != event["effective_at"]:
            errors.append("event rule/version mismatch")
    for fact in world.facts:
        if any(ref not in ledger for ref in fact.input_fact_ids):
            errors.append(f"unknown fact input: {fact.fact_id}")
    return errors
