"""Per-file reporting.

Gathers downloads, revenue and rejected passes in one place so the view
stays a view.
"""
from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from ads_module.models import MICRO_PER_CENT, AdEarning, DownloadSession

from .models import FileDownload, Purchase

DEFAULT_DAYS = 30


def file_report(upload_file, days=DEFAULT_DAYS):
    since = timezone.now() - timedelta(days=days)

    downloads = FileDownload.objects.filter(file=upload_file)
    by_kind = {
        row['kind']: row
        for row in downloads.values('kind').annotate(
            count=Count('id'), bytes=Sum('bytes_sent'),
        )
    }

    def kind_stat(kind):
        row = by_kind.get(kind, {})
        return {'count': row.get('count', 0), 'bytes': row.get('bytes') or 0}

    free = kind_stat(FileDownload.Kind.FREE)
    paid = kind_stat(FileDownload.Kind.PAID)
    owner = kind_stat(FileDownload.Kind.OWNER)

    # Ad revenue.
    earnings = AdEarning.objects.filter(file=upload_file)
    ad_totals = {
        row['status']: row['micro']
        for row in earnings.values('status').annotate(micro=Sum('micro_cents'))
    }
    ad_confirmed = ad_totals.get(AdEarning.Status.CONFIRMED, 0) or 0
    ad_pending = ad_totals.get(AdEarning.Status.PENDING, 0) or 0
    ad_rejected = ad_totals.get(AdEarning.Status.REJECTED, 0) or 0

    # Sales revenue.
    sales = Purchase.objects.filter(file=upload_file).aggregate(
        count=Count('id'), gross=Sum('amount'), share=Sum('seller_share'),
    )

    # Rejected sessions by reason, to show what the anti-fraud rules caught and
    # how often.
    rejections = list(
        DownloadSession.objects
        .filter(file=upload_file, counted=False)
        .exclude(reject_reason='')
        .values('reject_reason')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    sessions = DownloadSession.objects.filter(file=upload_file)

    # Daily trend for the chart.
    daily = list(
        downloads.filter(created_at__gte=since)
        .exclude(kind=FileDownload.Kind.OWNER)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )
    peak = max((row['count'] for row in daily), default=0)
    for row in daily:
        row['percent'] = round(row['count'] * 100 / peak) if peak else 0

    public_downloads = free['count'] + paid['count']

    return {
        'file': upload_file,
        'days': days,

        'total_downloads': public_downloads,
        'free': free,
        'paid': paid,
        'owner': owner,
        'bytes_total': free['bytes'] + paid['bytes'],
        'unique_visitors': (
            downloads.exclude(visitor_hash='')
            .values('visitor_hash').distinct().count()
        ),

        'ad_confirmed_micro': ad_confirmed,
        'ad_pending_micro': ad_pending,
        'ad_rejected_micro': ad_rejected,
        'ad_impressions': earnings.aggregate(n=Sum('impressions'))['n'] or 0,

        'sales_count': sales['count'] or 0,
        'sales_gross': sales['gross'] or 0,
        'sales_share': sales['share'] or 0,

        # Total revenue for this file in cents: sales share plus confirmed ad income.
        'total_earned_cents': (sales['share'] or 0) + ad_confirmed // MICRO_PER_CENT,

        'sessions_total': sessions.count(),
        'sessions_counted': sessions.filter(counted=True).count(),
        'rejections': rejections,
        'daily': daily,
    }
