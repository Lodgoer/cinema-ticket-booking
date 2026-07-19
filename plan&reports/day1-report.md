# گزارش کامل روز ۱ — پروژه‌ی رزرو بلیط سینما

**تاریخ:** ۱۸-۱۹ تیر ۱۴۰۵ (۹-۱۰ ژوئیه ۲۰۲۶)
**هدف روز:** پایه‌ریزی محیط توسعه + طراحی کامل دیتابیس

---

## ۱. راه‌اندازی Docker

### چرا Docker؟

قبل از هر چیز، باید Postgres و Redis روی سیستم اجرا می‌شدن. به‌جای نصب مستقیم هرکدوم روی ویندوز (که خصوصاً برای Redis پیچیده‌ست، چون Redis رسماً از ویندوز پشتیبانی نمی‌کنه)، از Docker استفاده کردیم.

**مفهوم پایه:**
- **Image** = یه بسته‌ی آماده و غیرقابل‌تغییر از یه نرم‌افزار (مثل یه نسخه‌ی نصب‌شده و بسته‌بندی‌شده)
- **Container** = یه نسخه‌ی در حال اجرا از یه image. از یه image می‌شه چندتا container مختلف ساخت.

### نصب و تست اولیه

Docker Desktop برای ویندوز نصب شد. تست اولیه:

```
docker run hello-world
```

این یه container تک‌مصرفی اجرا کرد که فقط یه پیام تاییدیه چاپ کرد و تموم شد — برای اطمینان از سالم بودن نصب.

### مشکل شبکه (تحریم)

موقع تلاش برای `docker pull postgres`، چندین بار با خطاهای `TLS handshake timeout` و در نهایت خطای صریح‌تر `failed to authorize: failed to fetch oauth token` مواجه شدیم. علت: **Docker Hub دسترسی IP های ایران رو مسدود کرده** (به‌خاطر تحریم‌ها). این یه محدودیت واقعی و شناخته‌شده برای دولوپرهای ایرانیه، نه مشکل فنی از سمت ما. راه‌حل: روشن کردن VPN، که بعدش دانلود موفق شد (Docker خودش لایه‌های از قبل دانلودشده رو کش می‌کنه، پس هر تلاش مجدد از صفر شروع نمی‌شد).

### تلاش اول: `docker run` دستی

ابتدا Postgres و Redis رو جدا جدا با `docker run` بالا آوردیم:

```bash
docker run --name my-postgres -e POSTGRES_PASSWORD=mysecret -p 5432:5432 -d postgres
docker run --name my-redis -p 6379:6379 -d redis
```

توضیح فلگ‌ها:
- `--name` → اسم دلخواه container
- `-e POSTGRES_PASSWORD=...` → متغیر محیطی که Postgres image ازش برای تنظیم پسورد یوزر پیش‌فرض استفاده می‌کنه
- `-p 5432:5432` → پورت مپینگ: پورت داخل container به پورت روی ویندوز وصل می‌شه
- `-d` → detached، یعنی در پس‌زمینه اجرا شه

با `psql` و `redis-cli` به هر دو وصل شدیم و تست‌های پایه (`SELECT version()`, `SET`/`GET`/`EXPIRE`/`TTL`) رو زدیم. مخصوصاً تست TTL مهم بود چون دقیقاً همون مکانیزمیه که برای «نگه‌داشتن موقت صندلی» (seat-hold) در پروژه‌ی اصلی استفاده می‌شه: یه کلید با یه عمر مشخص گذاشته می‌شه و بعد از اون مدت، Redis خودش بدون نیاز به کد اضافه پاکش می‌کنه.

### تلاش دوم و نهایی: `docker-compose.yml`

بعد از تست اولیه، container های دستی پاک شدن (`docker rm -f`) و به‌جاش یه فایل `docker-compose.yml` نوشته شد که هر دو سرویس رو با یه دستور بالا میاره — این نسخه‌ای‌ست که در نهایت توی ریپو موند:

