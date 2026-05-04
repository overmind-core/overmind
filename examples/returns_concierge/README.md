# Returns Concierge

Decides refund / replace / deny / escalate on a customer return request and
drafts the customer-facing reply.

**Stack:** OpenAI SDK (function calling) + local JSON orders / customers /
photo-inspection fixtures.

## Register

```bash
overmind agent register returns-concierge agent:run
overmind agent validate returns-concierge --data data/seed.json
overmind setup returns-concierge
overmind optimize returns-concierge
```

## Seeded sub-optimalities

- System prompt has no policy table for final-sale items, abuse patterns
  (`serial_returner`), or LTV-based goodwill thresholds.
- `customer_message` has no tone or length guidance — replies come back
  robotic and inconsistent.
- Always calls `inspect_condition_photos`, even when no photos were attached
  (sometimes hallucinates damage from a `None` URL).
- Defaults to `gpt-4o`; the 90% of within-window same-decision cases don't
  need it.
- Schema drift: `amount` returned as `"19.99"`, `19.99`, or `"$19.99"`.

## Input / output

```python
run(
    {
        "order_id": "O-9002",
        "customer_id": "C-2",
        "reason": "Headphones arrived with a cracked earcup",
        "photos_url": "https://photos.example.com/9002-a",
        "message": "Just got these and one ear is cracked. Can I get a replacement?",
    }
)
# -> {
#   "decision": "replace",
#   "amount": 199.00,
#   "restocking_fee": 0,
#   "customer_message": "Sorry to hear about the damage...",
#   "reasoning": "..."
# }
```
