# SYNTHETIC logic-exit sample

**No real data.** Invented Foundry Actions, Query, and Function source in
Palantir's published Actions API v2 / Query API wire shapes, to prove the logic
exit (see [`../foundry_exit/logic_exit.py`](../../foundry_exit/logic_exit.py)).

- `actionTypes.json` — List Action Types (2 actions, typed parameters)
- `queryTypes.json` — Get Query Type (1 query, typed params + output)
- `functions/avgDelayByRoute.ts` — invented Function source, sealed VERBATIM

Carries the CONTRACT (definitions) + SOURCE. Does NOT carry the Actions engine
or Functions runtime — attested on the shard. Run:

```
axm-logic-exit samples/logic_exit_synthetic --out ./logic_exit_out
```
