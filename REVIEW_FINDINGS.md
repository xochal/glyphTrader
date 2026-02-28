# Review Findings — glyphTrader Build Plan

Summary of all issues identified across 4 review rounds (2 architecture + 2 security).
These are bugs and pitfalls that must be handled correctly during implementation.

---

## CRITICAL — Architecture (5 issues)

### 1. Order Fill Deduplication (`processed_order_ids`)
**Problem**: The Tradier service uses an in-memory `processed_order_ids` set to avoid processing the same fill twice. On restart, this set is empty, causing duplicate processing of recent fills.
**Solution**: On startup, load recent fill IDs from the database (last 24 hours of `exit_history` and `entry_fills`). Populate `processed_order_ids` before starting the monitor cycle.

### 2. Atomic `close_position()`
**Problem**: Closing a position involves multiple steps (cancel orders, record exit, update DB). If the process crashes mid-close, the position can be left in an inconsistent state (orders cancelled but exit not recorded, or exit recorded but orders still active).
**Solution**: Wrap the entire close sequence in a database transaction. Cancel orders first (idempotent), then record exit and delete position in a single atomic commit. On startup reconciliation, detect and clean up any half-closed positions.

### 3. Wait-for-Cancel Before Replacing Orders
**Problem**: Tradier order cancellation is asynchronous. If you cancel an order and immediately place a replacement, both can be active simultaneously, causing double fills.
**Solution**: After cancelling, poll the order status (up to 5 seconds, 500ms intervals) until status is `cancelled` or `rejected`. Only then place the replacement. The production Tradier service already implements `_wait_for_cancel()` — port it exactly.

### 4. Per-Symbol V4 Thresholds (Not Global)
**Problem**: The V4 score filter uses per-symbol thresholds (68 for semis, 78 for solar, 80 for nuclear, 82 for mega tech, 75 default). Using a single global threshold of 75 would miss profitable semiconductor entries and allow weak mega tech entries.
**Solution**: Look up the symbol in `watchlist.json` for its `v4_threshold`. Fall back to 75 only if the symbol is somehow not in the watchlist.

### 5. Stagnant Win Exit Requires T1 Hit
**Problem**: The time-decay stop (20 days, >5% profit → exit) should only trigger AFTER T1 has been hit. Without this condition, positions that haven't hit T1 but are profitable for 20 days would be force-exited, missing the T1 partial exit.
**Solution**: Check `t1_hit == True` before applying the stagnant win time stop. If T1 hasn't been hit yet, the time stop does not apply regardless of how many days have passed.

---

## IMPORTANT — Architecture (10 issues)

### 6. ATR Precision (Integer Cents)
All ATR-derived values (stop prices, target prices) must be calculated in float, then converted to integer cents only at the final storage/comparison step. Do NOT accumulate rounding errors by converting to cents at intermediate steps.

### 7. Cross-Trade Fill Attribution
When multiple positions exist for the same symbol (e.g., after a pyramid that creates a new position), fill detection must match fills to the correct position. Use order IDs stored in the database to attribute fills, not just symbol matching.

### 8. OCO Leg Flattening
Tradier OCO parent orders have no `symbol`, `side`, or `exec_quantity` fields. You must look at the child legs to get these values. The production fix: when processing fills, if the order has `class=oco` or `class=otoco`, iterate the legs array to find the filled leg.

### 9. State Name Consistency
The production system uses different names for the same concept in different places (e.g., `stop_loss_price` vs `stop_price`, `t1_hit` vs `t1_filled`). Pick ONE naming convention and use it everywhere in glyphTrader. Suggested: `stop_price`, `t1_hit`, `t2_hit`, `t3_hit`.

### 10. Breakeven Capping
The breakeven stop is set to `entry_price + 1.0%`, but this is CAPPED at `t1_price - 1.0%`. Without the cap, when ATR is very small, the breakeven stop could be set ABOVE the current price, causing an immediate stop-out.

### 11. Base Stop Update on Breakeven
When breakeven is triggered, `base_stop` must also be updated to the new breakeven level. Stepped stops ratchet from `base_stop`, so if base_stop isn't updated, stepped stops would ratchet from the original (lower) stop instead of the breakeven floor.

### 12. Signal Sort by V4 Score
When multiple signals fire on the same day, they must be sorted by V4 score descending before capital allocation. This ensures the highest-conviction signals get filled first when capital is limited.

