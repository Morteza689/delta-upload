"""Chart and progress-bar upload tests."""
import json
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from upload_module.models import UploadFile
from utils import charts

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()


class TimeseriesTests(TestCase):
    def test_empty_days_are_filled_in(self):
        """Dropping empty days makes the slope of the line lie."""
        today = timezone.localdate()
        rows = [{'day': today, 'count': 5}]

        chart = charts.timeseries(rows, days=7)

        self.assertEqual(len(chart['points']), 7)
        self.assertEqual(chart['points'][-1]['count'], 5)
        self.assertEqual(chart['points'][0]['count'], 0)

    def test_total_counts_every_day(self):
        today = timezone.localdate()
        rows = [
            {'day': today, 'count': 3},
            {'day': today - timedelta(days=2), 'count': 4},
        ]
        chart = charts.timeseries(rows, days=7)
        self.assertEqual(chart['total'], 7)

    def test_no_data_is_reported(self):
        chart = charts.timeseries([], days=30)
        self.assertFalse(chart['has_data'])
        self.assertEqual(chart['total'], 0)

    def test_points_stay_inside_the_viewbox(self):
        today = timezone.localdate()
        rows = [{'day': today - timedelta(days=i), 'count': i * 3} for i in range(10)]
        chart = charts.timeseries(rows, days=10, width=600, height=160)

        for point in chart['points']:
            self.assertGreaterEqual(point['x'], 0)
            self.assertLessEqual(point['x'], 600)
            self.assertGreaterEqual(point['y'], 0)
            self.assertLessEqual(point['y'], 160)

    def test_a_datetime_day_is_accepted(self):
        """TruncDate returns a datetime rather than a date on some backends."""
        rows = [{'day': timezone.now(), 'count': 2}]
        chart = charts.timeseries(rows, days=3)
        self.assertEqual(chart['total'], 2)

    def test_the_line_path_is_well_formed(self):
        rows = [{'day': timezone.localdate(), 'count': 1}]
        chart = charts.timeseries(rows, days=5)
        self.assertTrue(chart['line'].startswith('M'))
        self.assertIn('L', chart['line'])

    def test_only_a_few_date_labels_are_shown(self):
        """Thirty date labels overlap on a phone."""
        chart = charts.timeseries([], days=30)
        shown = [p for p in chart['points'] if p['show_label']]
        self.assertLessEqual(len(shown), 8)


class RingTests(TestCase):
    def test_percentage_is_computed(self):
        ring = charts.ring(25, 100)
        self.assertEqual(ring['percent'], 25)

    def test_zero_total_does_not_divide_by_zero(self):
        ring = charts.ring(0, 0)
        self.assertEqual(ring['percent'], 0)
        self.assertEqual(ring['dash'], 0)

    def test_full_ring_uses_the_whole_circumference(self):
        ring = charts.ring(10, 10)
        self.assertEqual(ring['percent'], 100)
        self.assertAlmostEqual(ring['dash'], ring['circumference'], places=1)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class DashboardChartTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username='dash', email='dash@example.com', password='pw',
            email_active_code='d' * 10, is_active=True,
        )
        self.client.force_login(self.user)

    def test_dashboard_renders_with_no_data(self):
        """A new user's dashboard must not error or show a meaningless empty chart."""
        response = self.client.get(reverse('user_dashbord'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'هنوز دانلودی ثبت نشده')

    def test_dashboard_shows_the_chart_when_there_is_data(self):
        from upload_module.models import FileDownload

        upload = UploadFile.objects.create(
            name='demo', author=self.user, slug='dashdemo',
            file=SimpleUploadedFile('demo.bin', b'x'),
        )
        FileDownload.objects.create(file=upload, kind=FileDownload.Kind.FREE)

        response = self.client.get(reverse('user_dashbord'))
        self.assertContains(response, 'chart-line')
        self.assertNotContains(response, 'هنوز دانلودی ثبت نشده')

    def test_dashboard_query_count_does_not_grow_with_the_number_of_files(self):
        """Totals must come from aggregates, not a loop over rows.

        A user with a hundred files should not load a hundred objects to
        produce one number.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def dashboard_queries():
            with CaptureQueriesContext(connection) as captured:
                self.client.get(reverse('user_dashbord'))
            return len(captured)

        UploadFile.objects.create(
            name='one', author=self.user, slug='one',
            file=SimpleUploadedFile('one.bin', b'x'),
        )
        # Warm anything cached per process first. The claim under test is that
        # the count does not grow with the number of files, not that a cold
        # first request costs the same as a warm one.
        dashboard_queries()
        with_one = dashboard_queries()

        for index in range(30):
            UploadFile.objects.create(
                name=f'f{index}', author=self.user, slug=f'slug{index}',
                file=SimpleUploadedFile(f'f{index}.bin', b'x'),
            )
        with_many = dashboard_queries()

        self.assertEqual(with_one, with_many)


@override_settings(MEDIA_ROOT=MEDIA_ROOT, MAX_UPLOAD_SIZE_BYTES=10 * 1024 * 1024)
class AjaxUploadTests(TestCase):
    """Progress-bar uploads come over XHR, so the responses must be machine
    readable.
    """

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username='up', email='up@example.com', password='pw',
            email_active_code='u' * 10, is_active=True,
        )
        self.client.force_login(self.user)

    def post(self, ajax=True, **extra):
        data = {
            'name': 'demo', 'description': '', 'visibility': 'private', 'price': 0,
            'file': SimpleUploadedFile('demo.bin', b'hello'),
        }
        data.update(extra)
        headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
        return self.client.post(reverse('upload_page'), data, **headers)

    def test_ajax_upload_returns_json_with_a_redirect(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)

        payload = json.loads(response.content)
        self.assertEqual(payload['status'], 'success')
        self.assertIn('redirect', payload)
        self.assertTrue(UploadFile.objects.filter(slug=payload['slug']).exists())

    def test_ajax_validation_error_returns_422_with_the_form(self):
        """A 422 tells the script this is a validation error, not a server fault."""
        response = self.post(visibility='private', price='500')

        self.assertEqual(response.status_code, 422)
        self.assertIn(b'form', response.content.lower())
        self.assertFalse(UploadFile.objects.exists())

    def test_a_plain_form_post_still_redirects(self):
        """Upload must work without JavaScript too."""
        response = self.post(ajax=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(UploadFile.objects.exists())

    def test_the_upload_page_carries_the_progress_markup(self):
        html = self.client.get(reverse('upload_page')).content.decode()
        self.assertIn('data-upload-form', html)
        self.assertIn('data-upload-progress', html)
        self.assertIn('data-upload-percent', html)
        self.assertIn('progress-fill', html)

    def test_the_progress_box_starts_hidden(self):
        """An empty bar should not be visible before the upload starts."""
        html = self.client.get(reverse('upload_page')).content.decode()
        self.assertRegex(html, r'data-upload-progress\s+hidden')

    def test_the_upload_script_is_loaded(self):
        html = self.client.get(reverse('upload_page')).content.decode()
        self.assertIn('src/upload.js', html)
