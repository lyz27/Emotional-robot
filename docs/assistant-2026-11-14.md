# Durable assistant validation

The previous voice endpoint used `remember=False` and had no work tools. Work
records and browser-only reminders were disconnected from spoken requests. Voice
now shares committed records and a durable delivery queue.

- Local suite: 101 tests passed, including scheduling, restart recovery, request
  replay, clarification, ambiguous hours/targets, year rollover, notes, negative
  requests, failed writes, leases, rescheduling and safety before tool dispatch.
- Jetson: 23 assistant tests passed in the deployed Python environment.
- Live voice HTTP path: an authorized record returned in 47 ms, its next-day
  schedule query in 31 ms, and the same query after runtime restart in 41 ms.
  These are text/action timings, excluding capture, ASR, TTS and speaker output.
- A model paraphrase retained the saved appointment/time in 3.358 seconds, using
  a synonymous task name. It did not execute a new action.
- User physically confirmed both queries found the correct task/time, separated
  by goodbye and a new wake session.
- Future due delivery passed fake-clock and isolated API tests. The user's future
  appointment has not reached its scheduled time during this validation.

Source and SQLite databases were backed up locally before deployment. Production
records, audio and backups remain outside Git. Fixtures are synthetic, not database
exports. Free-form planning remains slower than the direct action path; wake/ASR
accuracy still affects the text that gets executed.