```yaml
services:
  postgres:
    image: postgres:16
    container_name: cinema-postgres
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: mysecret
      POSTGRES_DB: cinema_db
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    container_name: cinema-redis
    ports:
      - "6379:6379"

volumes:
  postgres_data:
```

**چرا این بهتر از دستورات جدا بود؟**
- یه فایل واحد که توی گیت‌هاب می‌مونه، یعنی هرکسی (یا خود من فردا) با یه دستور (`docker compose up -d`) کل محیط رو دقیقاً همینطور بازسازی می‌کنه — بدون یادآوری دستی پورت‌ها و اسم‌ها
- `POSTGRES_DB: cinema_db` باعث شد دیتابیس پروژه خودکار موقع بالا اومدن ساخته بشه
- `volumes: postgres_data` یعنی داده‌های دیتابیس حتی با پاک شدن container از بین نمی‌ره، جای دائمی روی دیسک ذخیره می‌شه

---

## ۲. Python Virtual Environment

```
python -m venv venv
venv\Scripts\activate
pip install fastapi uvicorn[standard] sqlalchemy asyncpg alembic redis pydantic-settings
```

**چرا virtual environment؟** دقیقاً معادل مفهومی جدا نگه‌داشتن پکیج‌های هر پروژه‌ی .NET؛ هر پروژه‌ی پایتون نسخه‌ی ایزوله‌ی خودش از پکیج‌ها رو داره، بدون تداخل با پروژه‌های دیگه روی همون سیستم.

پکیج‌های نصب‌شده و نقششون:

| پکیج | نقش | معادل .NET |
|---|---|---|
| fastapi | فریم‌ورک وب | ASP.NET Core |
| uvicorn | سرور اجرا | Kestrel |
| sqlalchemy | ORM | Entity Framework Core |
| asyncpg | درایور async پستگرس | Npgsql (نسخه‌ی async) |
| alembic | ابزار migration | EF Core Migrations |
| redis | کتابخونه‌ی پایتونی Redis | StackExchange.Redis |
| pydantic-settings | مدیریت تنظیمات از .env | appsettings.json / IOptions |

نکته‌ی مهم عملیاتی: هر بار که یه پنجره‌ی ترمینال جدید باز می‌شه، `venv` غیرفعال می‌شه و باید دوباره `venv\Scripts\activate` زده بشه — این یه عادت دائمی کار روی پروژه است.

---

## ۳. اسکلت اولیه‌ی FastAPI — `/health` endpoint

یه فایل `main.py` ساده ساخته شد با یه endpoint تست:

```python
from fastapi import FastAPI
import asyncpg
import redis

app = FastAPI()

@app.get("/health")
async def health_check():
    result = {"postgres": "unknown", "redis": "unknown"}

    try:
        conn = await asyncpg.connect(
            host="localhost", port=5432,
            user="postgres", password="mysecret", database="cinema_db",
        )
        await conn.close()
        result["postgres"] = "connected"
    except Exception as e:
        result["postgres"] = f"error: {str(e)}"

    try:
        r = redis.Redis(host="localhost", port=6379)
        r.ping()
        result["redis"] = "connected"
    except Exception as e:
        result["redis"] = f"error: {str(e)}"

    return result
```

هدف: مطمئن شدن از اینکه FastAPI می‌تونه واقعاً به هر دو دیتابیس وصل بشه، قبل از رفتن سراغ کد پیچیده‌تر. بعد از رفع یه باگ کوچیک در نحوه‌ی پاس دادن connection string (اولین تلاش با یه رشته‌ی DSN بود که parse نمی‌شد؛ راه‌حل دادن پارامترهای جدا جدا بود)، هر دو کلید `"connected"` برگردوندن.

با اجرای `uvicorn main:app --reload` و باز کردن `http://127.0.0.1:8000/health` این تایید شد.

---

## ۴. راه‌اندازی گیت و گیت‌هاب

```
git init
git add .
git commit -m "Initial project setup: docker-compose for Postgres+Redis, FastAPI health check"
```

