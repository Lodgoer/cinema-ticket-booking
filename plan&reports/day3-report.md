# گزارش کامل روز ۳ — پروژه‌ی رزرو بلیط سینما

**تاریخ:** ۳۰ تیر ۱۴۰۵ (۲۱ ژوئیه ۲۰۲۶)
**هدف روز:** فلوی کامل رزرو (از hold تا بلیط) + پرداخت sandbox + background worker

---

## ۱. اضافه کردن `expires_at` به جدول Booking

### چرا لازم بود؟

تا این مرحله، وقتی کاربر یه صندلی رو hold می‌کرد و سپس booking می‌ساخت، اگه پرداخت نمی‌کرد، اون booking برای همیشه `pending` باقی می‌موند. نیاز به یه مکانیزم خودکار برای پاک‌سازی booking های منقضی‌شده بود — بدون نیاز به cleanup دستی.

### تصمیم طراحی

یه ستون `expires_at` به جدول `booking` اضافه شد که مشخص می‌کنه هر booking تا چه زمانی معتبره. این مقدار دقیقاً برابر با TTL همون hold تو Redis هست (۱۰ دقیقه). Background worker هر چند دقیقه یه‌بار booking های منقضی‌شده رو پیدا و لغوشون می‌کنه.

### پیاده‌سازی

تغییر در `models.py`:

