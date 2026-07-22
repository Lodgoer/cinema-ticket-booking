# گزارش کامل روز ۴ — پروژه‌ی رزرو بلیط سینما

**تاریخ:** ۳۱ تیر ۱۴۰۵ (۲۲ ژوئیه ۲۰۲۶)
**هدف روز:** صف مدیریت ازدحام + آمار و گزارش‌ها + جمع‌بندی فنی

---

## ۱. Waiting Room — صف انتظار مجازی (Redis Sorted Set)

### چرا لازم بود؟

برای سانس‌های پرطرفدار (مثلاً اکران اول یه فیلم پرفروش)، ممکنه صدها کاربر همزمان وارد سایت بشن. اگه به همه اجازه‌ی انتخاب صندلی بدیم، هم سرور overload می‌شه، هم تجربه‌ی کاربر بد می‌شه (همه چی کند می‌شه). Waiting room مثل Ticketmaster عمل می‌کنه: کاربرها وارد صف می‌شن و به‌صورت batch اجازه‌ی انتخاب صندلی می‌گیرن.

### تصمیم طراحی

**Redis Sorted Set** (`waiting_room:{showtime_id}`) با امتیاز = زمان ورود (timestamp). این ترتیب FIFO رو رایگان میده:
- `ZADD`: اضافه کردن کاربر به صف
- `ZRANK`: گرفتن جایگاه کاربر تو صف
- `ZPOPMIN`: بیرون کشیدن N نفر اول (کمترین امتیاز = زودتر وارد شده = FIFO)

### پیاده‌سازی — `waiting_room.py`

```python
# سه نوع کلید Redis:
# waiting_room:{showtime_id}       — ZSET از user_id -> join_timestamp
# waiting_room_admitted:{showtime_id} — SET از admitted user IDs (با TTL)
# waiting_room_token:{showtime_id}:{user_id} — STRING "1", با TTL

BATCH_SIZE = 10           # تعداد کاربر در هر batch
ADMISSION_INTERVAL = 5    # ثانیه بین batch ها
WAITING_ROOM_TOKEN_TTL_SECONDS = 120  # ۲ دقیقه — فقط برای ورود به مرحله انتخاب صندلی
```

**نکته‌ی طراحی توکن:** توکن admission فقط ۱۲۰ ثانیه معتبره — یعنی کاربر بعد از admit شدن باید سریع صندلی‌ها رو hold کنه. اگه hold موفق باشه، TTL ۶۰۰ ثانیه‌ای Redis hold جایگزین می‌شه.

### Endpoint ها — `waiting_room_router.py`

| Method | Path | توضیح |
|---|---|---|
| `POST` | `/waiting-room/{showtime_id}/join` | ورود به صف |
| `GET` | `/waiting-room/{showtime_id}/status` | نمایش جایگاه و وضعیت admission |
| `POST` | `/waiting-room/{showtime_id}/admit` | فراخوانی batch بعدی (admin/worker) |
| `POST` | `/waiting-room/{showtime_id}/leave` | ترک صف |

### اتصال به Seat Hold — `hold_router.py`

تغییر مهم در `hold_seat`: اگه waiting room فعال باشه (کلید ZSET وجود داشته باشه)، کاربر باید توکن admission معتبر داشته باشه وگرنه ۴۰۳ Forbidden می‌گیره:

```python
queue_exists = await r.exists(waiting_room_key(showtime_id))
if queue_exists:
    admitted = await is_admitted(r, showtime_id, user.id)
    if not admitted:
        raise HTTPException(status_code=403, detail="You must be admitted through the waiting room...")
```

**نکته‌ی مهم:** Waiting room اختیاریه — اگه کسی تو صف نباشه، hold عادی کار می‌کنه. این یعنی پروژه backward-compatible می‌مونه.

---

## ۲. Statistics & Reports — آمار برای مدیر/ادمین

### دسترسی مبتنی بر نقش (Role-Based Access)

- **Admin:** آمار همه‌ی سینماها
- **Theater Manager:** فقط آمار سینماهایی که مدیریتشون می‌کنه (از طریق جدول `cinema_manager`)
- **Customer:** اصلاً دسترسی نداره

### `stats_router.py` — پنج گزارش تجمیعی

