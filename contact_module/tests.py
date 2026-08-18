"""Ticket system tests.

Mostly isolation: one user's ticket must never reach another.
"""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Ticket, TicketMessage

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class TicketTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def make_user(self, name, staff=False):
        return User.objects.create_user(
            username=name, email=f'{name}@example.com', password='pw',
            email_active_code=name * 3, is_active=True, is_staff=staff,
        )

    def setUp(self):
        self.user = self.make_user('owner')
        self.other = self.make_user('other')
        self.staff = self.make_user('agent', staff=True)
        self.client.force_login(self.user)

    def create_ticket(self, subject='مشکل دانلود'):
        self.client.post(reverse('ticket_new'), {
            'subject': subject, 'department': Ticket.Department.TECHNICAL,
            'priority': Ticket.Priority.NORMAL, 'body': 'توضیح مشکل',
        })
        return Ticket.objects.latest('pk')


class TicketCreationTests(TicketTestCase):
    def test_creating_a_ticket_also_creates_the_first_message(self):
        """A ticket with no message must not sit in the queue."""
        ticket = self.create_ticket()
        self.assertEqual(ticket.messages.count(), 1)
        self.assertEqual(ticket.messages.first().body, 'توضیح مشکل')
        self.assertFalse(ticket.messages.first().is_staff_reply)

    def test_a_new_ticket_waits_for_staff(self):
        ticket = self.create_ticket()
        self.assertEqual(ticket.status, Ticket.Status.OPEN)
        self.assertTrue(ticket.waiting_for_staff)

    def test_the_ticket_belongs_to_the_creator(self):
        ticket = self.create_ticket()
        self.assertEqual(ticket.user, self.user)

    def test_creating_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('ticket_new'))
        self.assertEqual(response.status_code, 302)

    def test_an_invalid_form_creates_nothing(self):
        self.client.post(reverse('ticket_new'), {'subject': '', 'body': ''})
        self.assertFalse(Ticket.objects.exists())


class TicketIsolationTests(TicketTestCase):
    """One user's ticket must never reach another."""

    def test_another_user_cannot_open_the_ticket(self):
        ticket = self.create_ticket()
        self.client.force_login(self.other)
        response = self.client.get(reverse('ticket_detail', args=[ticket.pk]))
        self.assertEqual(response.status_code, 404)

    def test_another_user_cannot_reply(self):
        ticket = self.create_ticket()
        self.client.force_login(self.other)
        self.client.post(reverse('ticket_detail', args=[ticket.pk]),
                         {'body': 'پیام نفوذی'})
        self.assertEqual(ticket.messages.count(), 1)

    def test_the_list_only_shows_own_tickets(self):
        self.create_ticket('مال من')
        self.client.force_login(self.other)
        response = self.client.get(reverse('ticket_list'))
        self.assertNotContains(response, 'مال من')

    def test_staff_can_open_any_ticket(self):
        ticket = self.create_ticket()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('ticket_detail', args=[ticket.pk]))
        self.assertEqual(response.status_code, 200)


