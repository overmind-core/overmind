# AP Invoice Triage

Decides what to do with an inbound vendor invoice: assign GL codes, flag
exceptions, route to the right approver tier.

**Stack:** OpenAI SDK (function calling) + local JSON vendor / PO / history
fixtures.

## Register

```bash
overmind agent register ap-invoice agent:run
overmind agent validate ap-invoice --data data/seed.json
overmind setup ap-invoice
overmind optimize ap-invoice
```

## Seeded sub-optimalities

- System prompt has no 3-way-match rule, no segregation-of-duties thresholds,
  and no calibration of T1/T2/T3 approver tiers.
- Tool ordering is wrong: model decides first, *then* runs `fraud_signals`,
  so duplicate-invoice / weekend-submission bait slips through.
- `fetch_purchase_order` description doesn't say to skip when there's no PO
  number, so the model burns a round on every PO-less invoice.
- Defaults to `gpt-4o`; the 80% of invoices that match a PO cleanly don't
  need it.
- No JSON schema; `gl_codes` sometimes returned as a comma-string.

## Input / output

```python
run(
    {
        "invoice": {
            "invoice_number": "INV-A-1004",
            "vendor_name": "AWS",
            "po_number": "PO-2024-0451",
            "submitted_at": "2026-04-12 Mon",
            "total": 12450.00,
            "lines": [{"description": "AWS cloud hosting - March", "amount": 12450.00}],
        }
    }
)
# -> {
#   "decision": "approve",
#   "gl_codes": ["6105"],
#   "exceptions": [],
#   "approver_tier": "T1",
#   "reasoning": "..."
# }
```
