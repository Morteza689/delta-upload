from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.utils.decorators import method_decorator
from django.views.generic import ListView

from .models import MICRO_PER_CENT, AdEarning, EarningCarry


@method_decorator(login_required, 'dispatch')
class EarningListView(ListView):
    """Ad revenue: how much is confirmed and how much is still pending."""

    template_name = 'ads_module/earnings.html'
    context_object_name = 'earnings'
    paginate_by = 30

    def get_queryset(self):
        return (
            AdEarning.objects
            .filter(user_id=self.request.user.id)
            .select_related('file')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        mine = AdEarning.objects.filter(user_id=self.request.user.id)

        def total(status):
            return mine.filter(status=status).aggregate(
                micro=Sum('micro_cents'), views=Sum('impressions'),
            )

        pending = total(AdEarning.Status.PENDING)
        confirmed = total(AdEarning.Status.CONFIRMED)
        carry = EarningCarry.objects.filter(user_id=self.request.user.id).first()

        context['pending_micro'] = pending['micro'] or 0
        context['confirmed_micro'] = confirmed['micro'] or 0
        context['total_views'] = (pending['views'] or 0) + (confirmed['views'] or 0)
        context['carry_micro'] = carry.micro_cents if carry else 0
        context['micro_per_cent'] = MICRO_PER_CENT
        return context