ریپازیتوری خالی روی گیت‌هاب ساخته شد: `https://github.com/Lodgoer/cinema-ticket-booking`

نکته‌ی آموزنده‌ای که پیش اومد: چون هنگام ساخت ریپو تیک "Add a README file" فراموش شد برداشته بشه، گیت‌هاب یه commit خودکار با `README.md` ساخت که با تاریخچه‌ی local (که مستقل بود) تداخل داشت. رفع شد با:

```
git remote add origin https://github.com/Lodgoer/cinema-ticket-booking.git
git fetch origin
git branch -M main
git pull origin main --allow-unrelated-histories
git push -u origin main
```

`--allow-unrelated-histories` لازم بود چون local و remote هرکدوم تاریخچه‌ی commit جدا و بی‌ربط به هم داشتن (یکی فقط README، یکی فایل‌های واقعی پروژه) و گیت به‌صورت پیش‌فرض از merge کردن دو تاریخچه‌ی کاملاً بی‌ربط جلوگیری می‌کنه، مگر صراحتاً اجازه داده بشه.

---

## ۵. طراحی دیتابیس — تصمیمات کلیدی

### فلسفه‌ی کلی: Database-first

به‌جای اینکه مستقیم برم سراغ نوشتن کلاس‌های SQLAlchemy (که ابزار خودش SQL رو تولید می‌کنه)، اول `schema.sql` خام و دستی نوشته شد. دلیل: وقتی ORM خودش SQL می‌سازه، راحت می‌شه از فکر کردن دقیق به کلید‌ها، نرمال‌سازی، ایندکس‌ها و constraint ها طفره رفت. نوشتن دستی SQL این تفکر رو اجباری می‌کنه، و چون طراحی دیتابیس دقیقاً همون مهارتیه که قراره در دفاع پروژه نشون داده بشه، این مسیر انتخاب شد.

### فهرست کامل جدول‌ها (۱۴ جدول)

`cinema`, `hall`, `seat_type`, `seat`, `movie`, `showtime`, `app_user`, `cinema_manager`, `showtime_seat`, `discount`, `booking`, `booking_seat`, `payment`, `ticket`

### تصمیم کلیدی ۱: جداسازی `Seat` (فیزیکی) از `ShowtimeSeat` (وضعیت per-showtime)

**مسئله:** یه صندلی فیزیکی (مثلاً ردیف A صندلی ۱ توی سالن ۲) برای هزاران سانس مختلف در طول زمان استفاده میشه. اگه وضعیت "رزرو شده/آزاد" رو مستقیم روی خود `Seat` نگه می‌داشتیم، اون صندلی فقط می‌تونست برای *یک* سانس در کل تاریخ، وضعیت داشته باشه — که غلطه.

**راه‌حل:** `Seat` فقط مشخصات فیزیکی و ثابت رو نگه می‌داره (ردیف، شماره، نوع صندلی). یه جدول جدا `ShowtimeSeat` برای هر ترکیب (صندلی × سانس) یه ردیف جدا داره، با وضعیت مخصوص همون سانس. این جداسازی، پایه‌ی معماری کل تضمین consistency پروژه‌ست.

### تصمیم کلیدی ۲: وضعیت `held` فقط در Redis، نه در Postgres

**مسئله:** اگه هر بار که یه کاربر یه صندلی رو موقتاً انتخاب می‌کنه (held) این توی Postgres ثبت بشه، یعنی هر کلیک کاربر یه نوشتن روی دیتابیس اصلی — که هم پرترافیکه هم برای یه وضعیت موقت و اکثراً بی‌نتیجه (خیلی از holdها هیچ‌وقت به پرداخت نمی‌رسن) هزینه‌ی زیادیه.

**راه‌حل:** ستون `status` در `showtime_seat` فقط بین `available` و `booked` سوییچ می‌کنه — نه `held`. وضعیت held به‌طور کامل در Redis زندگی می‌کنه، با یه کلید TTL-دار:

