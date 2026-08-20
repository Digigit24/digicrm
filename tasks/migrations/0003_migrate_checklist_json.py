"""Move ``Task.checklist``'s JSON blob into ``TaskChecklistItem`` rows.

The ``checklist`` column is deliberately LEFT IN PLACE and left populated.  This
migration is reversible, and dropping the column in the same change would make
the rollback lossy -- the old data would already be gone by the time anyone
found out they needed it.  A later migration can drop it once the rows have been
in production long enough to trust.

Historic blobs are not uniform (this was an untyped JSONField that several
clients wrote to), so the parser accepts:

    ["Call the lead", "Send brochure"]
    [{"text": "Call", "done": true}, ...]
    [{"title": "Call", "completed": true, "order": 2}, ...]
    {"items": [...]}

Anything unparseable is skipped rather than guessed at; the original JSON is
still sitting in the column either way.
"""
from django.db import migrations

TEXT_KEYS = ('text', 'title', 'label', 'name', 'item', 'value', 'description')
DONE_KEYS = ('is_done', 'done', 'completed', 'checked', 'complete')
ORDER_KEYS = ('order_index', 'order', 'position', 'index', 'sort_order')


def _as_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ('items', 'checklist', 'steps', 'entries'):
            value = raw.get(key)
            if isinstance(value, list):
                return value
        return []
    return []


def _parse_entry(entry, position):
    """Return ``(text, is_done, order_index)`` or ``None`` if unusable."""
    if isinstance(entry, str):
        text = entry.strip()
        return (text, False, position) if text else None

    if not isinstance(entry, dict):
        return None

    text = None
    for key in TEXT_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            break
    if not text:
        return None

    is_done = False
    for key in DONE_KEYS:
        if key in entry:
            is_done = bool(entry[key])
            break

    order_index = position
    for key in ORDER_KEYS:
        value = entry.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            order_index = value
            break

    return (text, is_done, order_index)


def forwards(apps, schema_editor):
    Task = apps.get_model('tasks', 'Task')
    TaskChecklistItem = apps.get_model('tasks', 'TaskChecklistItem')

    queryset = (
        Task.objects
        .exclude(checklist__isnull=True)
        .only('id', 'tenant_id', 'checklist')
        .order_by('id')
    )

    batch = []
    for task in queryset.iterator(chunk_size=500):
        entries = _as_list(task.checklist)
        if not entries:
            continue
        # Idempotent: a re-run (or a partially applied run) must not double up.
        if TaskChecklistItem.objects.filter(task_id=task.id).exists():
            continue
        for position, entry in enumerate(entries):
            parsed = _parse_entry(entry, position)
            if parsed is None:
                continue
            text, is_done, order_index = parsed
            batch.append(TaskChecklistItem(
                tenant_id=task.tenant_id,
                task_id=task.id,
                text=text,
                is_done=is_done,
                order_index=order_index,
                done_at=None,
            ))
        if len(batch) >= 1000:
            TaskChecklistItem.objects.bulk_create(batch, batch_size=1000)
            batch = []
    if batch:
        TaskChecklistItem.objects.bulk_create(batch, batch_size=1000)


def backwards(apps, schema_editor):
    """Fold the rows back into the JSON column, then delete them.

    Rows created after the forward migration (i.e. through the new checklist
    endpoints) are written back in the canonical
    ``[{"text": ..., "is_done": ...}]`` shape, so nothing added since the
    migration is lost on rollback.
    """
    Task = apps.get_model('tasks', 'Task')
    TaskChecklistItem = apps.get_model('tasks', 'TaskChecklistItem')

    task_ids = (
        TaskChecklistItem.objects
        .values_list('task_id', flat=True)
        .distinct()
    )
    for task_id in list(task_ids):
        items = list(
            TaskChecklistItem.objects
            .filter(task_id=task_id)
            .order_by('order_index', 'id')
            .values('text', 'is_done', 'order_index')
        )
        Task.objects.filter(pk=task_id).update(checklist=[
            {
                'text': item['text'],
                'is_done': item['is_done'],
                'order_index': item['order_index'],
            }
            for item in items
        ])
    TaskChecklistItem.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0002_crm_wide_tasks'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
