from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commercial', '0006_commercialcustomer_is_bypass'),
    ]

    operations = [
        # ── GIS fields ────────────────────────────────────────────────────────
        migrations.AddField(
            model_name='meterreading',
            name='gis_id',
            field=models.CharField(
                blank=True, max_length=20,
                help_text='GIS point ID linked to this reading location',
            ),
        ),
        migrations.AddField(
            model_name='meterreading',
            name='gis_match',
            field=models.BooleanField(
                null=True, blank=True,
                help_text='True if GPS matches GIS reference point, False if not, None if unchecked',
            ),
        ),

        # ── OCR fields ────────────────────────────────────────────────────────
        migrations.AddField(
            model_name='meterreading',
            name='ocr_status',
            field=models.CharField(
                max_length=10, default='pending',
                choices=[
                    ('pending',  'Pending'),
                    ('matched',  'Matched — OCR agrees with submitted reading'),
                    ('mismatch', 'Mismatch — OCR value differs from submission'),
                    ('failed',   'Failed — OCR could not process image'),
                    ('skipped',  'Skipped — no image submitted'),
                ],
                help_text='Result of OCR check against the proof image',
            ),
        ),
        migrations.AddField(
            model_name='meterreading',
            name='ocr_extracted_value',
            field=models.DecimalField(
                max_digits=12, decimal_places=2, null=True, blank=True,
                help_text='Meter value read by OCR from the proof photo',
            ),
        ),
        migrations.AddField(
            model_name='meterreading',
            name='ocr_confidence',
            field=models.DecimalField(
                max_digits=5, decimal_places=2, null=True, blank=True,
                help_text='OCR confidence score (0–1)',
            ),
        ),

        # ── Audit fields ──────────────────────────────────────────────────────
        migrations.AddField(
            model_name='meterreading',
            name='audit_status',
            field=models.CharField(
                max_length=10, null=True, blank=True,
                choices=[
                    ('pending',  'Pending Review'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                ],
                help_text='Supervisor audit decision on this reading',
            ),
        ),
        migrations.AddField(
            model_name='meterreading',
            name='audited_by',
            field=models.CharField(
                max_length=36, blank=True,
                help_text='User ID of the auditor',
            ),
        ),
        migrations.AddField(
            model_name='meterreading',
            name='audit_note',
            field=models.TextField(
                blank=True,
                help_text="Auditor's note or rejection reason",
            ),
        ),
        migrations.AddField(
            model_name='meterreading',
            name='audited_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='When the audit decision was made',
            ),
        ),

        # ── Indexes ───────────────────────────────────────────────────────────
        migrations.AddIndex(
            model_name='meterreading',
            index=models.Index(fields=['ocr_status'], name='commercial_mr_ocr_status_idx'),
        ),
        migrations.AddIndex(
            model_name='meterreading',
            index=models.Index(fields=['audit_status'], name='commercial_mr_audit_status_idx'),
        ),
    ]