```
SET seat_hold:{showtime_id}:{seat_id} user_id NX EX 600
```

Postgres فقط در دو لحظه‌ی واقعاً مهم دست می‌خوره: وقتی پرداخت موفق میشه (→ `booked`) و وقتی رزرو لغو میشه (→ `available`). این یعنی مرز مسئولیت روشنه: Redis مالک وضعیت لحظه‌ای و پرتردده، Postgres مالک نتیجه‌ی نهایی و پایدار.

### تصمیم کلیدی ۳ (مهم‌ترین — یه باگ واقعی که در حین طراحی کشف و رفع شد)

**نسخه‌ی اولیه (دارای باگ):**

```sql
CREATE TABLE booking_seat (
    id BIGSERIAL PRIMARY KEY,
    booking_id BIGINT NOT NULL REFERENCES booking(id) ON DELETE CASCADE,
    showtime_seat_id BIGINT NOT NULL REFERENCES showtime_seat(id),
    UNIQUE (showtime_seat_id)
);
```

**مشکل:** وقتی یه `booking` لغو میشه، فقط `status` اون تغییر می‌کنه (به `cancelled`) — ردیف `booking_seat` مربوطه هرگز `DELETE` نمی‌شه (چون `ON DELETE CASCADE` فقط زمانی فعال میشه که خود `booking` واقعاً حذف بشه، نه وقتی فقط وضعیتش تغییر کنه). نتیجه: اون `UNIQUE(showtime_seat_id)` برای همیشه فعال می‌مونه، و یه صندلی که یه‌بار (حتی برای یه رزرو بعداً لغوشده) claim شده، **دیگه هیچ‌وقت** قابل رزرو نیست. این مستقیماً با ادعای اصلی پروژه («double-booking غیرممکنه») در تناقضه، چون باعث میشه صندلی‌های واقعاً آزاد قفل بمونن.

**راه‌حل نهایی:**

```sql
CREATE TABLE booking_seat (
    id BIGSERIAL PRIMARY KEY,
    booking_id BIGINT NOT NULL REFERENCES booking(id) ON DELETE CASCADE,
    showtime_seat_id BIGINT NOT NULL REFERENCES showtime_seat(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'cancelled'))
);

CREATE UNIQUE INDEX uq_active_booking_seat
    ON booking_seat (showtime_seat_id)
    WHERE status = 'active';
```

به `booking_seat` یه ستون `status` اضافه شد، و به‌جای `UNIQUE` سطح جدول، یه **partial unique index** ساخته شد که *فقط* روی ردیف‌های `active` اعمال میشه. یعنی وقتی یه رزرو لغو میشه، به‌جای حذف رکورد، `status` می‌شه `cancelled` — و چون دیگه `active` نیست، دیتابیس اجازه می‌ده یه ردیف *جدید و فعال* برای همون `showtime_seat_id` ساخته بشه.

**چرا این بهتر از حذف دستی رکورد در کد اپلیکیشن بود؟**
- تضمین در سطح دیتابیسه (نه application logic) — یعنی حتی اگه یه مسیر جدید در کد بعداً اضافه بشه و لغو رزرو رو یادش بره، غیرممکنه که دو رزرو *فعال* روی یه صندلی هم‌زمان وجود داشته باشن
- تاریخچه حفظ می‌شه: می‌شه بعداً پرسید «این صندلی قبلاً چندبار claim و cancel شده» — برای آمار و دیباگ مفیده

این constraint دقیقاً همونیه که کل تضمین «double-booking غیرممکنه» پروژه روش سوار شده، ترکیب با:

```sql
UNIQUE (showtime_id, seat_id)  -- روی خود showtime_seat
```

### سایر تصمیمات طراحی