### 13. EMA Uses Close-Only Mode
The EMA crossover filter uses `close_only` confirmation — it compares `ema_5` vs `ema_13` using close prices only. Other modes (high/low, full stack) produce different signals.

### 14. Sector Tier Multipliers
TIER_1 (Semiconductors, Nuclear, Cloud/SaaS) gets 1.5x position size multiplier. All other active tiers get 1.0x. This is applied AFTER VIX sizing adjustment. The multiplier is in `watchlist.json` per stock.

### 15. Max Pyramids = 2 (3 Tranches Total)
A position can have at most 2 pyramid additions (initial + 2 adds = 3 tranches). Each pyramid requires V4 >= 75 regardless of the stock's entry threshold.

---

## CRITICAL — Security (1 issue)

### 16. Recovery Key Cannot Re-Encrypt When Locked
**Problem**: If the admin forgets the password, the recovery key endpoint must re-encrypt all Tradier credentials with a new Fernet key derived from the new password. But the system is locked (Fernet key not in memory), so it can't decrypt the existing credentials to re-encrypt them.
**Solution**: The recovery flow must: (1) verify the recovery key against its bcrypt hash, (2) prompt for a new password, (3) derive new Fernet key, (4) since we can't decrypt old credentials, the user must re-enter their Tradier API token during recovery. Store the new encrypted credentials. Log this as an audit event.

---

## HIGH — Security (3 issues)

### 17. Fernet Key in Memory
The derived Fernet key lives in process memory while the system is unlocked. This is acceptable for this threat model (single-user, LAN deployment), but the key should be zeroed when the system locks (session timeout) and never written to disk or logs.

### 18. Docker Install Supply Chain
`setup.sh` installs Docker via `curl | sh` pattern. This is a supply chain risk.
**Mitigation**: Pin the Docker install script URL, verify GPG signatures, and document the risk. Consider requiring Docker as a prerequisite instead of auto-installing.

### 19. Git Pull Without GPG Verification
`update.sh` runs `git pull` without verifying commit signatures. A compromised repo could inject malicious code.
**Mitigation**: Document this risk. For LAN-only deployment, the risk is low. For internet-facing, consider GPG-signed tags and `git verify-tag` before pulling.

---

## MEDIUM — Security (6 issues)

### 20. Setup Token Visible in Container Logs
The first-boot setup token is printed to container logs (`docker logs`). Anyone with Docker access can read it.
**Mitigation**: Print the token only to stdout during interactive `setup.sh` run, not in container logs. Or use the `.env` `ADMIN_PASSWORD` alternative.

### 21. Degraded Token Co-Location
The degraded-mode JWT (encrypted with `jwt_secret`) is stored in the same SQLite database as the `jwt_secret` itself. An attacker with DB access gets both.
**Mitigation**: Acceptable for threat model (physical access = game over anyway). Document the design decision.

### 22. No Docker `cap_drop`
Containers should drop all Linux capabilities and add back only what's needed.
**Mitigation**: Add `cap_drop: [ALL]` to all services in `docker-compose.yml`. The backend needs no special capabilities.

### 23. SQLite WAL File Exposure
WAL and SHM files contain recent writes and could leak data if the `data/` volume is accessed externally.
**Mitigation**: Ensure `data/` directory permissions are 700. The `.gitignore` already excludes `*.db-wal` and `*.db-shm`.

### 24. VERSION File Validation
The backend reads `VERSION` at startup. A malformed VERSION file could cause crashes or injection if used unsafely.
**Mitigation**: Validate VERSION matches `^\d+\.\d+\.\d+$` regex. Use a fallback version (e.g., "0.0.0-unknown") if validation fails.

### 25. API Timeout Enforcement
Long-running API requests (e.g., fetching market data) should have timeouts to prevent resource exhaustion.
**Mitigation**: Set `httpx` client timeouts (connect=5s, read=30s). Add FastAPI middleware timeout for all endpoints (60s max).

---

## Checklist Summary

| Severity | Count | IDs |
|----------|-------|-----|
| CRITICAL (Architecture) | 5 | 1-5 |
| IMPORTANT (Architecture) | 10 | 6-15 |
| CRITICAL (Security) | 1 | 16 |
| HIGH (Security) | 3 | 17-19 |
| MEDIUM (Security) | 6 | 20-25 |
| **Total** | **25** | |

Each issue should be addressed during the relevant build phase. Mark items as DONE in this file as they are implemented.