| Endpoint | توضیح | پیاده‌سازی |
|---|---|---|
| `/stats/sales-by-movie` | تعداد بلیط و درآمد به تفکیک فیلم | Aggregate query |
| `/stats/sales-by-cinema` | تعداد بلیط و درآمد به تفکیک سینما | Aggregate query |
| `/stats/sales-by-showtime` | تعداد بلیط و درآمد به تفکیک سانس | Aggregate query |
| `/stats/revenue-over-time` | درآمد روزانه | Aggregate query |
| `/stats/peak-hours` | ساعت‌های پرطرفدار | Aggregate query |

### `reports_router.py` — Materialized View برای Occupancy Rate

**چرا materialized view برای occupancy rate؟**

گزارش occupancy rate نیاز به JOIN پیچیده‌ی چهار جدول (showtime + hall + cinema + showtime_seat) داره و ممکنه هر لحظه query بشه. با materialized view، این محاسبه هر ۵ دقیقه یه‌بار انجام می‌شه و query خیلی سریع‌تره.

**چرا بقیه‌ی گزارش‌ها materialized view ندارن؟**

| گزارش | پیاده‌سازی | دلیل |
|---|---|---|
| Occupancy rate | Materialized view | JOIN پیچیده، query مکرر |
| Sales by movie/cinema | Aggregate query | ساده‌تر، real-time بهتره |
| Revenue over time | Aggregate query | داده‌ی سری زمانی ارزش fresh بودن داره |
| Peak hours | Aggregate query | سبک، نیاز به cache نداره |

**Migration — `alembic/versions/a1b2c3d4e5f6_add_materialized_view_occupancy_rate.py`:**

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_occupancy_rate AS
SELECT
    st.id AS showtime_id,
    -- ... فیلدها
    ROUND(booked::numeric / NULLIF(capacity, 0) * 100, 1) AS occupancy_percent
FROM showtime st
JOIN hall h ON h.id = st.hall_id
-- ...
WITH NO DATA;

-- ایندکس یکتا برای CONCURRENTLY refresh
CREATE UNIQUE INDEX idx_mv_occupancy_rate_showtime ON mv_occupancy_rate (showtime_id);
```

**Trade-off:** داده‌ی occupancy حداکثر ۵ دقیقه تاخیر داره. این برای داشبورد مدیریتی قابل قبوله. برای نمایش real-time صندلی‌ها، Redis hold map + Postgres status منبع حقیقیه.

---

## ۳. Background Worker — دو task جدید

### `sweep_waiting_room` — Admit دوره‌ای صف

هر `ADMISSION_INTERVAL` ثانیه (پیش‌فرض ۵)، worker همه‌ی waiting room های فعال رو اسکن می‌کنه و N نفر اول رو admit می‌کنه:

```python
async def sweep_waiting_room(ctx) -> int:
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor=cursor, match="waiting_room:*", count=100)
        for key in keys:
            showtime_id = int(key.split(":")[-1])
            admitted = await admit_batch(r, showtime_id, BATCH_SIZE)
            total_admitted += len(admitted)
        if cursor == 0:
            break
    return total_admitted
```

### `refresh_occupancy_view` — بروزرسانی Materialized View

هر ۵ دقیقه، materialized view رو refresh می‌کنه:

```python
async def refresh_occupancy_view(ctx) -> None:
    async with async_session() as session:
        await session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_occupancy_rate"))
        await session.commit()
```

**نکته‌ی `CONCURRENTLY`:** این کلمه تضمین می‌کنه که در حین refresh، query های خواندنی بلاک نشن. بدون این کلمه، کل view قفل می‌شد تا refresh تموم بشه.

---

## ۴. تست‌ها — نتایج واقعی

### تست‌های واحد (`test_unit.py`) — ۲۰/۲۰ PASS

```
test_unit.py::TestPaymentStateMachine::test_all_states_are_known PASSED
test_unit.py::TestPaymentStateMachine::test_refunded_is_terminal PASSED
test_unit.py::TestPaymentStateMachine::test_succeeded_cannot_be_reprocessed PASSED
test_unit.py::TestPaymentStateMachine::test_failed_is_terminal PASSED
test_unit.py::TestPaymentStateMachine::test_pending_can_transition_to_processing PASSED
test_unit.py::TestPaymentStateMachine::test_processing_can_transition_to_succeeded PASSED
test_unit.py::TestPaymentStateMachine::test_processing_can_transition_to_failed PASSED
test_unit.py::TestSeatClaimIntegrityError::test_integrity_error_raises_valueerror PASSED
test_unit.py::TestSeatClaimIntegrityError::test_booking_router_translates_to_409 PASSED
test_unit.py::TestSeatClaimIntegrityError::test_error_message_contains_seat_info PASSED
test_unit.py::TestPriceCalculation::test_single_seat_price PASSED
test_unit.py::TestPriceCalculation::test_multiple_seats_price PASSED
test_unit.py::TestPriceCalculation::test_all_same_price PASSED
test_unit.py::TestPriceCalculation::test_decimal_precision PASSED
test_unit.py::TestPriceCalculation::test_empty_seat_list_is_zero PASSED
test_unit.py::TestPriceCalculation::test_booking_service_calculates_total PASSED
test_unit.py::TestWaitingRoomAdmissionOrdering::test_fifo_ordering PASSED
test_unit.py::TestWaitingRoomAdmissionOrdering::test_token_ttl_is_120_seconds PASSED
test_unit.py::TestWaitingRoomAdmissionOrdering::test_zpopmin_gives_fifo PASSED
test_unit.py::TestWaitingRoomAdmissionOrdering::test_batch_size_configurable PASSED