- **`price_snapshot`** در `showtime_seat`: قیمت از `seat_type.price` در لحظه‌ی ساخت سانس کپی می‌شه، تا تغییر بعدی قیمت پایه، بلیط‌های قبلاً فروخته‌شده رو تحت تاثیر قرار نده (immutability تاریخی قیمت — الگوی رایج در سیستم‌های مالی)
- **`starts_at`/`ends_at`** به‌صورت صریح ذخیره می‌شن (نه محاسبه‌شده از `movie.duration_minutes`)، تا اگه بعداً مدت‌زمان یه فیلم اصلاح بشه، سانس‌های گذشته بی‌صدا جابه‌جا نشن
- **`idempotency_key UNIQUE`** روی `payment`: جلوگیری از دوبار کسر پول در صورت تکرار یه request (مثلاً به‌خاطر retry شبکه)
- **`ticket.booking_seat_id UNIQUE`**: هر صندلی بلیط QR جدا داره (نه یه بلیط مشترک برای کل booking) — چون هر صندلی جدا دم در اسکن میشه
- **Exclusion constraint برای جلوگیری از تداخل سانس‌ها** (کامنت‌شده در schema، برای فعال‌سازی در روز ۲ اختیاریه):
```sql
-- ALTER TABLE showtime ADD CONSTRAINT no_overlapping_showtimes
--     EXCLUDE USING gist (hall_id WITH =, tstzrange(start_time, end_time) WITH &&);
```
- **Reference flow لغو رزرو** (مستندشده به‌عنوان کامنت در schema، برای پیاده‌سازی در روز ۴/۵): پنج مرحله (قفل ردیف booking با `SELECT ... FOR UPDATE`، چک idempotency، آپدیت booking، آپدیت booking_seat، آزادسازی showtime_seat) همه در یک تراکنش، تا کرش وسط عملیات هیچ‌وقت باعث ناهماهنگی بین جدول‌ها نشه.

---

## ۶. تست واقعی روی دیتابیس

`schema.sql` با موفقیت روی `cinema_db` اجرا شد (۱۴ جدول + ۷ ایندکس، بدون خطا). سپس با داده‌ی واقعی، رفتار constraint اصلی تست شد:

1. یه `cinema`, `hall`, `seat`, `movie`, `showtime`, `showtime_seat`, `app_user` ساخته شد
2. **booking اول** صندلی رو claim کرد → موفق
3. **booking دوم** سعی کرد همون صندلی رو claim کنه → با خطای `duplicate key value violates unique constraint "uq_active_booking_seat"` رد شد (یعنی تضمین درست کار کرد)
4. booking اول لغو شد (`UPDATE booking_seat SET status = 'cancelled' ...`)
5. booking دوم دوباره تلاش کرد → این‌بار **موفق** شد (چون صندلی واقعاً آزاد شده بود)

این چرخه‌ی کامل، اثبات عملی و زنده‌ایه از اینکه معماری consistency پروژه واقعاً کار می‌کنه — نه فقط تئوری روی کاغذ.

---

## ۷. رسم ERD

از روی `schema.sql`، یه نسخه‌ی معادل به زبان DBML نوشته شد (`schema.dbml`) و در **dbdiagram.io** بارگذاری شد تا دیاگرام بصری کامل ۱۴ جدول و روابطشون رسم بشه. این ابزار مستقیم قابل export به PNG/PDF هست، برای استفاده در پرزنتیشن دفاع پروژه.

---

## ۸. مدل‌های SQLAlchemy

### ساختار فایل‌ها

- `database.py` → تنظیمات اتصال (Engine, Session, Base)
- `models.py` → ۱۴ کلاس پایتونی معادل ۱۴ جدول

### `database.py`

```python
from datetime import datetime
from sqlalchemy import BigInteger, DateTime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "postgresql+asyncpg://postgres:mysecret@localhost:5432/cinema_db"

engine = create_async_engine(DATABASE_URL, echo=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    type_annotation_map = {
        int: BigInteger,
        datetime: DateTime(timezone=True),
    }
```

