<div dir="rtl">

# دلتا آپلود

**اشتراک‌گذاری فایل با درآمد برای آپلودکننده.** فایل را می‌گذاری، یک لینک می‌گیری، و هر بار که کسی از صفحه‌ی دانلود عبور می‌کند سهمی از درآمد تبلیغات آن صفحه به کیف پولت می‌نشیند. اگر بخواهی می‌توانی روی فایل قیمت بگذاری و مستقیم بفروشی؛ ۹۰٪ مبلغ فروش سهم فروشنده است و ۱۰٪ کمیسیون سایت.

[English](#delta-upload) · [d-up.ir](https://d-up.ir)

## چه کاری می‌کند

| | |
|---|---|
| **آپلود** | تا ۲ گیگابایت برای کاربر واردشده، ۲۰۰ مگابایت برای مهمان. آپلود چندتکه با نمایش درصد. |
| **دو نوع دسترسی** | عمومی یا خصوصی، با تاریخ انقضای اختیاری برای لینک عمومی. |
| **فروش فایل** | قیمت‌گذاری به سنت. خرید از کیف پول، دسترسی دائمی، بدون خرید تکراری. |
| **درآمد تبلیغات** | هر نمایش در میکروسنت انبار می‌شود و بعد از دوره‌ی نگه‌داشت به کیف پول منتقل می‌شود. |
| **کوتاه‌کننده‌ی لینک** | با صفحه‌ی میانی تبلیغاتی. نسخه‌ی درآمدزا نیاز به ورود دارد. |
| **کیف پول** | شارژ با کریپتو (NOWPayments) و زرین‌پال. برداشت با تأیید مدیر. |
| **ذخیره در تلگرام** | فایل روی کانال خصوصی خود کاربر، با رباتی که خودش می‌سازد. |
| **API** | REST با کلید، برای آپلود و مدیریت فایل از بیرون پنل. |
| **پنل مدیریت** | کاربران، فایل‌ها، تیکت، نرخ ارز، تنظیمات تبلیغات و امنیت. |
| **دو زبان** | فارسی و انگلیسی، انتخاب با کوکی نه پیشوند آدرس. اعداد فارسی فقط در حالت فارسی. |

## ذخیره در تلگرام

فایل‌های حجیم می‌توانند روی کانال خصوصی خود کاربر بروند، نه دیسک سرور. هیچ رباتی مال سایت نیست: هر کاربر ربات خودش را از BotFather می‌سازد، توکنش را در پنل ثبت می‌کند و ربات را ادمین کانال خودش می‌کند.

توکن‌ها با Fernet رمز می‌شوند و کلیدش (`TELEGRAM_TOKEN_KEY`) بیرون دیتابیس است. فایل به تکه‌های ۱۹ مگابایتی شکسته می‌شود، چون Bot API بیشتر از ۲۰ مگابایت را برنمی‌گرداند. فایل تلگرامی از سهمیه‌ی فضای کاربر کم نمی‌شود، پس صف انتقال سقف جداگانه دارد.

خطرها در صفحه‌ی همان قابلیت نوشته شده‌اند و بی‌تعارف: توکن ربات یعنی دسترسی به کانال، و کانال جای فایل‌هاست.

## معماری پول

همه‌ی مبالغ عدد صحیح‌اند، هیچ‌جا اعشار شناور نیست.

- **کیف پول و قیمت:** سنت آمریکا (`int`).
- **درآمد تبلیغات:** میکروسنت (`MICRO_PER_CENT = 10_000`)، چون سهم آپلودکننده از یک نمایش کمتر از یک سنت است و گرد کردنش یعنی صفر. باقی‌مانده در `EarningCarry` می‌ماند تا جمع شود.
- **نرخ تبلیغات:** یکتانت به ازای **کلیک** پرداخت می‌کند نه نمایش، و کلیک هرگز به ما گزارش نمی‌شود. پس نرخ هر نمایش از «تومان هر کلیک» و «کلیک در هر هزار نمایش» ساخته می‌شود و با نرخ دلار به سنت تبدیل می‌شود. بالا بودن این عدد یعنی تعهدی که از جیب اپراتور در می‌آید.
- **کمیسیون فروش:** کمیسیون رو به پایین گرد می‌شود و باقی به فروشنده می‌رسد، پس جمعشان همیشه دقیقاً برابر مبلغ پرداختی است. عدد در `Purchase.site_commission` ثبت می‌شود نه محاسبه‌ی لحظه‌ای، وگرنه عوض شدن درصد، فروش‌های سال قبل را بازنویسی می‌کرد.

## راه انداختن

نیازها: Python 3.12، PostgreSQL 16، Redis 7، Node (فقط برای build استایل).

```bash
python -m venv .venv
.venv\Scripts\activate            # ویندوز
pip install -r requirements.txt
npm install

cp .env.example .env              # مقادیر را پر کن؛ DATABASE_URL اجباری است
python manage.py migrate
python manage.py createsuperuser
npm run build                     # static/css/tailwind.css
python manage.py runserver
```

SQLite پشتیبانی نمی‌شود و این عمدی است: یک فایل با یک نویسنده با چند worker خطای `database is locked` می‌دهد، و توسعه باید روی همان موتوری باشد که production است.

داده‌ی نمونه برای دیدن سایت با محتوا:

```bash
python manage.py seed_demo
```

## کارهای دوره‌ای

بدون این‌ها سایت بالا می‌آید ولی بی‌صدا خراب کار می‌کند:

```bash
python manage.py confirm_ad_earnings    # وگرنه درآمد کاربران تا ابد «در انتظار» می‌ماند
python manage.py purge_expired_uploads  # وگرنه دیسک پر می‌شود
python manage.py update_exchange_rate   # نرخ دلار؛ بی آن نرخ تبلیغات صفر می‌شود
python manage.py telegram_worker --once # وگرنه فایل‌ها تا ابد «در صف انتقال» می‌مانند
python manage.py purge_orphan_files --delete
```

## تست

```bash
python manage.py test
```

بیش از هزار تست. چیزهایی که تست می‌کنند و از قبل شکسته بودند: ایمیل فعال‌سازی که ارسال نمی‌شد، لینک فعال‌سازی که بار دوم ۴۰۴ می‌داد (اسکنر ایمیل قبل از کاربر بازش می‌کند)، دانلود بازه‌ای که توکن یک‌بارمصرف را می‌سوزاند، و نامی که فایل با آن برمی‌گشت.

## زبان و ترجمه

کاتالوگ‌ها با `polib` کامپایل می‌شوند، نه با `gettext` سیستمی:

```bash
python manage.py compilepo
```

## مستندات

- [`.env.example`](.env.example) — هر متغیر محیطی با توضیح این‌که نبودش چه می‌شکند.
- [`DEPLOY.md`](DEPLOY.md) — استقرار، به ترتیب، با علامت‌گذاری موارد بحرانی.

</div>

---

# Delta Upload

**File sharing that pays the uploader.** Put up a file, get a link, and a share of the ad revenue from its download page lands in your wallet. Files can also be priced and sold directly: the seller keeps 90% and the site takes a 10% commission.

[فارسی](#دلتا-آپلود) · [d-up.ir](https://d-up.ir)

## What it does

| | |
|---|---|
| **Uploads** | 2 GB for a signed-in user, 200 MB for a guest. Chunked, with a live percentage. |
| **Two kinds of access** | Public or private, with an optional expiry on the public link. |
| **Selling files** | Priced in cents. Paid from the wallet, access is permanent, no repeat purchase. |
| **Ad revenue** | Accrued per impression in micro-cents, moved to the wallet after a holding period. |
| **Link shortener** | With an ad interstitial. The earning variant requires a login. |
| **Wallet** | Topped up by crypto (NOWPayments) or ZarinPal. Withdrawals need staff approval. |
| **Telegram storage** | Files kept in the user's own private channel, through a bot they create. |
| **API** | Key-authenticated REST for uploading and managing files outside the panel. |
| **Staff panel** | Users, files, tickets, exchange rate, ad and security settings. |
| **Two languages** | Persian and English, selected by cookie rather than URL prefix. Persian numerals only in Persian. |

## Telegram storage

Large files can live in the user's own private channel instead of on the server's disk. No bot belongs to the site: each user creates their own through BotFather, registers its token in their panel, and makes it an admin of their own channel.

Tokens are encrypted with Fernet and the key (`TELEGRAM_TOKEN_KEY`) is held outside the database. Files are split into 19 MB pieces because the Bot API will not hand back more than 20 MB. A Telegram-backed file does not count against the storage quota, so the transfer queue has a cap of its own.

The risks are written plainly on the feature's own page: the bot token is access to the channel, and the channel is where the files are.

## How the money works

Every amount is an integer. There is no floating point anywhere in the accounting.

- **Wallets and prices:** US cents (`int`).
- **Ad earnings:** micro-cents (`MICRO_PER_CENT = 10_000`), because an uploader's share of a single impression is worth less than a cent and rounding it makes it zero. The remainder is held in `EarningCarry` until it adds up.
- **The ad rate:** Yektanet pays per *click*, not per impression, and a click is never reported back to us. So the impression rate is derived from toman-per-click and clicks-per-mille, then converted to cents at the current exchange rate. Setting it too high promises money that comes out of the operator's own pocket.
- **Sale commission:** the commission rounds down and the seller takes the remainder, so the two always add back to exactly what the buyer paid. The figure is recorded in `Purchase.site_commission` rather than recomputed, or changing the rate would rewrite last year's sales.

## Running it

Needs Python 3.12, PostgreSQL 16, Redis 7, and Node for the stylesheet build.

```bash
python -m venv .venv
source .venv/bin/activate         # .venv\Scripts\activate on Windows
pip install -r requirements.txt
npm install

cp .env.example .env              # fill it in; DATABASE_URL is required
python manage.py migrate
python manage.py createsuperuser
npm run build                     # static/css/tailwind.css
python manage.py runserver
```

SQLite is deliberately unsupported: one file with one writer gives `database is locked` under several workers, and development should run on the same engine as production.

For a site with something in it:

```bash
python manage.py seed_demo
```

## Scheduled work

Without these the site runs and quietly misbehaves:

```bash
python manage.py confirm_ad_earnings    # or earnings stay "pending" forever
python manage.py purge_expired_uploads  # or the disk fills up
python manage.py update_exchange_rate   # the dollar rate; without it the ad rate is zero
python manage.py telegram_worker --once # or files sit in the transfer queue forever
python manage.py purge_orphan_files --delete
```

## Tests

```bash
python manage.py test
```

Over a thousand of them. Among the things they cover, each of which was once broken: the activation email that was never sent, the activation link that 404'd on a second visit (mail scanners open it before the user does), range requests burning a single-use download token, and the name a file came back under.

## Translation

Catalogues are compiled with `polib` rather than a system `gettext`:

```bash
python manage.py compilepo
```

## Documentation

- [`.env.example`](.env.example) — every environment variable, with what breaks when it is missing.
- [`DEPLOY.md`](DEPLOY.md) — deployment in order, with the critical steps marked.
