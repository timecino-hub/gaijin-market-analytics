# Future Development Log

This log records future planning only. It does not mean the features below are
implemented, enabled, or used by `RuleBasedV1`.

## Administrator Strength Assessment

Future versions may maintain administrator-entered strength assessments for
each vehicle or item. The administrator should enter a strength score, not a
direct price multiplier.

Planning principles:

- Use a bounded score such as `0..100`, with `50` as neutral.
- Distinguish game modes.
- Store `effective_from`, `expires_at`, `reason`, `evidence`, and `version`.
- Start in shadow mode only.
- Do not affect current `RuleBasedV1`.
- Accumulate data and backtest before public use.
- Allow limited use only after validation in a future `RuleBasedV2`.
- Never overwrite older assessment versions.

## Strength Change Events

Future analysis should distinguish absolute current strength from recent buff
or nerf events. Short-term price changes may be explained better by recent
changes than by the absolute strength level.

Possible manually maintained event types:

- Battle rating adjustments.
- Weapon or ammunition changes.
- Armor changes.
- Radar, missile, or thermal-imaging changes.
- Game mechanics changes.
- Competitive environment changes.

## Return Events And Supply Shocks

Future versions may maintain a manual event calendar for return events and
supply shocks.

Event categories to distinguish:

- Tradable coupon re-release.
- Loot boxes producing old vehicle coupons.
- Store return sales.
- Substitute vehicle returns.
- Anniversary or seasonal events.
- Non-tradable vehicle returns.

Potential fields:

- `event_type`
- `event_start`
- `event_end`
- `affected_item_id`
- `direct_coupon_supply`
- `substitute_availability`
- `expected_severity`
- `source`
- `confirmed`
- `administrator_notes`

Initial use should be annotation and historical backtesting only. These events
should not directly modify public results until separately validated.

## Community Ratings

Community ratings should be considered only after the website is complete,
stable, has user accounts, and has anti-abuse controls.

Potential workflow:

```text
user rating
-> aggregation and anti-abuse checks
-> administrator review
-> administrator decision
-> new formal assessment version
```

User averages must not automatically modify formal strength factors.

Risks to handle:

- Sockpuppet voting.
- Faction bias.
- Holder manipulation.
- Game-mode differences.
- Sample size.
- Short-term sentiment after updates.

## Development Order

1. Finish the current market hard cap.
2. Stabilize the website and backtest framework.
3. Add administrator strength assessment in shadow mode.
4. Add a manually maintained return-event calendar.
5. Backtest strength and event features.
6. Create a future `RuleBasedV2`.
7. Consider community ratings only after the website is stable and has user
   accounts plus anti-abuse controls.
