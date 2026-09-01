"""
app/services/auto_submit_service.py
Server-side, browser-independent enforcement of exam deadlines.

Why this exists: the client-side exam timer (templates/exam_page.html)
auto-submits when it reaches zero, but that only works if the student's
browser is open, connected, and running JS at that exact moment. A closed
tab, a crashed laptop, a dead connection, or a student who never returns
to the exam page must NOT leave their attempt permanently 'in_progress'
with no score. This module is that safety net — a small background sweep,
running in-process (no new infrastructure — reuses the exact
threading.Thread pattern app/__init__.py already uses for periodic cache
cleanup), that finds attempts whose stored effective_deadline has passed
and finalizes them using the same finalize_exam_attempt() the student's
own manual submit uses.

Scale: this is written to be safe from thousands of attempts becoming due
at the same moment (e.g. one large Scheduled Exam ending for everyone at
once) — see claim_due_attempts_batch() in app/db/attempts.py for the
locking design (FOR UPDATE SKIP LOCKED, bounded batch size, no long-held
transaction). Each attempt is finalized independently and failures are
isolated per-attempt, never aborting the rest of the batch.
"""

import logging
import time as _time

import app.config as config

log = logging.getLogger(__name__)


def sweep_due_attempts_once(batch_size: int = None) -> int:
    """Claim and finalize a single batch of due/stuck attempts. Returns
    how many were claimed (0 means nothing was due right now)."""
    from app.db.attempts import claim_due_attempts_batch
    from app.services.exam_service import finalize_exam_attempt

    batch_size = batch_size or config.AUTO_SUBMIT_BATCH_SIZE
    claimed = claim_due_attempts_batch(batch_size)
    for attempt in claimed:
        attempt_id = attempt.get("id")
        try:
            ok, result_id, msg = finalize_exam_attempt(attempt_id)
            if ok:
                log.info("[auto_submit] finalized attempt_id=%s result_id=%s (%s)",
                         attempt_id, result_id, msg)
            else:
                # Left in 'finalizing' — the next sweep's stale-reclaim
                # clause will retry it once finalization_claimed_at ages
                # past the stale threshold. One failed attempt never
                # blocks or rolls back the rest of this batch.
                log.warning("[auto_submit] finalize failed attempt_id=%s: %s", attempt_id, msg)
        except Exception:
            log.exception("[auto_submit] unhandled error finalizing attempt_id=%s", attempt_id)
    return len(claimed)


def run_sweep_loop() -> None:
    """The background thread's body — started once from
    app/__init__.py:_start_auto_submit_sweep(). Sleeps between ticks, and
    on each tick keeps draining full batches back-to-back (without
    sleeping) so a mass-deadline moment — many attempts due at once — is
    worked off promptly instead of trickling out one batch per sleep
    interval; it only goes back to sleeping once a tick claims fewer than
    a full batch, i.e. it has genuinely caught up."""
    while True:
        try:
            claimed = sweep_due_attempts_once()
            while claimed >= config.AUTO_SUBMIT_BATCH_SIZE:
                claimed = sweep_due_attempts_once()
        except Exception:
            log.exception("[auto_submit] sweep tick failed")
        _time.sleep(config.AUTO_SUBMIT_SWEEP_INTERVAL_SECONDS)
