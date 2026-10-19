# Auth and tenant isolation

The API issues HMAC Bearer access tokens. Protected requests parse the token and reload an active user. Client payloads never supply `user_id`.

All MySQL run reads use `(user_id, run_id)` scoped methods. Cross-user run, message, event, trace, and checkpoint access return no resource (HTTP 404 for API routes), preventing IDOR and existence disclosure.
