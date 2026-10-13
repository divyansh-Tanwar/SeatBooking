# Free public demo: Render + Neon

The `render.yaml` Blueprint creates exactly one **free web service**. It creates no paid resources. Supply a Neon Free PostgreSQL connection string in the Render `DATABASE_URL` environment field; never commit it or paste it into chat.

1. Sign in to Neon, select the Free plan, and create a SeatLock project/database.
2. Select PostgreSQL 17 and a region close to the Render service when offered.
3. Push this repository's deployment changes to GitHub.
4. In Render choose New → Blueprint and select this repository and its main branch.
5. Review that the service plan is **Free**. Paste Neon's connection string (including SSL parameters) into `DATABASE_URL` directly in Render.
6. Deploy. The build installs the lockfile and collects static assets. Startup applies migrations, seeds public events, and starts the web and demo-job processes.
7. Use the successful Render service's HTTPS URL on the resume, after checking registration, seat reservation, simulated checkout and booking history.

## Public-demo behavior

- `DEBUG=0`, secure cookies, and provider-generated independent secrets.
- Render's assigned hostname is added to Django's host/CSRF allowlists. A custom domain additionally needs `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`.
- Visitors create their own customer accounts. There is no shared customer password or usable organizer login.
- Sample events belong to an inactive organizer with an unusable password. Past booked events are never changed; startup adds replacement events when the old booking deadlines have passed.
- PostgreSQL transactions, seat row locks, uniqueness, expiration checks and payment idempotency are unchanged.
- Availability uses five-second polling. Redis/WebSockets and separate Celery workers remain available through the full Docker configuration, but are not deployed in this free profile.
- Demo jobs run alongside the web process. Both are supervised: if one exits, the service exits so the host can restart it.
- When the free web service sleeps, background processing pauses. Unpaid holds still have absolute database deadlines; they cannot be confirmed after expiration. Pending work resumes on wakeup.
- Email delivery is disabled on the public demo; confirmed bookings are visible in the app.
- One instance only. This startup migration/supervisor arrangement is for a portfolio demo, not a multi-instance production deployment.

## Free-tier limits

Render's free web service can sleep when idle, so the first visitor may see a cold start. Render free PostgreSQL expires after 30 days; this Blueprint deliberately uses an external database instead. Neon Free has compute/storage quotas and can suspend when quotas are exhausted. Background polling consumes database compute while the web service is awake. Free service limits can change; inspect both dashboards before deploying.

Sources: https://render.com/docs/free and https://neon.com/docs/introduction/plans

No uptime guarantee is implied. Do not present the public free demo's performance as the local benchmark result.
