# REST API
All routes omit trailing slashes. Amounts are integer minor units (149900 INR = ₹1,499).
List endpoints use `?page=2` and return count/next/previous/results.

## Authentication
1. GET /api/auth/csrf returns csrfToken and sets its cookie.
2. POST /api/auth/register with username, password, optional email.
3. POST /api/auth/login with username, password.
4. Login/register return access and user, and set an HttpOnly refresh cookie.
5. Send Authorization: Bearer ACCESS on protected APIs.
6. POST /api/auth/refresh rotates the cookie and returns a new access token.
7. POST /api/auth/logout revokes the session and clears the cookie.
8. GET /api/auth/me returns username and role.

Register/login/refresh/logout require X-CSRFToken and the CSRF cookie.
Passwords require at least ten characters and Django's password validators.

## Events and organizer
- GET /api/events?search=music
- GET /api/events/{event_id}/seats
- GET or POST /api/organizer/events (organizer only)
- POST /api/organizer/events/{id}/seats with rows (1–26), columns (1–30), price
- POST /api/organizer/events/{id}/publish
- GET /api/organizer/operations

Create event fields: title, description, venue, starts_at, booking_deadline, currency. Dates use ISO 8601 with timezone. Supported currencies: INR, USD, EUR, GBP.
Inventory can be generated once while in draft. Published configuration cannot be edited. Organizer queries and writes enforce ownership.

## Reservation
POST /api/reservations
Header: Idempotency-Key: unique-client-generated-key
Body: {"seat_id": "UUID"}

201: hold representation with id, seat_label, event_id, price, currency, status and expires_at.
409 seat_unavailable / sales_closed.
GET /api/reservations/{id}: own current state, server_time, payment_status and booking_id.
POST /api/reservations/{id}/cancel: repeated release is harmless; confirmed booking returns 409.
POST /api/reservations/{id}/confirm with empty JSON body and Idempotency-Key:
202: attempt_id, status_url, status. Poll status_url. An already confirmed reservation with a new key returns its booking representation.
GET /api/bookings: customer's confirmed bookings.

Keys are required for reserve and confirm, up to 100 ASCII characters. Reuse with different inputs returns 409. Replays return the stored response. Unexpected rolled-back failures are retryable with the same key.
Unauthorized object access returns 404. Validation errors return 400. Authentication errors return 401. Role/CSRF/signature failures return 403. Lock timeout/deadlock return 503 + Retry-After.

## Callback simulator
POST /api/payments/callback
Body: event_id, attempt_id, amount, currency, outcome ("success" or "failure").
X-Simulator-Signature is the hexadecimal HMAC-SHA256 of the exact raw request body, keyed by SIMULATOR_SECRET.
The normal dispatcher calls the shared service directly. This route exercises authenticated external callback delivery.
For manual failure/late-result demonstrations, pause the worker before starting checkout, then:
```
python manage.py simulate_payment ATTEMPT_UUID --outcome failure --event-id demo-failure-1
```

## WebSocket
Connect /ws/events/{event_id}/ from an allowed origin.
Messages: subscribed; seat.changed with seat_id and version.
Only public published events are subscribable. Messages contain no customer data.
Subscribe then fetch the REST snapshot, and periodically refetch while visible. Notifications are not booking confirmations.
