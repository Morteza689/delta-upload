from django.utils import timezone
from utils.http_service import get_client_ip
from django.shortcuts import render ,redirect ,reverse
from django.http import Http404
from django.views import View
from . import forms
from .models import User
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
# Create your views here.

# AccountView was removed: it showed only the name and email, which the
# dashboard already covers. /account/ now redirects there permanently.

# Password reset: limits both mail flooding and bulk probing for which
# addresses are registered.
def send_reset_email(user):
    """Issue a reset token and mail the link.

    Issuing invalidates any earlier token for this user, so a second request
    quietly retires the first link rather than leaving several live at once.
    """
    from django.utils.translation import gettext_lazy

    from utils.email_service import absolute_url, send_email

    from .language import language_for
    from .tokens import LIFETIME, PasswordResetToken

    raw = PasswordResetToken.issue(user)
    url = absolute_url(reverse('reset_password_page', args=[raw]))

    # Lazy, so the subject is translated inside the language override rather
    # than in whatever language happens to be active here.
    return send_email(
        gettext_lazy('بازیابی رمز عبور در دلتا آپلود'), user.email,
        {
            'user': user,
            'reset_url': url,
            'valid_hours': int(LIFETIME.total_seconds() // 3600),
        },
        'emails/reset_password.html',
        language=language_for(user),
    )


@method_decorator(ratelimit(key='ip', rate='5/h', method='POST'), 'dispatch')
class ForgatPasswordView(View):
    def get(self,request):
        forgat_password_form = forms.ForgetPasswordForm(remote_ip=get_client_ip(request))
        contaxt={
            'forgat_password_form':forgat_password_form
        }
        return render(request,'account_module/forgat_password.html',contaxt)

    def post(self, request):
        forgat_password_form = forms.ForgetPasswordForm(
            request.POST, remote_ip=get_client_ip(request))
        if forgat_password_form.is_valid():
            user_email = forgat_password_form.cleaned_data.get('email')
            user = User.objects.filter(email__iexact=user_email).first()

            if user is not None:
                send_reset_email(user)

            # The same response either way. Reporting an unknown address would
            # turn this form into an account enumeration tool.
            return render(request, 'account_module/forgat_password.html', {
                'forgat_password_form': forms.ForgetPasswordForm(
                    remote_ip=get_client_ip(request)),
                'sent': True,
            })

        return render(request, 'account_module/forgat_password.html', {
            'forgat_password_form': forgat_password_form,
        })




@method_decorator(ratelimit(key='ip', rate='10/h', method='POST'), 'dispatch')
class RestPasswordView(View):
    def get(self, request, active_code):
        # Validity only. Consumption happens on POST so browser prefetch or an
        # email scanner cannot burn the link.
        from .tokens import PasswordResetToken, hash_token

        token = PasswordResetToken.objects.filter(
            token_hash=hash_token(active_code)).first()
        if token is None or not token.is_valid():
            return redirect(reverse('login_page'))

        return render(request, 'account_module/reset_password.html', context={
            'reset_password_form': forms.ResetPasswordForm(),
            'user': token.user,
        })

    def post(self, request, active_code):
        from .tokens import PasswordResetToken, hash_token

        reset_password_form = forms.ResetPasswordForm(request.POST)
        if not reset_password_form.is_valid():
            token = PasswordResetToken.objects.filter(
                token_hash=hash_token(active_code)).first()
            return render(request, 'account_module/reset_password.html', context={
                'reset_password_form': reset_password_form,
                'user': token.user if token else None,
            })

        # Consumed here, not on GET: the token is single use and replaying
        # this request must not change the password again.
        user = PasswordResetToken.consume(active_code)
        if user is None:
            return redirect(reverse('login_page'))

        user.set_password(reset_password_form.cleaned_data.get('password'))
        user.is_active = True
        user.save(update_fields=['password', 'is_active'])

        # Kill open sessions: if an attacker knew the old password and was
        # logged in, changing it must throw them out.
        from django.contrib.sessions.models import Session
        for session in Session.objects.filter(expire_date__gte=timezone.now()):
            if str(session.get_decoded().get('_auth_user_id')) == str(user.pk):
                session.delete()

        return redirect(reverse('login_page'))




# The activation code is 72 characters, but the limit still stops bulk guessing.
@method_decorator(ratelimit(key='ip', rate='20/h'), 'dispatch')
class ActiveAccountView(View):
    def get(self, request, active_code):
        # Exact match, not iexact: case-insensitive matching shrinks the guess
        # space.
        user = User.objects.filter(email_active_code=active_code).first()
        if user is None:
            raise Http404()

        already_used = user.activated_at is not None

        # The code is deliberately not rotated, and activated_at is what makes
        # that safe.
        #
        # Rotating made the second visit a 404, and there is almost always a
        # second visit: mail scanners and link checkers fetch every URL in a
        # message before the person sees it, so the visitor's own click was the
        # one that failed on a link that had worked moments earlier.
        #
        # Once this has been used, it can no longer change is_active. Without
        # that, an account an operator disabled could be switched back on with
        # the old activation email.
        if not already_used:
            user.is_active = True
            user.activated_at = timezone.now()
            user.save(update_fields=['is_active', 'activated_at'])

        return render(request, 'account_module/account_activated.html', {
            'already_active': already_used,
        })