============================= 20 passed in 2.02s =============================
```

**پوشش تست:**
- State machine پرداخت (۷ تست): ترنزیشن‌های معتبر و نامعتبر
- IntegrityError → 409 Conflict (۳ تست): ساختار کد + پیام خطا
- محاسبه قیمت (۶ تست): تک صندلی، چند صندلی، precision اعشاری
- Waiting room (۴ تست): FIFO ordering، TTL، configurable batch size

### تست concurrency (`test_concurrency.py`) — نیاز به سرور واقعی

این تست‌ها به سرور در حال اجرا (docker containers) نیاز دارن و نمی‌تونن به‌صورت standalone اجرا بشن. طبق Day 3، این تست‌ها با موفقیت اجرا شدن و اثبات کردن double-booking غیرممکنه.

### بدهی فنی شناسایی‌شده (Technical Debt)

**مشکل:** تست‌های concurrency (`test_concurrency.py`) به `showtime_id=1` و `seat_id=1` hardcoded وابسته‌ان. این یعنی:
- تست‌ها فقط با داده‌ی seed شده در دیتابیس کار می‌کنن
- اگه داده‌ی seed عوض بشه، تست‌ها fail می‌شن
- اجرای parallel تست‌ها مشکل‌ساز می‌شه (conflict روی همون seat)

**راه‌حل پیشنهادی (برای بعد از دفاع):**
- استفاده از test fixtures یا seeded test data (مثلاً `conftest.py` با factory pattern)
- یا ساخت داده‌ی test در هر تست به‌صورت isolated (cinema → hall → seats → movie → showtime)
- این مورد در code review بعد از دفاع بررسی و رفع می‌شه.

---

## ۵. README — مستندسازی کامل

README پروژه از ۱ خط به ۱۳۹ خط ارتقا پیدا کرد. بخش‌ها:

1. **Architecture** — جدول لایه‌ها و فایل‌ها
2. **Key Design Decisions** — توضیح چراها (مهم‌ترین بخش برای مصاحبه):
   - Dual-layer seat protection (Redis + Postgres)
   - Waiting room design
   - Role-based access for stats
   - Materialized view trade-offs
3. **API Endpoints** — تمام endpoint ها با جدول مرتب
4. **Running** — دستورالعمل اجرا از صفر
5. **Configuration** — تنظیمات کلیدی

---

## ۶. خلاصه‌ی وضعیت پلن روز ۴

| بلوک | کار | وضعیت |
|---|---|---|
| **بلوک ۱** | Waiting room service (Redis Sorted Set) | ✅ |
| بلوک ۱ | Waiting room endpoints (join/status/admit/leave) | ✅ |
| بلوک ۱ | اتصال waiting room به seat hold | ✅ |
| **بلوک ۲** | Statistics endpoints (5 گزارش تجمیعی) | ✅ |
| بلوک ۲ | Materialized view occupancy rate + migration | ✅ |
| بلوک ۲ | Reports endpoints (occupancy + popular) | ✅ |
| بلوک ۲ | Role-based access control برای آمار | ✅ |
| **بلوک ۳** | تست‌های واحد (20/20 pass) | ✅ |
| بلوک ۳ | Worker: sweep_waiting_room + refresh_occupancy_view | ✅ |
| بلوک ۳ | README مستندسازی کامل | ✅ |

تمام موارد پلن روز ۴ با موفقیت تکمیل شد. بخش فنی پروژه اکنون کامله و از اینجا به بعد فقط مستندسازی، مرور، و آماده‌سازی دفاع باقی می‌مونه.
