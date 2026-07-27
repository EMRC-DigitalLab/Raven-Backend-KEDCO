from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tmo', '0009_googlesheetfeed_feed_type_expand'),
    ]

    operations = [
        migrations.CreateModel(
            name='SheetAlertEmail',
            fields=[
                ('id',         models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email',      models.EmailField(unique=True)),
                ('name',       models.CharField(blank=True, max_length=100)),
                ('is_active',  models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['email'],
            },
        ),
    ]
