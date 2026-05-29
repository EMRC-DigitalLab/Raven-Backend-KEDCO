from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0004_generatedreport_generation_method_comparison_sections'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReportInsightsCache',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cache_key', models.CharField(db_index=True, max_length=64, unique=True)),
                ('insights', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
