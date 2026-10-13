# Architecture and decisions

## Components
Browser → DRF APIs → PostgreSQL.
PostgreSQL outbox → Celery dispatcher → simulated payment processing / Channels+Redis / SMTP.
Channels → browser notification → authoritative REST snapshot.

Redis never determines ownership. Database commits do not depend on Redis availability. A broker outage delays callbacks and emails; holds still expire and can be reclaimed.

## Lock order
Idempotency record → EventSeat → Reservation → PaymentAttempt → PaymentEvent.
Candidate lookup is unlocked; state is re-read while locked. Joined Event rows are not locked by seat or reservation services.
The expiration sweeper discovers candidates, then takes the same seat-first locks. It never locks reservations first.

Transactions use READ COMMITTED. Lock/statement timeouts return retryable HTTP 503. Expected occupancy conflicts return 409. Database errors are allowed to roll back; ordinary business rejections are response values so an expiration can commit.

## Invariants
1. Conditional uniqueness permits only one HELD or CONFIRMED reservation per seat.
2. One booking and one payment attempt per reservation.
3. Expiration uses database wall time after locking. At equality the hold has expired.
4. A confirmed reservation never becomes available.
5. Payment callbacks cannot change an already terminal payment outcome.
6. Client-provided price, customer identity, role and payment outcomes do not govern booking writes.
7. Idempotency keys are scoped to customer + operation and fingerprinted.
8. Availability snapshots can immediately become stale; the reserve endpoint decides.

The reservation's state is the authoritative allocation. A separate cached seat status is intentionally absent. EventSeat.version orders its change notifications. Public snapshots contain no reservation or customer identifiers.

## Payment semantics
Confirm creates a pending attempt and a durable payment outbox record, returns 202 plus a status URL. The simulator runs after a short delay. Its event handler validates the amount/currency and transitions under locks. Late success becomes UNFULFILLED; it never revives a reservation.
A retry of the same HTTP idempotency key returns its original response. Use the status URL for current state.
The callback endpoint verifies HMAC-SHA256 of the raw JSON body. Stable event IDs and fingerprints reject altered replays.

## Outbox semantics
Workers claim pending work with SELECT FOR UPDATE SKIP LOCKED, assign a unique lease token, and commit. Delivery is outside that transaction. Acknowledgment uses the lease token as a fencing check. Worker time limits are shorter than the lease.
A crash after delivery permits repeated delivery. Payment deduplication prevents duplicate bookings. SMTP delivery is at-least-once effort and may duplicate. Successful group publication is not proof that a browser received it.
Failures back off up to five minutes and remain inspectable. Alerts should use overdue counts and last_error. There is no silent discard limit.

## Authentication
JWT claims contain sub, sid, iss, aud, iat, exp. Access lasts at most five minutes.
Refresh tokens are high-entropy opaque credentials, hashed with SHA-256 in PostgreSQL. Session and credential locks serialize consumption and rotation.
Replay revocation commits before returning an authentication failure. Access authentication checks the session and active user.
Cookies are same-site and HttpOnly; mutating cookie routes explicitly enforce CSRF. Browser tabs coordinate refresh through Web Locks when available; strict replay policy can require re-login in older browsers or after response loss.

## Reconnection
Subscribe acknowledgment precedes the REST fetch. Incoming notifications mark a fetch dirty and request another serialized refresh. Reconnect, focus, expiry, conflicts and periodic polling trigger resynchronization. Silent channel-layer drops therefore do not leave the page stale indefinitely.

## Deferred features
Multiple-seat atomic checkout, real payments/refunds, paid cancellation, inventory editing after publication, password reset/email verification, distributed abuse controls, retention/archiving, and horizontal load testing.