```python
class Booking(Base):
    # ... سایر فیلدها
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

Migration خودکار توسط Alembic ساخته شد:

```
alembic revision --autogenerate -m "add expires_at to booking"
```

Alembic تشخیص داد فقط یه ستون اضافه شده (`Detected added column 'booking.expires_at'`) — یعنی بقیه‌ی مدل‌ها دقیقاً منطبق با دیتابیس واقعی بودن.

### تغییر در schema.sql

```sql
CREATE TABLE booking (
    -- ... سایر ستون‌ها
    expires_at      TIMESTAMPTZ,                            -- when the hook expires;
                                                              -- background worker sweeps these
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**نکته:** چون جدول از قبل داده داشت، ستون به‌صورت `nullable` اضافه شد تا رکوردهای موجود خراب نشن. رکوردهای جدید همیشه مقدار دارن.

---

## ۲. Pydantic Schemas برای Booking/Payment/Ticket

### الگوی کلی

دقیقاً همون الگوی روز ۲ (Create/Read/Update) برای سه موجودیت جدید اعمال شد:

```python
# ---------- Booking ----------
class BookingCreate(BaseModel):
    showtime_id: int
    seat_ids: list[int]  # لیست صندلی‌هایی که کاربر می‌خواد رزرو کنه

class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    status: str
    total_price: Decimal
    expires_at: datetime
    created_at: datetime
    booking_seats: list[BookingSeatRead]  # nesting — مثل navigation property در .NET

# ---------- Payment ----------
class PaymentCreate(BaseModel):
    booking_id: int
    idempotency_key: str  # کلید یکتا برای جلوگیری از پرداخت تکراری

# ---------- Ticket ----------
class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    booking_seat_id: int
    qr_code: str         # کد QR یکتا برای هر بلیط
    issued_at: datetime
```

**نکته‌ی طراحی `BookingCreate`:** به‌جای گرفتن `booking_id` از کاربر، لیست `seat_ids` گرفته می‌شه. این یعنی کاربر مشخص می‌کنه «این صندلی‌ها رو می‌خوام» و سیستم خودش booking رو می‌سازه. این API تمیزتریه چون کاربر نباید با ID داخلی سیستم کار کنه.

---

## ۳. Booking Service — منطق اصلی کسب‌وکار

### فلسفه

`booking_service.py` معادل یه **Application Service** در Clean Architecture هست — منطق کسب‌وکار رو از router (HTTP) و repository (دیتابیس) جدا می‌کنه. هر تابع یه عملیات واحد (unit of work) هست که چند کوئری رو در یه تراکنش انجام میده.

### تابع ۱: `create_booking`

این مهم‌ترین تابع پروژه‌ست — لحظه‌ای که «انتخاب صندلی» تبدیل به «رزرو واقعی» می‌شه.

```python
async def create_booking(session, user_id, showtime_id, seat_ids) -> Booking:
    # ۱. پیدا کردن ShowtimeSeat ها برای صندلی‌های درخواستی
    # ۲. چک اینکه هیچکدام booked نباشن
    # ۳. محاسبه قیمت کل از price_snapshot
    # ۴. ساخت Booking (status='pending', expires_at=now+10min)
    # ۵. ساخت BookingSeat برای هر صندلی (status='active')
    # ۶. تغییر وضعیت ShowtimeSeat به 'booked'
    # ۷. commit — اگه partial unique index نقض بشه، IntegrityError می‌گیریم
```

**نکته‌ی کلیدی:** این تراکنش روی Postgres اجرا می‌شه، نه Redis. اگه دو کاربر همزمان سعی کنن روی یه صندلی booking بسازن، `uq_active_booking_seat` جلوی هر دو رو نمی‌گیره — فقط یکی برنده می‌شه و اون یکی `IntegrityError` می‌گیره که تبدیل به `409 Conflict` می‌شه.

```python
try:
    await session.commit()
except IntegrityError:
    await session.rollback()
    raise ValueError("One or more seats were just taken by another customer")
```

### باگ کشف‌شده: MissingGreenlet

بعد از پیاده‌سازی اولیه، endpoint ساخت booking خطای `500 Internal Server Error` برگردوند. بررسی Traceback نشون داد:

```
NameError: name 'select' is not defined
```

علت: `select` فقط داخل تابع import شده بود، نه در سطح ماژول. بعد از رفع اول، خطای دومی ظاهر شد:

```
MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here
```

**علت واقعی:** وقتی FastAPI سعی می‌کنه `BookingRead` رو از شیء SQLAlchemy بسازه، به فیلد `booking_seats` (یک relationship) دسترسی پیدا می‌کنه. در حالت async، SQLAlchemy نمی‌تونه lazy loading انجام بده — چون lazy loading نیاز به I/O داره و ما در یه greenlet هستیم.

**راه‌حل:** بعد از commit، booking رو دوباره با `selectinload` query می‌کنیم تا relationship از قبل eager-loading شده باشه:

```python
result = await session.execute(
    select(Booking)
    .where(Booking.id == booking.id)
    .options(selectinload(Booking.booking_seats))
)
return result.scalar_one()
```

**نکته‌ی مهم برای دفاع:** این یه مشکل رایج در SQLAlchemy async هست. در حالت sync، lazy loading خودکار کار می‌کنه؛ در async، باید صریحاً eager loading مشخص بشه. این تفاوت مستقیماً از معماری greenlet در Python نشأت می‌گیره.

### تابع ۲: `cancel_booking`

دقیقاً طبق پseudocode ای که در `schema.sql` روز ۱ نوشته شده بود:

```python
async def cancel_booking(session, booking_id, user_id) -> Booking:
    # ۱. SELECT ... FOR UPDATE — قفل ردیف booking
    result = await session.execute(
        select(Booking)
        .where(Booking.id == booking_id, Booking.user_id == user_id)
        .with_for_update()  # ← این خط قفل رو اعمال می‌کنه
    )
    booking = result.scalar_one_or_none()
    
    # ۲. چک idempotency — اگه قبلاً لغو شده، کاری نکن
    if booking.status in ("cancelled", "expired"):
        return booking  # idempotent
    
    # ۳. غیرفعال کردن booking_seat ها
    # ۴. آزادسازی showtime_seat ها
    # ۵. تغییر وضعیت booking به 'cancelled'
```

**چرا `SELECT ... FOR UPDATE` لازمه؟** بدون این قفل، اگه دو درخواست همزمان برای لغو یه booking بیاد، هر دو می‌بینن `status='pending'` و هر دو سعی می‌کنن لغو کنن. نتیجه: صندلی‌ها دوبار آزاد می‌شن. با قفل، درخواست دوم تا اتمام اولی wait می‌کنه و بعد `status='cancelled'` رو می‌بینه و کاری نمی‌کنه.

### تابع ۳: `confirm_payment`

بعد از موفقیت پرداخت، booking رو تایید و بلیط صادر می‌کنه:

```python
async def confirm_payment(session, booking_id) -> Booking:
    # ۱. چک وضعیت booking (باید 'pending' باشه)
    # ۲. پیدا کردن booking_seat های active
    # ۳. صدور Ticket با QR code یکتا برای هر صندلی
    # ۴. تغییر وضعیت booking به 'confirmed'
```

### تابع ۴: `sweep_expired_bookings`

برای background worker:

```python
async def sweep_expired_bookings(session) -> int:
    now = datetime.now(timezone.utc)
    # پیدا کردن booking های pending که expires_at اونها گذشته
    # لغو هرکدوم (آزادسازی صندلی‌ها)
    # تغییر وضعیت به 'expired'
```

---

## ۴. Payment Provider ساختگی (Mock Stripe)

### فلسفه

به‌جای اتصال به یه درگاه واقعی (نیاز به کارت تست، API key، و فرآیند approval)، یه `FakePaymentProvider` ساخته شد که رفتار Stripe رو شبیه‌سازی می‌کنه:

```python
class FakePaymentProvider:
    async def charge(self, amount: float, idempotency_key: str) -> dict:
        await asyncio.sleep(0.1)  # شبیه‌سازی تاخیر شبکه
        
        provider_ref = f"ch_{uuid.uuid4().hex[:16]}"  # مثل Stripe charge ID
        
        # مقادیر خاص (با .99) برای تست مسیر خطا
        if amount % 1 == 0.99:
            return {"success": False, "error": "Card declined", "provider_ref": provider_ref}
        
        return {"success": True, "provider_ref": provider_ref}
    
    async def refund(self, provider_ref: str) -> dict:
        # stub برای آینده
        ...
```

**نکته‌ی تست:** اگه مبلغ پرداخت با `.99` تموم بشه (مثلاً `10.99`)، پرداخت رد می‌شه. این برای تست کردن مسیر خطا بدون نیاز به mock پیچیده خیلی مفیده.

---

## ۵. Endpoints رزرو و پرداخت

### ساختار `booking_router.py`

پنج endpoint جدید اضافه شد:

| Method | Path | توضیح |
|---|---|---|
| `POST` | `/bookings` | ساخت booking از صندلی‌های held شده |
| `GET` | `/bookings/{id}` | نمایش جزئیات booking |
| `POST` | `/bookings/{id}/cancel` | لغو رزرو |
| `POST` | `/bookings/pay` | پرداخت (با idempotency key) |
| `GET` | `/bookings/{id}/tickets` | دریافت بلیط‌ها |

### Endpoint پرداخت — ترتیب چک‌ها مهمه

```python
@booking_router.post("/pay")
async def pay_for_booking(data: PaymentCreate, ...):
    # ۱. اول idempotency check — اگه کلید تکراریه، همون payment قبلی رو برگردون
    existing = await session.execute(
        select(Payment).where(Payment.idempotency_key == data.idempotency_key)
    )
    if existing_payment:
        return existing_payment  # ← بدون چک کردن وضعیت booking!
    
    # ۲. بعد چک وضعیت booking
    if booking.status != "pending":
        raise HTTPException(status_code=409, ...)
```

**باگ کشف‌شده و رفع‌شده:** در نسخه‌ی اولیه، چک وضعیت booking **قبل از** idempotency check بود. نتیجه: اگه کاربر دکمه‌ی پرداخت رو دوبار بزنه (retry)، درخواست دوم چون booking قبلاً `confirmed` شده، خطای ۴۰۹ می‌داد — در حالی که باید همون payment قبلی رو برمی‌گردوند. ترتیب درست: اول idempotency، بعد وضعیت.

### flow کامل تست‌شده

```
Hold seat → POST /bookings (pending) → POST /pay (succeeded) → GET /tickets (QR code)
```

```
1. Holding seat 8...
   OK: held_by=5 ttl=600s

2. Creating booking...
   OK: id=4 status=pending total=10.00 seats=1

3. Paying...
   OK: status=succeeded ref=ch_d9769b57a2344b0e

4. Getting tickets...
   OK: 1 ticket(s)
   QR=TKT-667EA5DF015B

5. Seat map:
   seat=5 status=booked
   seat=6 status=booked
   seat=7 status=booked
   seat=8 status=booked
```

---

## ۶. Background Worker با arq

### چرا background worker؟

وقتی کاربری صندلی‌ها رو hold کرد و booking ساخت ولی پرداخت نکرد، اون booking باید بعد از مدتی منقضی بشه و صندلی‌ها آزاد بشن. این کار نباید در درخواست اصلی انجام بشه (چون ممکنه کاربر ساعت‌ها بعد برگرده)، بلکه یه فرآیند پس‌زمینه‌ای باید دوره‌ای این کار رو انجام بده.

### انتخاب arq به‌جای Celery

**arq** یه worker سبک و async-native هست که روی Redis سوار می‌شه. مزایا نسبت به Celery برای این پروژه:
- ساده‌تر (نیاز به broker جداگانه نداره، از همون Redis استفاده می‌کنه)
- async از پایه (سازگار با SQLAlchemy async)
- کمتر از ۳۰ خط تنظیم

### محتوای `worker.py`

```python
async def sweep_expired_bookings(ctx) -> int:
    """هر دقیقه اجرا می‌شه: booking های منقضی‌شده رو لغو می‌کنه."""
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        # پیدا کردن booking های pending با expires_at گذشته
        # لغو هرکدوم و آزادسازی صندلی‌ها
        ...

class WorkerSettings:
    functions = [sweep_expired_bookings]
    cron_jobs = [
        cron(sweep_expired_bookings),  # هر دقیقه
    ]
```

### trade-off شناخته‌شده: Sweep vs Lazy Check

**Sweep (پیاده‌سازی فعلی):** worker دوره‌ای (هر دقیقه) polling می‌کنه. مزیت: ساده و قابل‌اعتماد. عیب: تا ۱ دقیقه تاخیر بین منقضی شدن hold و واقعاً آزاد شدن صندلی.

**Lazy Check (بهبود آینده):** هر بار که صندلی خونده می‌شه، چک بشه آیا hold منقضی شده. مزیت: real-time. عیب: پیچیده‌تر و نیاز به تغییر چند endpoint.

**چرا Sweep انتخاب شد؟** برای یه پروژه‌ی یک‌هفته‌ای، سادگی اجرا مهم‌تر از real-time بودنِ ۱ دقیقه‌ایه. در سند `worker.py` ثبت شده که lazy check به‌عنوان بهبود آینده شناسایی شده — این خودش یه نکته‌ی خوب برای مصاحبه‌ست (نشون میدی trade-off رو می‌دونی و آگاهانه انتخاب کردی).

---

## ۷. تست Concurrency

### هدف

اثبات عملی این ادعا که **«double booking غیرممکنه»** — نه فقط در تئوری، بلکه با کد واقعی.

### طراحی تست

```python
@pytest.mark.asyncio
async def test_concurrent_seat_booking():
    # دو کاربر همزمان سعی می‌کنن یه صندلی رو hold کنن
    hold_a, hold_b = await asyncio.gather(
        client.post(f"/showtimes/{id}/seats/{id}/hold", headers=headers_a),
        client.post(f"/showtimes/{id}/seats/{id}/hold", headers=headers_b),
    )
    
    # دقیقاً یکی باید موفق بشه
    assert 200 in results
    assert results.count(200) == 1
    
    # برنده booking می‌سازه
    # بازنده باید خطا بگیره
```

### دو لایه‌ی تضمین

تست دو لایه‌ی محافظت رو验证 می‌کنه:

1. **لایه‌ی اول (Redis):** `SET NX` تضمین می‌کنه فقط یکی hold موفق می‌شه
2. **لایه‌ی دوم (Postgres):** `uq_active_booking_seat` تضمین می‌کنه حتی اگه Redis fail بشه، دو booking فعال روی یه صندلی وجود نداشته باشه

**این دقیقاً همون چیزیه که در مصاحبه پرسیده می‌شه:** «وقتی دو نفر همزمان یه صندلی رو بخوان چی می‌شه؟» — و جواب: «هر دو لایه (Redis + Postgres) تضمین می‌کنن فقط یکی برنده بشه.»

### نحوه اجرا

```
pytest test_concurrency.py -v
```

---

## ۸. Commit های امروز

پنج commit جداگانه و منطقی انجام شد:

**Commit اول:**
```
git commit -m "Add expires_at to booking with Alembic migration for booking expiry tracking"
```

**Commit دوم:**
```
git commit -m "Add Pydantic schemas for booking, payment, and ticket endpoints"
```

**Commit سوم:**
```
git commit -m "Add booking service (create/cancel/confirm/sweep) and mock Stripe payment provider"
```

**Commit چهارم:**
```
git commit -m "Add booking endpoints (create/pay/cancel/tickets) and arq background sweep worker"
```

**Commit پنجم:**
```
git commit -m "Add concurrency test for double-booking prevention and update requirements"
```

**پکیج‌های جدید نصب‌شده:**
- `arq` — background worker سبک و async-native
- `pytest`, `pytest-asyncio` — تست‌نویسی async
- `httpx` — تست endpoint ها با ASGI transport

---

## ۹. باگ‌های کشف‌شده و رفع‌شده

### باگ ۱: `NameError: name 'select' is not defined`

**علت:** وقتی import های محلی (`from sqlalchemy import select`) از داخل تابع حذف شدن (چون قبلاً در سطح ماژول بودن)، فراموش شد `select` رو به import های سطح ماژول اضافه کنیم.

**رفع:** اضافه کردن `from sqlalchemy import select` به import های بالای فایل.

### باگ ۲: `MissingGreenlet: greenlet_spawn has not been called`

**علت:** FastAPI سعی داشت relationship `booking_seats` رو lazy load کنه، ولی در حالت async این غیرممکنه.

**رفع:** استفاده از `selectinload` برای eager loading رابطه‌ها بعد از commit.

### باگ ۳: Idempotency check در ترتیب اشتباه

**علت:** چک وضعیت booking قبل از idempotency check بود. نتیجه: retry پرداخت (با کلید یکسان) خطای ۴۰۹ می‌داد به‌جای بازگرداندن payment قبلی.

**رفع:** جابه‌جایی ترتیب — اول idempotency check، بعد چک وضعیت booking.

---

## خلاصه‌ی وضعیت پلن روز ۳

| بلوک | کار | وضعیت |
|---|---|---|
| **بلوک ۱** | تراکنش نهایی رزرو (create_booking) | ✅ |
| بلوک ۱ | تراکنش کنسل رزرو (cancel_booking + FOR UPDATE) | ✅ |
| بلوک ۱ | مدیریت خطای duplicate roll (409 Conflict) | ✅ |
| **بلوک ۲** | تست concurrency (pytest + asyncio) | ✅ |
| **بلوک ۳** | payment provider ساختگی (Mock Stripe) | ✅ |
| بلوک ۳ | state machine پرداخت (pending → succeeded/failed) | ✅ |
| بلوک ۳ | idempotency_key برای جلوگیری از پرداخت تکراری | ✅ |
| بلوک ۳ | background worker (arq) برای sweep دوره‌ای | ✅ |
| **بلوک ۴** | صدور بلیط با QR code بعد از پرداخت موفق | ✅ |
| بلوک ۴ | مدیریت timeout ( expires_at + sweep) | ✅ |

تمام موارد پلن روز ۳ با موفقیت و به‌صورت کامل تست‌شده به پایان رسید. کل فلوی انتخاب صندلی تا بلیط نهایی اکنون کار می‌کنه.
