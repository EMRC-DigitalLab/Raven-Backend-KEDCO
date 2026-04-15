from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commercial', '0005_add_meter_manager_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='commercialcustomer',
            name='is_bypass',
            field=models.BooleanField(
                default=False,
                help_text='True if this customer has been flagged for meter bypass / tampering in DataNest',
            ),
        ),
        migrations.AddIndex(
            model_name='commercialcustomer',
            index=models.Index(fields=['customer_type'], name='commercial_customer_type_idx'),
        ),
        migrations.AddIndex(
            model_name='commercialcustomer',
            index=models.Index(fields=['is_bypass'], name='commercial_is_bypass_idx'),
        ),
    ]