نکات مهم:
- پیشوند `postgresql+asyncpg` (نه فقط `postgresql`) به SQLAlchemy می‌گه از درایور async استفاده کنه
- `echo=True` باعث می‌شه هر SQL که SQLAlchemy پشت صحنه تولید می‌کنه، در ترمینال چاپ بشه — برای یادگیری خیلی مفیده که ببینی کد پایتون دقیقاً چه SQL ای می‌سازه
- `type_annotation_map` یه نگاشت سراسریه: هر `Mapped[int]` پیش‌فرض می‌شه `BIGINT` (چون بیشتر ستون‌های عددی در schema، شناسه‌ها بودن)، و هر `Mapped[datetime]` می‌شه `TIMESTAMPTZ`

### الگوی کلی هر کلاس مدل (مثال: `Hall`)

```python
class Hall(Base):
    __tablename__ = "hall"
    __table_args__ = (UniqueConstraint("cinema_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cinema_id: Mapped[int] = mapped_column(ForeignKey("cinema.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    capacity: Mapped[int] = mapped_column(Integer)

    cinema: Mapped["Cinema"] = relationship(back_populates="halls")
    seats: Mapped[list["Seat"]] = relationship(back_populates="hall")
```

معادل‌های مفهومی با EF Core:

| EF Core | SQLAlchemy |
|---|---|
| `[Key]` | `mapped_column(primary_key=True)` |
| `[ForeignKey]` | `ForeignKey("table.column", ondelete=...)` |
| Navigation Property | `relationship(back_populates=...)` |
| Fluent API constraints | `__table_args__` |

### باگ کشف‌شده و رفع‌شده: تفاوت نوع داده بین models.py و schema.sql

بعد از نوشتن اولیه‌ی مدل‌ها، دستور زیر برای تولید خودکار migration زده شد:

```
alembic revision --autogenerate -m "Initial schema"
```

Alembic به‌جای یه migration خالی، لیست بلندی از تغییرات ناخواسته گزارش داد، مثل:

```
Detected type change from BIGINT() to Integer() on 'app_user.id'
Detected type change from TIMESTAMP(timezone=True) to DateTime() on 'app_user.created_at'
```

**علت (دور اول):** چون در نگارش اولیه‌ی `models.py`، نوع دقیق ستون‌ها مشخص نشده بود (فقط `Mapped[int]` و `Mapped[datetime]`)، SQLAlchemy به‌صورت پیش‌فرض `Integer` معمولی (نه `BigInteger`) و `DateTime` بدون timezone تولید می‌کرد — که با schema واقعی (`BIGINT`, `TIMESTAMPTZ`) فرق داشت.

**رفع دور اول:** اضافه کردن `type_annotation_map` در `Base` (بالا نشون داده شد) که سراسری `int → BigInteger` و `datetime → DateTime(timezone=True)` رو تنظیم می‌کنه.

**مشکل دور دوم (پس از رفع اول):** چون `type_annotation_map` سراسریه، حالا *همه‌ی* ستون‌های `int` به `BigInteger` تبدیل شدن — ولی بعضی ستون‌ها در schema اصلی از نوع `INT` معمولی بودن (نه `BIGINT`)، چون هیچ‌وقت مقدار بزرگی نمی‌گیرن: `hall.capacity`, `seat.seat_number`, `movie.duration_minutes`, `movie.release_year`, `discount.max_uses`, `discount.used_count`.

**رفع دور دوم:** برای این ۶ ستون خاص، صراحتاً `mapped_column(Integer)` نوشته شد تا نوع پیش‌فرض سراسری override بشه:

```python
capacity: Mapped[int] = mapped_column(Integer)
```

بعد از این دو دور اصلاح، `alembic revision --autogenerate` دیگه هیچ `Detected type change` گزارش نکرد — یعنی مدل‌های پایتونی دقیقاً منطبق با `schema.sql` اصلی شدن.

**نکته‌ی مهم برای دفاع پروژه:** این تجربه نشون میده وقتی مسیر database-first طی میشه، Alembic autogenerate عملاً به‌عنوان یه لایه‌ی safety net عمل می‌کنه که تفاوت‌های نامحسوس بین کد و دیتابیس واقعی رو قبل از اجرا آشکار می‌کنه.

