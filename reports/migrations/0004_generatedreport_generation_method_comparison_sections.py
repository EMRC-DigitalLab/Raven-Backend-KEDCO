from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0003_alter_generatedreport_category_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='generatedreport',
            name='generation_method',
            field=models.CharField(
                blank=True,
                choices=[('pdf', 'PDF (server-side)'), ('data', 'Data (client-side)')],
                default='pdf',
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='reportsection',
            name='section_type',
            field=models.CharField(
                choices=[
                    ('cover_page', 'Cover Page'),
                    ('table_of_contents', 'Table of Contents'),
                    ('infrastructure_overview', 'Infrastructure Overview'),
                    ('technical_metrics', 'Technical Metrics Cards'),
                    ('system_reliability', 'System Reliability'),
                    ('interruption_breakdown', 'Interruption Breakdown Table'),
                    ('hours_of_supply_chart', 'Hours of Supply Chart'),
                    ('load_trend_chart', 'Load Trend Chart'),
                    ('energy_delivered_chart', 'Energy Delivered Chart'),
                    ('feeder_performance_table', 'Feeder Performance Table'),
                    ('state_performance_table', 'State Performance Table'),
                    ('district_performance_table', 'District Performance Table'),
                    ('service_band_summary', 'Service Band Summary'),
                    ('hr_overview', 'HR Overview'),
                    ('staff_metrics', 'Staff Metrics Cards'),
                    ('department_headcount', 'Headcount by Department'),
                    ('staff_productivity', 'Staff Productivity Metrics'),
                    ('wage_bill_analysis', 'Wage Bill Analysis'),
                    ('attrition_analysis', 'Attrition Analysis'),
                    ('recruitment_summary', 'Recruitment Summary'),
                    ('training_summary', 'Training & Development Summary'),
                    ('performance_appraisals', 'Performance Appraisals Summary'),
                    ('executive_overview', 'Executive Performance Overview'),
                    ('cfo_performance', 'CFO Performance Metrics'),
                    ('cto_performance', 'CTO Performance Metrics'),
                    ('cco_performance', 'CCO Performance Metrics'),
                    ('chro_performance', 'CHRO Performance Metrics'),
                    ('executive_kpi_summary', 'Executive KPI Summary Table'),
                    ('executive_comparison', 'Executive Performance Comparison'),
                    ('board_kpi_status', 'Board KPI Status'),
                    ('kpi_trends', 'KPI Trends Over Time'),
                    ('dso_compliance_overview', 'DSO Compliance Overview'),
                    ('dso_compliance_table', 'DSO Compliance by Station'),
                    ('custom_text', 'Custom Text/Notes'),
                    ('gaps_improvements', 'Gaps and Improvement Areas'),
                    ('commercial_summary', 'Commercial Summary'),
                    ('financial_summary', 'Financial Summary'),
                    ('collection_efficiency', 'Collection Efficiency'),
                    ('entity_comparison', 'Entity Comparison'),
                    ('period_comparison', 'Period Comparison'),
                    ('customer_comparison', 'Customer Comparison'),
                ],
                max_length=50,
            ),
        ),
    ]
