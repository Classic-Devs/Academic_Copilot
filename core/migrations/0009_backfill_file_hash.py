import hashlib

from django.db import migrations


def backfill_file_hash(apps, schema_editor):
    AcademicDocument = apps.get_model('core', 'AcademicDocument')
    for doc in AcademicDocument.objects.filter(file_hash='').iterator():
        try:
            digest = hashlib.sha256()
            with doc.file.open('rb') as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    digest.update(block)
        except Exception:
            continue  # missing file: leave the hash empty
        AcademicDocument.objects.filter(pk=doc.pk).update(file_hash=digest.hexdigest())


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_hardening_and_dedup'),
    ]

    operations = [
        migrations.RunPython(backfill_file_hash, migrations.RunPython.noop),
    ]
