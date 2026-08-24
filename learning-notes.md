 # Learning Notes — Cinema Ticketing Backend
 
## 1. Architecture & Request Flow
 
- FastAPI DI: `get_current_user()` → decodes JWT → gets `sub` → queries Postgres → returns `AppUser`.
- `get_session()` → async DB session; `get_redis()` → Redis client.
- **Postgres** = source of truth (persistent). **Redis** = temporary/transient state.
- `hold_seat` flow: check waiting room active → check user admitted → verify seat exists → verify seat not booked → try Redis hold → return success.
- Practiced tracing a feature across multiple files instead of reading functions in isolation.
- Learned to separate: *what I know from code* / *what I think it does* / *what I still need to check*.
 
---
 
## 2. Waiting Room
 
- Queue → admission flow; state lives in Redis.
- `hold_seat` checks if waiting room is active for the showtime; if active, user needs a valid admission token first.
 
**To investigate**
- How tokens are created, stored, validated, expired.
- What happens if a token expires mid seat-hold attempt.
- Is admission strictly FIFO? How does concurrency affect it?
- Is the waiting room always active, or only under certain conditions?
- Redis-down behavior during queueing/admission.
 
---
 
## 3. Redis Holds & Concurrency
 
- `SET NX EX`: `NX` = create only if key absent (blocks double-hold); `EX` = TTL (auto-expiry).
- Seat states: `AVAILABLE` → `HELD` → `PENDING BOOKING` → `CONFIRMED` / `EXPIRED`.
- Defense in depth: Redis blocks duplicate *holds*; Postgres partial unique index blocks duplicate *active bookings*.
- Redis hold represents temporary user intent, not a confirmed purchase.
 
**To investigate**
- Redis hold is **not deleted** when `create_booking()` runs — currently relies only on TTL to expire it. Should it be deleted immediately instead?
- Possible TTL mismatch: `HOLD_TTL_SECONDS` (~300s) vs `BOOKING_HOLD_MINUTES` (10 min) — comment claims they match, verify actual value.
- Exact interaction between Redis holds and Postgres booking constraints needs a deeper look.
- Behavior when Redis is unavailable — during hold, during admission, after hold but before booking creation. Are errors caught meaningfully or does it crash?
 
---
 
## 4. Booking Flow
 
`create_booking()`:
1. Look up `ShowtimeSeat` rows → verify all exist & unbooked.
2. Calculate total via `price_snapshot`.
3. Create `Booking(status="pending")` + `BookingSeat(status="active")`.
4. Set `ShowtimeSeat.status = "booked"`.
5. Commit.
 
- `IntegrityError` (race on unique index) → converted to `ValueError` → router returns `409 Conflict`.
- `ShowtimeSeat.status="booked"` is set at *pending* creation, not at *paid* — "booked" ≠ "paid."
 
**To investigate**
- Can a confirmed booking be cancelled? What happens to payments/tickets/refunds in that case?
- `cancel_booking()` allows cancelling both pending and confirmed bookings — consequences unclear.
 
---
 
## 5. Expiry & Cleanup
 
- If unpaid: `pending` → `expires_at` reached → sweep worker → `Booking=expired` → `BookingSeat=cancelled` → seat becomes available again.
- Cleanup is currently a "sweep" strategy; a "lazy check" improvement was deferred in comments.
 
**To investigate**
- What if the sweep worker is down? Do expired bookings/seats stay stuck until it runs again?
- `pay_for_booking()` checks `status != "pending"` but **not** `expires_at` directly → an expired-but-not-yet-swept booking might still be payable. Needs verification — this is the most concrete potential bug.
 
---
 
## 6. Payment Flow
 
1. Check `idempotency_key` — if it exists, return the existing payment (no double charge).
2. Verify booking belongs to user & is still `pending`.
3. Create `Payment(status="pending")` → call sandbox provider → mark `succeeded`.
4. `confirm_payment()` → confirm booking → create tickets.
 
- Payment success and `confirm_payment()` happen in **separate commits** → if the second fails, you could end up with `Payment=succeeded`, `Booking=pending`, no tickets. Consistency risk.
- Payment provider is fake/sandbox — real failure/timeout scenarios untested.
 
**To investigate**
- Is `idempotency_key` uniqueness enforced at the DB level (unique constraint)?
- What happens with two concurrent requests using the same idempotency key before either commits?
- Is ticket creation idempotent if `confirm_payment()` is retried?
- What if ticket creation fails partway through the seat loop — does the transaction fully roll back?
 
---
 
## 7. Full Mental Model
 
```
Hold Seat → Redis Hold → Create Booking → Postgres(pending)
→ ShowtimeSeat=booked → Pay → Payment=succeeded
→ confirm_payment() → Booking=confirmed → Tickets created
 
(If unpaid: pending → expires_at reached → sweep worker
→ Booking=expired → BookingSeat=cancelled → Seat=available)
```
 
---
 
## 8. Production Hygiene
 
- Hardcoded secrets found (Postgres URL, Redis URL, JWT secret) → move to env vars / secret manager.
- Disable `echo=True` in the SQLAlchemy engine before production (leaks SQL + sensitive data into logs).
 
---
 
## 9. My Questions
 
1. **Redis TTL vs. booking expiry** — is there really a mismatch, and what does it mean?
2. **Expired-but-unswept booking payable?** — is this actually exploitable?
3. **Payment succeeds, `confirm_payment()` fails** — what's the real system state?
4. **Redis down** — what actually happens?
 
