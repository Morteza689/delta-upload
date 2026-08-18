"""Telegram Bot API client.

Only the handful of calls this project makes, over plain HTTP with requests. A
full library would pull in an async stack for the sake of seven endpoints.

Every function takes the token explicitly. There is no site-wide bot: each user
brings their own, so there is no sensible default and a missing token should be
a TypeError at the call site rather than a silent fall back to somebody else's
credentials.

Everything here raises TelegramError and nothing else. Callers deal with one
exception type instead of distinguishing a timeout from a 400 from a malformed
body, none of which they can act on differently.
"""
import logging

import requests

logger = logging.getLogger(__name__)

API_ROOT = 'https://api.telegram.org'

#: Bot API caps sendDocument at 50 MB and getFile downloads at 20 MB. The
#: smaller of the two is what actually binds, so files are split below it.
#: A little headroom is left for the multipart envelope.
CHUNK_BYTES = 19 * 1024 * 1024

#: Short calls. Uploads and downloads pass their own, much longer.
TIMEOUT = 30

#: An upload of a 19 MB part over a slow link still has to finish.
UPLOAD_TIMEOUT = 600


class TelegramError(Exception):
    """Any failure talking to Telegram."""

    def __init__(self, message, *, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        #: Set when Telegram asked us to back off, in seconds.
        self.retry_after = retry_after


def _unwrap(response):
    """Turn a Bot API response into its result, or raise."""
    try:
        payload = response.json()
    except ValueError:
        raise TelegramError(
            f'Telegram sent a non-JSON reply (HTTP {response.status_code})',
            status=response.status_code,
        )

    if payload.get('ok'):
        return payload.get('result')

    parameters = payload.get('parameters') or {}
    raise TelegramError(
        payload.get('description') or 'Telegram rejected the call',
        status=response.status_code,
        retry_after=parameters.get('retry_after'),
    )


def call(token, method, data=None, files=None, timeout=TIMEOUT):
    """One Bot API method, as the bot that owns this token."""
    if not token:
        raise TelegramError('No bot token was given')

    try:
        response = requests.post(
            f'{API_ROOT}/bot{token}/{method}', data=data or {}, files=files,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise TelegramError(f'Could not reach Telegram: {exc}')

    return _unwrap(response)


def get_me(token):
    return call(token, 'getMe')


def send_message(token, chat_id, text, **extra):
    return call(token, 'sendMessage', {'chat_id': chat_id, 'text': text, **extra})


def send_document(token, chat_id, filename, stream, caption=None):
    """Upload one part. Returns the message as Telegram stored it."""
    data = {'chat_id': chat_id, 'disable_notification': 'true'}
    if caption:
        data['caption'] = caption
    return call(
        token, 'sendDocument', data=data,
        files={'document': (filename, stream)}, timeout=UPLOAD_TIMEOUT,
    )


def delete_message(token, chat_id, message_id):
    return call(token, 'deleteMessage',
                {'chat_id': chat_id, 'message_id': message_id})


def get_chat(token, chat_id):
    return call(token, 'getChat', {'chat_id': chat_id})


def get_chat_member(token, chat_id, user_id):
    return call(token, 'getChatMember', {'chat_id': chat_id, 'user_id': user_id})


def get_file_path(token, file_id):
    """Resolve a file_id to the path used by the download host."""
    result = call(token, 'getFile', {'file_id': file_id})
    path = (result or {}).get('file_path')
    if not path:
        raise TelegramError('Telegram returned no file path')
    return path


def open_file(token, file_path, timeout=UPLOAD_TIMEOUT):
    """Start downloading a stored part. Returns a streaming response."""
    url = f'{API_ROOT}/file/bot{token}/{file_path}'
    try:
        response = requests.get(url, stream=True, timeout=timeout)
    except requests.RequestException as exc:
        raise TelegramError(f'Could not reach Telegram: {exc}')

    if response.status_code != 200:
        response.close()
        raise TelegramError(
            f'Telegram refused the download (HTTP {response.status_code})',
            status=response.status_code,
        )
    return response


def get_updates(token, offset=None, timeout=0, allowed=None):
    """Pending updates for this bot.

    Called on demand when a user asks us to look for their channel, not from a
    daemon, so the default timeout is zero: a long poll would hold a web worker
    open for half a minute waiting on something that has usually already
    happened.

    Because each user has their own bot, this needs no webhook and no running
    process. That removes a public endpoint, a shared secret, and a deploy step
    from the feature entirely.
    """
    data = {'timeout': timeout, 'limit': 100}
    if offset is not None:
        data['offset'] = offset
    if allowed is not None:
        data['allowed_updates'] = allowed
    return call(token, 'getUpdates', data, timeout=timeout + 20)


def delete_webhook(token):
    """Clear any webhook so getUpdates works.

    Telegram refuses getUpdates while a webhook is registered. A user who
    pointed their bot somewhere else before bringing it here would otherwise
    hit an error they have no way to understand.
    """
    return call(token, 'deleteWebhook', {'drop_pending_updates': 'false'})
