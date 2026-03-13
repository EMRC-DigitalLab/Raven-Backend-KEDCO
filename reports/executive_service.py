# reports/executive_service.py - NEW FILE
"""
Executive Performance Data Service for fetching executive KPI report data.
"""
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Avg, Count, Q
from django.utils import timezone

from hr.models import ExecutiveKPIDefinition, ExecutivePerformance


class ExecutiveReportDataService:
    """Service for fetching data for executive performance report sections"""
    
    def __init__(self, filters):
        """
        Initialize with filters.
        
        filters = {
            'from_date': '2025-01-01',
            'to_date': '2025-01-31',
            'executive_roles': ['CFO', 'CTO'],  # optional
            'kpi_categories': ['financial', 'technical'],  # optional
        }
        """
        self.filters = filters
        self.from_date = self._parse_date(filters.get('from_date'))
        self.to_date = self._parse_date(filters.get('to_date'))
        
        # Build KPI queryset based on filters
        self.kpi_queryset = self._get_filtered_kpis()
    
    def _parse_date(self, date_val):
        """Parse date from string or return as-is if already a date"""
        if isinstance(date_val, str):
            if 'T' in date_val:
                return datetime.fromisoformat(date_val.replace('Z', '+00:00')).date()
            return datetime.strptime(date_val, '%Y-%m-%d').date()
        elif isinstance(date_val, datetime):
            return date_val.date()
        return date_val
    
    def _get_filtered_kpis(self):
        """Get KPI definitions based on filters"""
        queryset = ExecutiveKPIDefinition.objects.filter(is_active=True)
        
        # Filter by executive roles
        if self.filters.get('executive_roles'):
            queryset = queryset.filter(executive_role__in=self.filters['executive_roles'])
        
        return queryset
    
    def _get_kpi_performance(self, kpi_def):
        """Get performance data for a specific KPI"""
        # Get latest performance record in period
        performance = ExecutivePerformance.objects.filter(
            kpi_definition=kpi_def,
            period_date__range=(self.from_date, self.to_date)
        ).order_by('-period_date').first()
        
        if not performance:
            # Return defaults from KPI definition
            return {
                'current': float(kpi_def.current_value) if kpi_def.current_value else 0,
                'target': float(kpi_def.target_value) if kpi_def.target_value else 0,
                'target_min': float(kpi_def.target_min) if kpi_def.target_min else None,
                'target_max': float(kpi_def.target_max) if kpi_def.target_max else None,
                'is_range': kpi_def.target_min is not None and kpi_def.target_max is not None,
                'unit': kpi_def.unit,
                'status': 'not_started',
                'progress': 0,
            }
        
        # Calculate status and progress
        status_info = {
            'progress': float(performance.progress_percentage),
            'status': performance.status,
        }
        
        return {
            'current': float(performance.actual_value),
            'target': float(kpi_def.target_value) if kpi_def.target_value else 0,
            'target_min': float(kpi_def.target_min) if kpi_def.target_min else None,
            'target_max': float(kpi_def.target_max) if kpi_def.target_max else None,
            'is_range': kpi_def.target_min is not None and kpi_def.target_max is not None,
            'unit': kpi_def.unit,
            'status': status_info['status'],
            'progress': status_info['progress'],
            'last_updated': performance.period_date.isoformat(),
        }
    
    # =========================================================================
    # EXECUTIVE OVERVIEW
    # =========================================================================
    
    def get_executive_overview(self):
        """Get executive performance overview across all roles"""
        total_kpis = self.kpi_queryset.count()
        
        # Get performance for all KPIs
        on_track = 0
        at_risk = 0
        off_track = 0
        not_started = 0
        
        for kpi in self.kpi_queryset:
            perf = self._get_kpi_performance(kpi)
            status = perf['status']
            
            if status in ['on_track', 'on_target', 'exceeding']:
                on_track += 1
            elif status in ['at_risk', 'satisfactory']:
                at_risk += 1
            elif status in ['off_track', 'critical']:
                off_track += 1
            else:
                not_started += 1
        
        # Overall health score (percentage of on-track KPIs)
        health_score = (on_track / total_kpis * 100) if total_kpis > 0 else 0
        
        # KPIs by role
        by_role = {}
        for role in ['CFO', 'CTO', 'CCO', 'CHRO']:
            role_kpis = self.kpi_queryset.filter(executive_role=role)
            role_count = role_kpis.count()
            
            role_on_track = sum(
                1 for kpi in role_kpis 
                if self._get_kpi_performance(kpi)['status'] in ['on_track', 'on_target', 'exceeding']
            )
            
            by_role[role] = {
                'total_kpis': role_count,
                'on_track': role_on_track,
                'health_score': (role_on_track / role_count * 100) if role_count > 0 else 0
            }
        
        return {
            'total_kpis': total_kpis,
            'on_track': on_track,
            'at_risk': at_risk,
            'off_track': off_track,
            'not_started': not_started,
            'health_score': round(health_score, 1),
            'by_role': by_role,
        }
    
    # =========================================================================
    # ROLE-SPECIFIC PERFORMANCE
    # =========================================================================
    
    def get_cfo_performance(self):
        """Get CFO performance metrics"""
        cfo_kpis = self.kpi_queryset.filter(executive_role='CFO')
        
        metrics = []
        for kpi in cfo_kpis:
            perf = self._get_kpi_performance(kpi)
            metrics.append({
                'name': kpi.name,
                'description': kpi.description,
                'current': perf['current'],
                'target': perf['target'],
                'target_min': perf['target_min'],
                'target_max': perf['target_max'],
                'is_range': perf['is_range'],
                'unit': perf['unit'],
                'status': perf['status'],
                'progress': perf['progress'],
                'deadline': kpi.deadline,
                'category': 'Financial Excellence',
            })
        
        return {
            'role': 'Chief Financial Officer',
            'role_code': 'CFO',
            'metrics': metrics,
            'total_kpis': len(metrics),
            'on_track': sum(1 for m in metrics if m['status'] in ['on_track', 'on_target', 'exceeding']),
        }
    
    def get_cto_performance(self):
        """Get CTO performance metrics"""
        cto_kpis = self.kpi_queryset.filter(executive_role='CTO')
        
        metrics = []
        for kpi in cto_kpis:
            perf = self._get_kpi_performance(kpi)
            metrics.append({
                'name': kpi.name,
                'description': kpi.description,
                'current': perf['current'],
                'target': perf['target'],
                'target_min': perf['target_min'],
                'target_max': perf['target_max'],
                'is_range': perf['is_range'],
                'unit': perf['unit'],
                'status': perf['status'],
                'progress': perf['progress'],
                'deadline': kpi.deadline,
                'category': 'Technical Operations',
            })
        
        return {
            'role': 'Chief Technical Officer',
            'role_code': 'CTO',
            'metrics': metrics,
            'total_kpis': len(metrics),
            'on_track': sum(1 for m in metrics if m['status'] in ['on_track', 'on_target', 'exceeding']),
        }
    
    def get_cco_performance(self):
        """Get CCO performance metrics"""
        cco_kpis = self.kpi_queryset.filter(executive_role='CCO')
        
        # Group by categories
        categories = {
            'billing': [],
            'collection': [],
            'band_a': [],
            'revenue': [],
        }
        
        for kpi in cco_kpis:
            perf = self._get_kpi_performance(kpi)
            metric_data = {
                'name': kpi.name,
                'description': kpi.description,
                'current': perf['current'],
                'target': perf['target'],
                'target_min': perf['target_min'],
                'target_max': perf['target_max'],
                'is_range': perf['is_range'],
                'unit': perf['unit'],
                'status': perf['status'],
                'progress': perf['progress'],
                'deadline': kpi.deadline,
            }
            
            # Categorize based on KPI name
            name_lower = kpi.name.lower()
            if 'billing' in name_lower or 'meter' in name_lower:
                categories['billing'].append(metric_data)
            elif 'collection' in name_lower:
                categories['collection'].append(metric_data)
            elif 'band a' in name_lower or 'feeder' in name_lower or 'customer' in name_lower:
                categories['band_a'].append(metric_data)
            else:
                categories['revenue'].append(metric_data)
        
        all_metrics = sum(categories.values(), [])
        
        return {
            'role': 'Chief Commercial Officer',
            'role_code': 'CCO',
            'categories': {
                'Billing Efficiency': categories['billing'],
                'Collection Efficiency': categories['collection'],
                'Band A Growth': categories['band_a'],
                'Revenue Generation': categories['revenue'],
            },
            'total_kpis': len(all_metrics),
            'on_track': sum(1 for m in all_metrics if m['status'] in ['on_track', 'on_target', 'exceeding']),
        }
    
    def get_chro_performance(self):
        """Get CHRO performance metrics"""
        chro_kpis = self.kpi_queryset.filter(executive_role='CHRO')
        
        metrics = []
        for kpi in chro_kpis:
            perf = self._get_kpi_performance(kpi)
            metrics.append({
                'name': kpi.name,
                'description': kpi.description,
                'current': perf['current'],
                'target': perf['target'],
                'target_min': perf['target_min'],
                'target_max': perf['target_max'],
                'is_range': perf['is_range'],
                'unit': perf['unit'],
                'status': perf['status'],
                'progress': perf['progress'],
                'deadline': kpi.deadline,
                'category': 'Human Resources',
            })
        
        return {
            'role': 'Chief Human Resources Officer',
            'role_code': 'CHRO',
            'metrics': metrics,
            'total_kpis': len(metrics),
            'on_track': sum(1 for m in metrics if m['status'] in ['on_track', 'on_target', 'exceeding']),
        }
    
    # =========================================================================
    # EXECUTIVE KPI SUMMARY TABLE
    # =========================================================================
    
    def get_executive_kpi_summary(self):
        """Get summary table of all executive KPIs"""
        summary = []
        
        for kpi in self.kpi_queryset.order_by('executive_role', 'name'):
            perf = self._get_kpi_performance(kpi)
            
            # Format target display
            if perf['is_range']:
                target_display = f"{perf['target_min']}-{perf['target_max']}{perf['unit']}"
            else:
                target_display = f"{perf['target']}{perf['unit']}"
            
            summary.append({
                'executive': kpi.executive_role,
                'kpi_name': kpi.name,
                'current': f"{perf['current']}{perf['unit']}",
                'target': target_display,
                'status': perf['status'],
                'progress': perf['progress'],
                'deadline': kpi.deadline,
            })
        
        return summary
    
    # =========================================================================
    # EXECUTIVE COMPARISON
    # =========================================================================
    
    def get_executive_comparison(self):
        """Compare performance across executives"""
        comparison = []
        
        for role in ['CFO', 'CTO', 'CCO', 'CHRO']:
            role_kpis = self.kpi_queryset.filter(executive_role=role)
            total = role_kpis.count()
            
            if total == 0:
                continue
            
            on_track = sum(
                1 for kpi in role_kpis 
                if self._get_kpi_performance(kpi)['status'] in ['on_track', 'on_target', 'exceeding']
            )
            
            at_risk = sum(
                1 for kpi in role_kpis 
                if self._get_kpi_performance(kpi)['status'] in ['at_risk', 'satisfactory']
            )
            
            off_track = sum(
                1 for kpi in role_kpis 
                if self._get_kpi_performance(kpi)['status'] in ['off_track', 'critical']
            )
            
            comparison.append({
                'role': role,
                'total_kpis': total,
                'on_track': on_track,
                'at_risk': at_risk,
                'off_track': off_track,
                'health_score': round((on_track / total * 100), 1) if total > 0 else 0,
            })
        
        return sorted(comparison, key=lambda x: x['health_score'], reverse=True)
    
    # =========================================================================
    # BOARD KPI STATUS
    # =========================================================================
    
    def get_board_kpi_status(self):
        """Get status of all Board KPIs"""
        # All KPIs are Board KPIs in this context
        return self.get_executive_kpi_summary()
    
    # =========================================================================
    # KPI TRENDS
    # =========================================================================
    
    def get_kpi_trends(self, config=None):
        """Get KPI trends over time"""
        config = config or {}
        selected_kpis = config.get('kpi_ids', [])
        
        trends = []
        
        # If specific KPIs selected, use those; otherwise use all
        kpis = self.kpi_queryset.filter(id__in=selected_kpis) if selected_kpis else self.kpi_queryset[:5]
        
        for kpi in kpis:
            # Get historical performance data
            history = ExecutivePerformance.objects.filter(
                kpi_definition=kpi,
                period_date__range=(self.from_date, self.to_date)
            ).order_by('period_date')
            
            data_points = [
                {
                    'date': perf.period_date.isoformat(),
                    'value': float(perf.actual_value),
                    'progress': float(perf.progress_percentage),
                    'status': perf.status,
                }
                for perf in history
            ]
            
            trends.append({
                'kpi_name': kpi.name,
                'executive': kpi.executive_role,
                'unit': kpi.unit,
                'target': float(kpi.target_value) if kpi.target_value else 0,
                'data_points': data_points,
            })
        
        return trends
    
    # =========================================================================
    # MASTER DATA GETTER
    # =========================================================================
    
    def get_all_section_data(self, section_type, config=None):
        """Get data for a specific executive section type"""
        config = config or {}
        
        data_methods = {
            'executive_overview': self.get_executive_overview,
            'cfo_performance': self.get_cfo_performance,
            'cto_performance': self.get_cto_performance,
            'cco_performance': self.get_cco_performance,
            'chro_performance': self.get_chro_performance,
            'executive_kpi_summary': self.get_executive_kpi_summary,
            'executive_comparison': self.get_executive_comparison,
            'board_kpi_status': self.get_board_kpi_status,
            'kpi_trends': lambda: self.get_kpi_trends(config),
        }
        
        method = data_methods.get(section_type)
        if method:
            return method()
        
        return {}