class TicketConversationTests(TicketTestCase):
    def test_a_staff_reply_marks_the_ticket_answered(self):
        ticket = self.create_ticket()
        self.client.force_login(self.staff)
        self.client.post(reverse('ticket_detail', args=[ticket.pk]),
                         {'body': 'پاسخ پشتیبانی'})

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.ANSWERED)
        self.assertTrue(ticket.messages.last().is_staff_reply)

    def test_a_user_reply_puts_it_back_in_the_queue(self):
        ticket = self.create_ticket()
        self.client.force_login(self.staff)
        self.client.post(reverse('ticket_detail', args=[ticket.pk]), {'body': 'پاسخ'})

        self.client.force_login(self.user)
        self.client.post(reverse('ticket_detail', args=[ticket.pk]), {'body': 'باز هم مشکل دارم'})

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.OPEN)
        self.assertFalse(ticket.messages.last().is_staff_reply)

    def test_a_users_own_reply_is_never_a_staff_reply(self):
        """Even a staff member's own ticket message is not a support reply."""
        self.client.force_login(self.staff)
        self.client.post(reverse('ticket_new'), {
            'subject': 'تیکت خود کارمند', 'department': Ticket.Department.OTHER,
            'priority': Ticket.Priority.LOW, 'body': 'متن',
        })
        ticket = Ticket.objects.latest('pk')
        self.assertFalse(ticket.messages.first().is_staff_reply)

    def test_closing_a_ticket(self):
        ticket = self.create_ticket()
        self.client.post(reverse('ticket_detail', args=[ticket.pk]), {'action': 'close'})

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.CLOSED)
        self.assertIsNotNone(ticket.closed_at)
        self.assertFalse(ticket.is_open)

    def test_a_closed_ticket_accepts_no_new_messages(self):
        ticket = self.create_ticket()
        self.client.post(reverse('ticket_detail', args=[ticket.pk]), {'action': 'close'})
        self.client.post(reverse('ticket_detail', args=[ticket.pk]), {'body': 'پیام بعد از بستن'})

        self.assertEqual(ticket.messages.count(), 1)

    def test_reopening_a_closed_ticket(self):
        ticket = self.create_ticket()
        self.client.post(reverse('ticket_detail', args=[ticket.pk]), {'action': 'close'})
        self.client.post(reverse('ticket_detail', args=[ticket.pk]), {'action': 'reopen'})

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.OPEN)

    def test_replying_bumps_the_ticket_in_the_queue(self):
        ticket = self.create_ticket()
        first = ticket.last_message_at

        self.client.force_login(self.staff)
        self.client.post(reverse('ticket_detail', args=[ticket.pk]), {'body': 'پاسخ'})

        ticket.refresh_from_db()
        self.assertGreater(ticket.last_message_at, first)


class TicketAttachmentTests(TicketTestCase):
    def attach(self):
        self.client.post(reverse('ticket_new'), {
            'subject': 'با پیوست', 'department': Ticket.Department.TECHNICAL,
            'priority': Ticket.Priority.NORMAL, 'body': 'متن',
            'attachment': SimpleUploadedFile('shot.png', b'image-bytes'),
        })
        return TicketMessage.objects.latest('pk')

    def test_the_attachment_is_saved(self):
        message = self.attach()
        self.assertTrue(message.attachment)

    def test_the_owner_can_download_the_attachment(self):
        message = self.attach()
        response = self.client.get(reverse('ticket_attachment', args=[message.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'image-bytes')

    def test_another_user_cannot_download_the_attachment(self):
        message = self.attach()
        self.client.force_login(self.other)
        response = self.client.get(reverse('ticket_attachment', args=[message.pk]))
        self.assertEqual(response.status_code, 404)

    def test_staff_can_download_the_attachment(self):
        message = self.attach()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('ticket_attachment', args=[message.pk]))
        self.assertEqual(response.status_code, 200)

    def test_an_oversized_attachment_is_refused(self):
        response = self.client.post(reverse('ticket_new'), {
            'subject': 'بزرگ', 'department': Ticket.Department.TECHNICAL,
            'priority': Ticket.Priority.NORMAL, 'body': 'متن',
            'attachment': SimpleUploadedFile('big.bin', b'x' * (9 * 1024 * 1024)),
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ticket.objects.exists())


class TicketListTests(TicketTestCase):
    def test_status_filter(self):
        open_ticket = self.create_ticket('باز')
        closed = self.create_ticket('بسته')
        self.client.post(reverse('ticket_detail', args=[closed.pk]), {'action': 'close'})

        response = self.client.get(reverse('ticket_list'), {'status': 'closed'})
        self.assertContains(response, 'بسته')
        self.assertNotContains(response, 'باز</span>')

    def test_counts_are_shown(self):
        self.create_ticket()
        response = self.client.get(reverse('ticket_list'))
        self.assertEqual(response.context['counts']['all'], 1)
        self.assertEqual(response.context['counts']['open'], 1)
