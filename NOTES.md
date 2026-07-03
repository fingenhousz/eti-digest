# Ops notes — scheduling & delivery reliability

Written 2026-07-03 after investigating missing WhatsApp notifications.

## Findings (2026-07-03)

1. **CallMeBot quota exhausted (main reason no messages arrived).**
   On 2026-07-02 (run 28579127076) CallMeBot answered every send with HTTP 200
   but the body said *"You have **0** messages left. I need your support..."* —
   messages were accepted but not delivered. On 2026-07-01 it warned
   *"Up to 16 messages per 240 minutes"* (HTTP 210). The old code logged the
   body but never failed, so every run stayed green. `eti_digest.py` now
   validates the response body and the workflow fails loudly when CallMeBot
   does not confirm queuing.
   → **Manual action**: check remaining quota / supporter status at callmebot.com,
   and if sends keep failing, re-send `I allow callmebot to send me messages`
   to the CallMeBot WhatsApp bot (+34 644 59 78 23) to refresh the API key.
   Note: gmail-digest shares the same phone/apikey — its multiple runs per day
   were consuming the same quota.

2. **GitHub's native `schedule` fires 3-4h late** on this repo (cron
   `0 6 * * 1-5` firing at 09:16, 09:52, 09:41 UTC — i.e. the digest arrived
   around 11:00-12:00 Paris instead of 08:00, when it was delivered at all).
   This is GitHub's documented best-effort behavior and cannot be fixed in-repo.

3. **No external trigger is currently active for this repo** (unlike
   gmail-digest, where a cron-job.org job still dispatches on time at 05:00).
   The commit history shows cron-job.org was used before and then dropped in
   favor of native cron — that swap is what made the timing unreliable.
   → **Manual action (2 min)**: create the cron-job.org job below.

## External scheduler setup (primary trigger) — TO CONFIGURE

Create a job on cron-job.org (or any external cron):

- **URL**: `https://api.github.com/repos/florianingenhousz/eti-digest/actions/workflows/digest.yml/dispatches`
- **Method**: `POST`
- **Headers**:
  - `Authorization: Bearer <PAT>` — a GitHub personal access token with `repo` + `workflow` scope (the same PAT already used by the gmail-digest cron-job.org job works)
  - `Accept: application/vnd.github+json`
- **Body**: `{"ref":"main"}`
- **Schedule**: 06:00 UTC, Monday-Friday (= 08:00 Paris in summer/CEST; note
  this shifts to 07:00 Paris in winter unless you adjust the external job).

A successful dispatch returns HTTP 204 with an empty body.

## Layers of defense

| Layer | Time (UTC) | Purpose |
|---|---|---|
| cron-job.org → workflow_dispatch | 06:00 Mon-Fri | primary, on time (**needs manual setup, see above**) |
| native `schedule` cron | 06:30 Mon-Fri (best effort, often hours late) | fallback, self-skips if a run already succeeded today |
| `keepalive.yml` (weekly) | Sun 04:00 | prevents GitHub auto-disabling the schedule after 60 days without pushes |
