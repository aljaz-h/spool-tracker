"""Keeps imports and backups visible after the user leaves the page.

An Import Review session (ImportSession) runs in the background - scan,
then wait for review, then commit - and a backup runs in a worker. This
module mirrors each one's state into two places:

- one DataLog row (Settings -> Logs, the admin view): RUNNING while
  scanning/importing, WAITING while the review sits unconfirmed, then
  SUCCESS/FAILED/CANCELLED, with finished_at for the duration column.
- one Notification for the profile that started it (the bell): rewritten
  in place as the import moves along, and marked unread again whenever
  there's something for the user to do or know (ready to review, done,
  failed) - a progress tick alone never re-notifies.

sync_import_session runs from ImportSession's post_save signal (see
apps.py), so every status change - wherever in the code it happens -
is reflected without each call site having to remember to.
"""

from django.urls import reverse
from django.utils import timezone


def _session_label(session):
    if session.source_filename:
        return session.source_filename
    return f"{session.get_source_display()} import"


def _import_state(session):
    """(log_status, notification_message, notify_unread, finished) for a
    session's current state."""
    from tracker.models import DataLog, ImportSession

    label = _session_label(session)
    Status = ImportSession.Status
    if session.status == Status.SCANNING:
        if session.total_items:
            progress = f" ({session.processed_items:,} of {session.total_items:,} checked)"
        else:
            progress = ""
        return DataLog.Status.RUNNING, f"Scanning {label}{progress}…", False, False
    if session.status == Status.READY:
        count = session.total_items
        return (
            DataLog.Status.WAITING,
            f"{label} is ready to review - {count:,} item{'s' if count != 1 else ''} found.",
            True, False,
        )
    if session.status == Status.IMPORTING:
        finishing = session.source_metadata.get("finishing")
        if finishing:
            progress = f" - finishing up ({finishing.get('done', 0):,} of {finishing.get('total', 0):,})"
        elif session.selected_items:
            progress = f" ({session.processed_items:,} of {session.selected_items:,})"
        else:
            progress = ""
        return DataLog.Status.RUNNING, f"Importing {label}{progress}…", False, False
    if session.status == Status.COMPLETED:
        message = f"Imported {label}: {session.imported_items:,} added"
        skipped = session.skipped_items + session.failed_items
        if skipped:
            message += f", {skipped:,} skipped"
        restored = session.source_metadata.get("backup_lists_restored")
        if restored:
            message += f", {restored['lists']} list{'s' if restored['lists'] != 1 else ''} restored"
        return DataLog.Status.SUCCESS, message + ".", True, True
    if session.status == Status.FAILED:
        return DataLog.Status.FAILED, f"Import of {label} failed: {session.error_message or 'unknown error'}", True, True
    if session.status == Status.EXPIRED:
        return DataLog.Status.CANCELLED, f"Your review of {label} expired before it was imported.", True, True
    return DataLog.Status.CANCELLED, f"Import of {label} was cancelled.", False, True


def _import_log_detail(session):
    parts = [_session_label(session)]
    from tracker.models import ImportSession

    if session.status == ImportSession.Status.COMPLETED:
        skipped = session.skipped_items + session.failed_items
        if skipped:
            parts.append(f"{skipped} skipped")
        restored = session.source_metadata.get("backup_lists_restored")
        if restored:
            parts.append(f"{restored['lists']} lists restored ({restored['items']} titles added)")
    elif session.status == ImportSession.Status.READY:
        parts.append("waiting for review")
    elif session.status == ImportSession.Status.EXPIRED:
        parts.append("review expired")
    return ", ".join(parts)[:255]


def sync_import_session(session, status_changed=True):
    from tracker.models import DataLog, ImportSession, Notification

    log_status, message, notify_unread, finished = _import_state(session)
    now = timezone.now()

    log = DataLog.objects.filter(import_session=session).order_by("id").first()
    log_fields = {
        "status": log_status,
        "detail": _import_log_detail(session),
        "item_count": session.imported_items if session.status == ImportSession.Status.COMPLETED else (session.total_items or None),
        "error_message": session.error_message if log_status == DataLog.Status.FAILED else "",
        "finished_at": now if finished else None,
    }
    if log is None:
        DataLog.objects.create(
            profile=session.profile, action=DataLog.Action.IMPORT, provider=session.source,
            import_session=session, **log_fields,
        )
    elif status_changed or log.detail != log_fields["detail"] or log.item_count != log_fields["item_count"]:
        if log.finished_at and finished:
            log_fields["finished_at"] = log.finished_at  # keep the first finish time
        DataLog.objects.filter(id=log.id).update(**log_fields)

    note = Notification.objects.filter(import_session=session).order_by("id").first()
    if note is None:
        Notification.objects.create(
            profile=session.profile, kind=Notification.Kind.IMPORT_STATUS, import_session=session,
            message=message[:255], link=reverse("import_review", args=[session.id]),
            # The person who just started it is looking at it - only a
            # later change (ready, done, failed) needs their attention.
            read=not notify_unread,
        )
        return
    updates = {"message": message[:255]}
    if status_changed:
        updates["created_at"] = now  # moves to the top of the bell
        updates["read"] = not notify_unread
    if updates["message"] != note.message or status_changed:
        Notification.objects.filter(id=note.id).update(**updates)


def on_import_session_saved(sender, instance, created=False, update_fields=None, raw=False, **kwargs):
    if raw:
        return
    status_changed = created or update_fields is None or "status" in update_fields
    sync_import_session(instance, status_changed=status_changed)


# --------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------


def backup_link():
    return f"{reverse('settings')}?tab=data#backups"


def start_backup(destination, notify=False):
    """Marks a backup as running: a RUNNING DataLog row, the destination's
    in_progress flag, and - for one the user started by hand - a
    "Backing up..." notification the result is written back into.
    Returns (data_log, notification_or_None)."""
    from tracker.models import DataLog, Notification

    destination.in_progress = True
    destination.save(update_fields=["in_progress"])
    log = DataLog.objects.create(
        profile=destination.profile, action=DataLog.Action.BACKUP, provider=destination.kind,
        status=DataLog.Status.RUNNING, detail=f"{destination.get_kind_display()}: backing up",
    )
    note = None
    if notify:
        note = Notification.objects.create(
            profile=destination.profile, kind=Notification.Kind.BACKUP_STATUS, read=True,
            message=f"Backing up to {destination.get_kind_display()}…", link=backup_link(),
        )
    return log, note


def finish_backup(destination, log, ok, message, note=None, previous_error=""):
    """Records a backup's outcome on its DataLog row and notification.
    A scheduled backup only notifies when it starts failing (not on every
    retry of the same failure, and not on routine success)."""
    from tracker.models import DataLog, Notification

    destination.in_progress = False
    destination.save(update_fields=["in_progress"])
    name = destination.get_kind_display()
    DataLog.objects.filter(id=log.id).update(
        status=DataLog.Status.SUCCESS if ok else DataLog.Status.FAILED,
        detail=f"{name}: {message}"[:255] if ok else name,
        error_message="" if ok else message,
        finished_at=timezone.now(),
    )
    text = f"Backed up to {name}." if ok else f"Backup to {name} failed: {message}"
    if note is not None:
        Notification.objects.filter(id=note.id).update(
            message=text[:255], read=False, created_at=timezone.now(),
        )
    elif not ok and not previous_error:
        Notification.objects.create(
            profile=destination.profile, kind=Notification.Kind.BACKUP_STATUS,
            message=text[:255], link=backup_link(),
        )
