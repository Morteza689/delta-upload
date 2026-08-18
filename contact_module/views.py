"""Support tickets and contact messages."""
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import ListView
from django.views.generic.edit import CreateView
from django_ratelimit.decorators import ratelimit

from upload_module import downloads

from .forms import ContactUsModelForm, TicketForm, TicketReplyForm
from .models import Ticket, TicketMessage


@method_decorator(login_required, 'dispatch')
class TicketListView(ListView):
    template_name = 'contact_module/ticket_list.html'
    context_object_name = 'tickets'
    paginate_by = 20

    def get_queryset(self):
        queryset = (
            Ticket.objects
            .filter(user_id=self.request.user.id)
            .annotate(message_count=Count('messages'))
        )
        status = self.request.GET.get('status')
        if status in Ticket.Status.values:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        mine = Ticket.objects.filter(user_id=self.request.user.id)
        context['counts'] = {
            'all': mine.count(),
            'open': mine.filter(status=Ticket.Status.OPEN).count(),
            'answered': mine.filter(status=Ticket.Status.ANSWERED).count(),
            'closed': mine.filter(status=Ticket.Status.CLOSED).count(),
        }
        context['active_status'] = self.request.GET.get('status', '')
        return context


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='10/h', method='POST'), 'dispatch')
class TicketCreateView(View):
    template_name = 'contact_module/ticket_new.html'

    def get(self, request):
        return render(request, self.template_name, {'form': TicketForm()})

    def post(self, request):
        form = TicketForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        # The ticket and its first message are created together, or a ticket with
        # no message can end up sitting in the queue.
        with transaction.atomic():
            ticket = form.save(commit=False)
            ticket.user = request.user
            ticket.save()

            TicketMessage.objects.create(
                ticket=ticket,
                author=request.user,
                body=form.cleaned_data['body'],
                attachment=form.cleaned_data.get('attachment'),
                is_staff_reply=False,
            )

        return redirect(reverse('ticket_detail', args=[ticket.pk]))


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='40/h', method='POST'), 'dispatch')
class TicketDetailView(View):
    template_name = 'contact_module/ticket_detail.html'

    def get_ticket(self, request, pk):
        """The user's own ticket, or any ticket for staff."""
        queryset = Ticket.objects.select_related('user')
        if not request.user.is_staff:
            queryset = queryset.filter(user_id=request.user.id)
        return get_object_or_404(queryset, pk=pk)

    def render_ticket(self, request, ticket, form=None):
        return render(request, self.template_name, {
            'ticket': ticket,
            'messages_list': ticket.messages.select_related('author'),
            'form': form or TicketReplyForm(),
            'is_staff_view': request.user.is_staff and ticket.user_id != request.user.id,
        })

    def get(self, request, pk):
        return self.render_ticket(request, self.get_ticket(request, pk))

    def post(self, request, pk):
        ticket = self.get_ticket(request, pk)
        action = request.POST.get('action')

        if action == 'close':
            ticket.touch(Ticket.Status.CLOSED)
            return redirect(reverse('ticket_detail', args=[ticket.pk]))

        if action == 'reopen':
            if not request.user.is_staff and ticket.status != Ticket.Status.CLOSED:
                raise Http404
            ticket.touch(Ticket.Status.OPEN)
            return redirect(reverse('ticket_detail', args=[ticket.pk]))

        if ticket.status == Ticket.Status.CLOSED:
            # A closed ticket cannot be quietly continued; it must be reopened first.
            return self.render_ticket(request, ticket)

        form = TicketReplyForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_ticket(request, ticket, form)

        is_staff_reply = request.user.is_staff and ticket.user_id != request.user.id

        with transaction.atomic():
            message = form.save(commit=False)
            message.ticket = ticket
            message.author = request.user
            message.is_staff_reply = is_staff_reply
            message.save()

            # Status says whose turn it is: a staff reply hands it to the user, a user
            # message puts it back in the queue.
            ticket.touch(
                Ticket.Status.ANSWERED if is_staff_reply else Ticket.Status.OPEN
            )

        return redirect(reverse('ticket_detail', args=[ticket.pk]))


@login_required
def ticket_attachment(request, pk):
    """A ticket attachment, for its owner and staff only."""
    message = get_object_or_404(
        TicketMessage.objects.select_related('ticket'), pk=pk,
    )
    if not request.user.is_staff and message.ticket.user_id != request.user.id:
        raise Http404
    if not message.attachment:
        raise Http404

    # Reuses the file serving layer built for uploads: attachments must not be
    # reachable through the public media path either.
    class _Wrapper:
        file = message.attachment
    return downloads.serve(_Wrapper())


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='5/h', method='POST'), 'dispatch')
class ContactUsView(CreateView):
    template_name = 'contact_module/contact_us_page.html'
    form_class = ContactUsModelForm
    success_url = '/support/contact-us/'