### حل مورد partial unique index در SQLAlchemy

چون SQLAlchemy راه مستقیمی برای تعریف partial unique constraint نداره، از `Index` با پرچم `unique=True` و پارامتر `postgresql_where` استفاده شد:

```python
class BookingSeat(Base):
    __tablename__ = "booking_seat"
    __table_args__ = (
        Index("idx_booking_seat_booking", "booking_id"),
        CheckConstraint("status IN ('active', 'cancelled')"),
        Index(
            "uq_active_booking_seat",
            "showtime_seat_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
```

این دقیقاً معادل پایتونی همون خط SQL دستی است که در `schema.sql` نوشته شده بود.

---

## ۹. اولین Alembic Migration

### مراحل راه‌اندازی

```
alembic init alembic
```

سپس دو تنظیم لازم بود:

**۱. در `alembic/env.py`:**
```python
from database import Base
from models import *

target_metadata = Base.metadata
```
این به Alembic می‌گه متادیتای همه‌ی کلاس‌هایی که از `Base` ارث می‌برن رو در نظر بگیره — معادل مفهومی چیزی که EF Core خودش پشت‌صحنه با `DbContext` انجام می‌ده، ولی اینجا باید صریح بیان بشه.

**۲. در `alembic.ini`:**
```ini
sqlalchemy.url = postgresql+psycopg2://postgres:mysecret@localhost:5432/cinema_db
```
نکته: اینجا از درایور `psycopg2` استفاده شد (نه `asyncpg`)، چون Alembic به‌طور پیش‌فرض به‌صورت sync کار می‌کنه، نه async. پکیج جدا نصب شد: `pip install psycopg2-binary`.

### روند ساخت و اجرای migration

بعد از دو دور اصلاح type mismatch (بالا توضیح داده شد)، migration نهایی فقط شامل یه خط بود:

```python
def upgrade() -> None:
    op.create_index('idx_payment_booking', 'payment', ['booking_id'], unique=False)
```

این نشون می‌ده تقریباً همه‌چی بین `models.py` و دیتابیس واقعی (که قبلاً با `schema.sql` دستی ساخته شده بود) هماهنگ بود؛ تنها چیزی که کم بود یه ایندکس بود که در نگارش اولیه‌ی `models.py` جا افتاده بود.

اجرا شد با:

```
alembic upgrade head
alembic current
```

خروجی نهایی تایید کرد دیتابیس روی revision صحیح (`b3edeb3dad3d`) نشسته.

---

## ۱۰. Commit نهایی

فایل‌های زیر به گیت‌هاب اضافه شدن:

- `schema.sql` — SQL خام کامل
- `schema.dbml` — نسخه‌ی معادل برای dbdiagram.io
- `database.py`, `models.py` — لایه‌ی SQLAlchemy
- `alembic.ini`, `alembic/` — تنظیمات و migration ها

```
git add .
git commit -m "Add SQLAlchemy models and first Alembic migration"
git push
```

---

## خلاصه‌ی وضعیت پلن روز ۱

| بخش پلن | وضعیت |
|---|---|
| نصب Docker + docker-compose + تست اتصال | ✅ انجام شد |
| virtual environment + پکیج‌ها | ✅ انجام شد |
| اسکلت FastAPI با `/health` | ✅ انجام شد |
| ریسرچ (مقالات Ticketmaster/BookMyShow/Airbnb) | ⬜ باقی مانده |
| لیست موجودیت‌ها + تصمیمات کلیدی طراحی | ✅ انجام شد (فراتر از حد پلن) |
| `UNIQUE`/constraint اصلی + تست واقعی | ✅ انجام شد |
| رسم ERD | ✅ انجام شد |
| مدل‌های SQLAlchemy | ✅ انجام شد |
| اولین Alembic migration | ✅ انجام شد |

تنها آیتم باقی‌مانده از کل پلن روز ۱، بخش ریسرچ مقالاته که به بعد موکول شد.
