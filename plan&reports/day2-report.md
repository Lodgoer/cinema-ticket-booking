# گزارش کامل روز ۲ — پروژه‌ی رزرو بلیط سینما

**تاریخ:** ۱۹ تیر ۱۴۰۵ (۱۹ ژوئیه ۲۰۲۶)
**هدف روز:** لایه‌ی ادمین + مدل‌ها/مایگریشن + یادگیری Redis + Seat Hold

---

## ۱. جابه‌جایی برنامه‌ریزی‌شده: Exclusion Constraint زودتر از موعد

طبق پلن اولیه، exclusion constraint دیتابیسی برای جلوگیری از تداخل زمانی دو سانس در یک سالن قرار بود روز ۶ اضافه بشه. چون امروز مستقیم روی جدول `showtime` کار می‌شد، تصمیم گرفته شد این کار زودتر انجام بشه تا بار روزهای بعد کمتر بشه.

### ساخت migration دستی

برخلاف migration های قبلی که با `alembic revision --autogenerate` ساخته می‌شدن، این یکی باید **دستی** نوشته می‌شد، چون Alembic نمی‌تونه exclusion constraint رو از روی مدل‌های SQLAlchemy خودکار تشخیص بده:

```
alembic revision -m "add exclusion for overlapping showtimes"
```

### محتوای migration

```python
def upgrade() -> None:
    # btree_gist لازمه تا یه exclusion index بتونه یه ستون معمولی (hall_id)
    # رو کنار یه بازه‌ی زمانی (tstzrange) در یک constraint واحد مقایسه کنه
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        """
        ALTER TABLE showtime
            ADD CONSTRAINT no_overlapping_showtimes
            EXCLUDE USING gist (
                hall_id WITH =,
                tstzrange(starts_at, ends_at) WITH &&
            )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE showtime DROP CONSTRAINT no_overlapping_showtimes")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
```

**توضیح منطق:** `EXCLUDE USING gist (hall_id WITH =, tstzrange(...) WITH &&)` یعنی «هیچ دو ردیفی نباید هم `hall_id` یکسان داشته باشن **و** بازه‌ی زمانی‌شون هم‌پوشانی (`&&`) داشته باشه». این قانون کسب‌وکاری در سطح دیتابیس تضمین می‌شه، نه فقط در کد اپلیکیشن.

### مشکل جانبی: قطعی Docker

موقع اجرای `alembic upgrade head`، خطای `Connection refused` روی پورت ۵۴۳۲ ظاهر شد. علت: بعد از روشن شدن مجدد سیستم، container های Docker (`cinema-postgres`, `cinema-redis`) خودکار بالا نیومده بودن یا هنوز کامل آماده نشده بودن. با `docker compose up -d` و صبر چند ثانیه‌ای، مشکل حل شد. **نکته‌ی عملیاتی دائمی:** هر روز قبل از کار روی پروژه، باید مطمئن شد Docker Desktop باز و container ها روشن هستن.

### تایید

```sql
\d showtime
```

خروجی تایید کرد:
```
"no_overlapping_showtimes" EXCLUDE USING gist (hall_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
```

---

## ۲. لایه‌ی Pydantic Schemas (DTO Pattern)

### فلسفه

Pydantic schema ها شکل «قرارداد API» رو از شکل «مدل دیتابیس» جدا می‌کنن — دقیقاً معادل DTO/ViewModel در ASP.NET Core. کاربر نباید بتونه هنگام ساخت یک رکورد، `id` یا `created_at` را خودش تعیین کند؛ و پاسخ خروجی (`Read`) می‌تواند فیلدهایی داشته باشد که ورودی (`Create`) هرگز آن‌ها را دریافت نمی‌کند.

### الگوی کلی هر موجودیت

برای هر جدول (`Cinema`, `Hall`, `SeatType`, `Seat`, `Movie`, `Showtime`) سه کلاس نوشته شد:

```python
class CinemaBase(BaseModel):
    name: str
    city: str
    address: str | None = None

class CinemaCreate(CinemaBase):
    pass

class CinemaUpdate(BaseModel):
    name: str | None = None
    city: str | None = None
    address: str | None = None

class CinemaRead(CinemaBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
```

### نکات فنی مهم

- **`model_config = ConfigDict(from_attributes=True)`** روی همه‌ی کلاس‌های `Read`: این تنظیم به Pydantic اجازه می‌دهد مستقیماً از یک شیء SQLAlchemy (نه فقط از دیکشنری) بخواند — چون این کلاس‌ها قرار است مستقیماً از نتیجه‌ی یک کوئری ساخته شوند.
- برای `Seat` و `Showtime` عمداً کلاس `Update` نوشته نشد، چون منطقاً تغییر جزئی این دو موجودیت کمتر معنا دارد (بیشتر باید حذف و دوباره ساخته شوند).
- `HallCreate` شامل `cinema_id` است (چون هنگام ساخت باید مشخص شود مال کدام سینماست)، اما `HallUpdate` این فیلد را ندارد (چون منطقی نیست یک سالن از یک سینما به سینمای دیگر منتقل شود).

---

## ۳. لایه‌ی Repository Pattern

### فلسفه

Repository لایه‌ای بین router (endpoint) و کوئری‌های خام SQLAlchemy قرار می‌گیرد — دقیقاً همان الگویی که در FoodHero با Clean Architecture استفاده شده. مزایا: router ها تمیز باقی می‌مانند، تست‌نویسی راحت‌تر می‌شود، و منطق کوئری پیچیده یک‌جای مرکزی دارد.

### Base Repository جنریک

```python
ModelType = TypeVar("ModelType", bound=Base)

class BaseRepository(Generic[ModelType]):
    model: type[ModelType]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, id: int) -> ModelType | None:
        return await self.session.get(self.model, id)

    async def get_all(self) -> list[ModelType]:
        result = await self.session.execute(select(self.model))
        return list(result.scalars().all())

    async def create(self, **kwargs) -> ModelType:
        obj = self.model(**kwargs)
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelType, **kwargs) -> ModelType:
        for key, value in kwargs.items():
            if value is not None:
                setattr(obj, key, value)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelType) -> None:
        await self.session.delete(obj)
        await self.session.commit()
```

`Generic[ModelType]` معادل مفهومی `Repository<T>` در دات‌نت است؛ `TypeVar("ModelType", bound=Base)` معادل `where T : class` است.

### Repository های اختصاصی

هر repository فقط با تعریف `model = <کلاس>` همه‌ی متدهای پایه را رایگان به دست می‌آورد، و در صورت نیاز متدهای اختصاصی هم اضافه می‌کند:

```python
class HallRepository(BaseRepository[Hall]):
    model = Hall

    async def get_by_cinema(self, cinema_id: int) -> list[Hall]:
        result = await self.session.execute(
            select(Hall).where(Hall.cinema_id == cinema_id)
        )
        return list(result.scalars().all())
```

**نکته‌ی طراحی `update()`:** شرط `if value is not None` امکان **partial update** را فراهم می‌کند — یعنی وقتی کاربر فقط `name` را برای تغییر می‌فرستد، بقیه‌ی فیلدها دست‌نخورده می‌مانند.

---

## ۴. CRUD کامل ادمین

با استفاده از دو لایه‌ی بالا، یک فایل `routers.py` نوشته شد که شامل endpoint های استاندارد (Create, List, Get, Update, Delete) برای `Cinema`, `Hall`, `SeatType`, `Seat`, `Movie` است. الگوی هر endpoint:

```python
@router.post("/cinemas", response_model=CinemaRead, status_code=status.HTTP_201_CREATED)
async def create_cinema(data: CinemaCreate, session: AsyncSession = Depends(get_session)):
    return await CinemaRepository(session).create(**data.model_dump())
```

### منطق ویژه‌ی ساخت Showtime

اینجا دو رفتار خاص، ورای CRUD ساده، پیاده‌سازی شد:

**۱. تبدیل خطای دیتابیس به پاسخ HTTP تمیز**

به‌جای بازنویسی چک تداخل زمانی در کد پایتون (که می‌تواند دچار race condition بین دو درخواست همزمان شود)، اجازه داده می‌شود خود Postgres (با exclusion constraint) درخواست را رد کند؛ کد فقط خطای سطح پایین را می‌گیرد و به پیام قابل‌فهم تبدیل می‌کند:

```python
try:
    await session.flush()  # INSERT فرستاده می‌شود و constraint چک می‌شود،
                            # بدون commit نهایی
except IntegrityError:
    await session.rollback()
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="This hall already has an overlapping showtime at that time.",
    )
```

این رویکرد بهتر از چک صرفاً در سطح اپلیکیشن است، چون تضمین واقعی در سطح دیتابیس باقی می‌ماند و race condition بین دو ادمین که هم‌زمان دو سانس می‌سازند، رخ نمی‌دهد.

**۲. ساخت خودکار ShowtimeSeat**

لحظه‌ی ساخت هر سانس، برای هر صندلی فیزیکی همان سالن، یک ردیف `ShowtimeSeat` با وضعیت `available` ساخته می‌شود؛ قیمت (`price_snapshot`) هم از قیمت واقعی `seat_type` مربوط به همان صندلی کپی می‌شود (نه یک مقدار ثابت):

```python
seats = await seat_repo.get_by_hall(data.hall_id)
seat_type_repo = SeatTypeRepository(session)
seat_type_prices: dict[int, object] = {}

for seat in seats:
    if seat.seat_type_id not in seat_type_prices:
        seat_type = await seat_type_repo.get(seat.seat_type_id)
        seat_type_prices[seat.seat_type_id] = seat_type.price

    session.add(
        ShowtimeSeat(
            showtime_id=showtime.id,
            seat_id=seat.id,
            status="available",
            price_snapshot=seat_type_prices[seat.seat_type_id],
        )
    )
```

### باگ کوچک حین تست: JSON نامعتبر در Swagger

هنگام تست دستی ساخت چند صندلی، تلاش شد چند آبجکت JSON با کاما از هم جدا در یک باکس Swagger فرستاده شود (شبیه آرایه ولی بدون `[...]`). این یک خطای سینتکس JSON بود که باعث خطای `422 Unprocessable Entity` (`"Extra data"`) شد. راه‌حل: هر endpoint تک‌آبجکتی Swagger فقط یک شیء را در هر Execute می‌پذیرد؛ درخواست‌ها باید جداگانه فرستاده شوند.

### تست کامل زنجیره

از طریق Swagger، یک `Cinema`، یک `Hall`، یک `SeatType`، چهار `Seat`، و یک `Movie` ساخته شد. سپس:
- ساخت اولین `Showtime` → موفق (۲۰۱)، و تأیید شد که ۴ ردیف `ShowtimeSeat` با `status='available'` و `price_snapshot=10.00` خودکار ساخته شدند (پس از رفع یک اشکال جانبی که در آن صندلی‌ها به‌درستی ثبت نشده بودند).
- تلاش برای ساخت یک `Showtime` دوم با بازه‌ی زمانی هم‌پوشان در همان سالن → رد شد با ۴۰۹ Conflict و پیام `"This hall already has an overlapping showtime at that time."`

---

## ۵. احراز هویت JWT و تفکیک نقش

### نصب پکیج‌ها

```
pip install "python-jose[cryptography]" passlib[bcrypt] python-multipart
```

- `python-jose` → ساخت و decode توکن JWT
- `passlib[bcrypt]` → هش کردن پسورد
- `python-multipart` → لازم برای دریافت فرم لاگین (username/password) در FastAPI

### مسئله‌ی `requirements.txt`

پیش از این هیچ فایلی لیست پکیج‌های نصب‌شده را ذخیره نمی‌کرد — یعنی اگر کسی دیگر ریپو را کلون می‌کرد، هیچ راهی برای دانستن پکیج‌های لازم نداشت. این مشکل با ساخت `requirements.txt` حل شد:

```
pip freeze > requirements.txt
```

از این پس، هر بار پکیج جدیدی نصب می‌شود، این دستور دوباره اجرا می‌شود تا فایل همیشه به‌روز بماند. نصب کامل برای هرکس دیگر:
```
pip install -r requirements.txt
```

### ساختار `auth.py`

معادل‌های مفهومی با ASP.NET Core Identity:

| بخش کد | معادل .NET |
|---|---|
| `hash_password` / `verify_password` | `PasswordHasher` |
| `create_access_token` | صدور JWT پس از لاگین |
| `get_current_user` (Depends) | `[Authorize]` |
| `require_role(...)` (Depends) | `[Authorize(Roles = "Admin")]` |

```python
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def require_role(*allowed_roles: str):
    """برمی‌گرداند یک dependency که فقط اجازه می‌دهد اگر نقش کاربر
    یکی از allowed_roles باشد."""
    async def role_checker(user: AppUser = Depends(get_current_user)) -> AppUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of these roles: {', '.join(allowed_roles)}",
            )
        return user
    return role_checker
```

**نکته‌ی طراحی مهم:** `require_role` یک تابع factory است که خودش یک dependency می‌سازد — این الگو انعطاف بیشتری نسبت به یک دکوریتور ثابت می‌دهد، چون می‌توان با `require_role("admin", "theater_manager")` چند نقش مجاز را هم‌زمان مشخص کرد.

محافظت کل روتر ادمین در یک خط، بدون نیاز به تکرار روی هر endpoint:

```python
router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_role("admin", "theater_manager"))],
)
```

**نکته‌ی امنیتی باقی‌مانده (TODO):** در حال حاضر `SECRET_KEY = "dev-secret-change-me"` به‌صورت hardcoded در کد قرار دارد. این باید در آینده به یک متغیر محیطی (`.env`) منتقل شود؛ فعلاً به‌عنوان یادداشت در کد ثبت شده است.

### باگ کشف‌شده و رفع‌شده: ناسازگاری `passlib` و `bcrypt`

هنگام تست اولین ثبت‌نام، سرور خطای ۵۰۰ برگرداند. بررسی Traceback نشان داد:

```
(trapped) error reading bcrypt version
AttributeError: module 'bcrypt' has no attribute '__about__'
...
ValueError: password cannot be longer than 72 bytes, truncate manually if necessary
```

**علت واقعی:** پیام آخر گمراه‌کننده بود — مشکل واقعاً به طول پسورد مربوط نبود. این یک ناسازگاری شناخته‌شده بین `passlib` (که دیگر به‌طور فعال نگهداری نمی‌شود) و نسخه‌های جدید `bcrypt` (که API داخلی‌اش تغییر کرده) است. نسخه‌ی تازه‌نصب‌شده‌ی `bcrypt` (نسخه‌ی ۵) با self-test داخلی `passlib` سازگار نبود.

**راه‌حل:**
```
pip install "bcrypt==4.0.1"
pip freeze > requirements.txt
```

پس از این تغییر و ری‌استارت سرور، ثبت‌نام با موفقیت (۲۰۱) انجام شد.

### تست کامل زنجیره‌ی احراز هویت

۱. `POST /auth/register` با نقش `admin` → موفق (۲۰۱)
۲. `GET /admin/cinemas` بدون توکن → رد شد (۴۰۱ Unauthorized)
۳. `POST /auth/login` با ایمیل و پسورد صحیح → دریافت `access_token`
۴. استفاده از توکن از طریق دکمه‌ی Authorize در Swagger
۵. `GET /admin/cinemas` با توکن → موفق (۲۰۰)، لیست سینماها بازگردانده شد

---

## ۶. یادگیری متمرکز Redis: `SET NX EX`

### مفهوم

```
SET seat_hold:{showtime_id}:{seat_id} user_id NX EX 600
```

- `NX` (Not eXists) → فقط اگر کلید از قبل وجود نداشته باشد، مقدار تنظیم می‌شود
- `EX 600` → کلید پس از ۶۰۰ ثانیه (۱۰ دقیقه) خودکار منقضی و پاک می‌شود

**نکته‌ی کلیدی:** این عملیات **atomic** است — چک «آیا کلید وجود دارد» و «تنظیم مقدار» در یک عملیات غیرقابل‌تقسیم انجام می‌شود. اگر این دو مرحله جدا بودند (اول `EXISTS` سپس `SET`)، بین این دو مرحله امکان داشت یک کاربر دیگر دقیقاً همان لحظه همان صندلی را بگیرد — این دقیقاً race condition‌ای است که کل معماری seat-hold می‌خواهد از آن جلوگیری کند.

### تست عملی race condition

با دو پنجره‌ی مجزای `redis-cli` (شبیه‌سازی دو کاربر همزمان):

**پنجره ۱:**
```
SET seat_hold:5:12 user_alice NX EX 600
→ OK
```

**پنجره ۲ (بلافاصله بعد):**
```
SET seat_hold:5:12 user_bob NX EX 600
→ (nil)
```

**تأیید:**
```
GET seat_hold:5:12
→ "user_alice"
```

این تست به‌صورت زنده نشان داد که تلاش دوم (`user_bob`) نه‌تنها رد شد، بلکه هیچ تأثیری روی مقدار موجود نگذاشت — دقیقاً معادل Redis برای همان تضمینی که `UNIQUE constraint` در Postgres در روز ۱ ارائه می‌داد، با این تفاوت که این یکی موقتی (TTL-دار) و در لایه‌ای بسیار سریع‌تر است.

---

## ۷. پیاده‌سازی Endpoint های Seat Hold

### اتصال Redis

```python
# redis_client.py
import redis.asyncio as redis

REDIS_URL = "redis://localhost:6379"
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

async def get_redis() -> redis.Redis:
    return redis_client
```

`decode_responses=True` باعث می‌شود مقادیر به‌صورت رشته‌ی پایتونی برگردند، نه bytes خام.

### Endpoint ۱: گرفتن Hold

```python
@hold_router.post("/seats/{seat_id}/hold")
async def hold_seat(showtime_id, seat_id, user=Depends(get_current_user), ...):
    # ابتدا چک Postgres: اگر صندلی از قبل booked است، اصلاً نیازی
    # به رفتن سراغ Redis نیست
    if showtime_seat.status == "booked":
        raise HTTPException(status_code=409, detail="Seat is already booked")

    # چک اصلی و رقابتی: عملیات اتمیک Redis
    acquired = await r.set(
        hold_key(showtime_id, seat_id), str(user.id),
        nx=True, ex=HOLD_TTL_SECONDS,
    )
    if not acquired:
        raise HTTPException(status_code=409, detail="Seat is currently held by someone else")
```

طراحی دو‌مرحله‌ای عمدی است: ابتدا چک ارزان‌تر و قطعی‌تر (Postgres)، سپس چک رقابتی و اتمیک (Redis).

### Endpoint ۲: نمایش نقشه‌ی صندلی‌ها (ترکیب دو منبع)

```python
@hold_router.get("/seats")
async def get_seat_map(showtime_id, ...):
    for ss in showtime_seats:
        effective_status = ss.status  # 'available' یا 'booked' از Postgres
        if effective_status == "available":
            held_by = await r.get(hold_key(showtime_id, ss.seat_id))
            if held_by is not None:
                effective_status = "held"
        ...
```

این endpoint دقیقاً همان چیزی است که تصمیم روز ۱ («`held` فقط در Redis زندگی می‌کند») را عملی می‌کند: برای هر صندلی، ابتدا وضعیت قطعی از Postgres خوانده می‌شود، و فقط اگر `available` بود، چک اضافی از Redis انجام می‌شود تا مشخص شود آیا هم‌اکنون کسی آن را موقتاً گرفته است.

### Endpoint ۳: آزادسازی دستی

```python
@hold_router.delete("/seats/{seat_id}/hold")
async def release_seat_hold(showtime_id, seat_id, user=Depends(get_current_user), ...):
    held_by = await r.get(key)
    if held_by != str(user.id) and user.role not in ("admin", "theater_manager"):
        raise HTTPException(status_code=403, detail="You don't hold this seat")
    await r.delete(key)
```

فقط خود صاحب hold یا یک ادمین می‌تواند آن را آزاد کند — یک چک مالکیت در سطح اپلیکیشن، چون خود Redis مفهوم «مالکیت» را نمی‌شناسد.

**محدودیت شناخته‌شده:** بین خواندن (`GET`) و حذف (`DELETE`) در این تابع یک race condition نظری کوچک وجود دارد. چون این عملیات فقط توسط صاحب hold یا ادمین (نه به‌صورت رقابتی مثل خود عملیات hold) صدا زده می‌شود، اهمیت عملی کمی دارد؛ برای حرفه‌ای‌سازی کامل می‌توان از یک اسکریپت Lua اتمیک استفاده کرد.

### تست کامل زنجیره

۱. `GET /showtimes/4/seats` (قبل از هر hold ای) → همه‌ی ۴ صندلی `available`
۲. `POST /showtimes/4/seats/1/hold` → موفق، `held_by` و `ttl_seconds: 600` بازگردانده شد
۳. `GET /showtimes/4/seats` (دوباره) → صندلی ۱ اکنون `"held"`، بقیه هنوز `"available"`
۴. `DELETE /showtimes/4/seats/1/hold` → موفق (۲۰۴ No Content)
۵. `GET /showtimes/4/seats` (بار سوم) → صندلی ۱ بازگشته به `"available"`

همه‌ی مراحل با موفقیت تأیید شدند.

---

## ۸. Commit های امروز

دو commit جداگانه انجام شد:

**Commit اول:**
```
git commit -m "Add Pydantic schemas and repository pattern for admin entities"
```

**Commit دوم:**
```
git commit -m "Add JWT auth with role-based access, exclusion constraint for overlapping showtimes, requirements.txt"
```

**Commit سوم:**
```
git commit -m "Add Redis-based seat hold endpoints (hold/release/seat map)"
```

فایل‌های نهایی اضافه‌شده در طول روز: `schemas.py`, `repositories.py`, `routers.py`, `auth.py`, `auth_router.py`, `redis_client.py`, `hold_router.py`, `requirements.txt`, migration جدید برای exclusion constraint، و به‌روزرسانی‌های `main.py`.

**نکته‌ی جانبی:** هنگام یکی از `push` ها، خطای اتصال به گیت‌هاب (`Failed to connect to github.com port 443`) رخ داد — همان مشکل شبکه‌ای مرتبط با تحریم که در روز ۱ هم پیش آمده بود. با روشن کردن VPN و تلاش مجدد، بدون مشکل حل شد.

---

## خلاصه‌ی وضعیت پلن روز ۲

| بلوک | کار | وضعیت |
|---|---|---|
| **بلوک ۱** | مدل‌های SQLAlchemy + Alembic migration | ✅ (از روز ۱) |
| بلوک ۱ | Pydantic schemas (Create/Read/Update) | ✅ |
| بلوک ۱ | Repository pattern | ✅ |
| **بلوک ۲** | CRUD ادمین کامل | ✅ |
| بلوک ۲ | چک تداخل زمانی سانس (سطح دیتابیس، زودتر از موعد) | ✅ |
| بلوک ۲ | ساخت خودکار ShowtimeSeat | ✅ |
| بلوک ۲ | JWT auth + تفکیک نقش | ✅ |
| **بلوک ۳** | یادگیری متمرکز Redis (TTL، SET NX EX) | ✅ |
| بلوک ۳ | endpoint انتخاب صندلی | ✅ |
| بلوک ۳ | endpoint نمایش وضعیت صندلی‌ها | ✅ |
| بلوک ۳ | endpoint آزادسازی دستی | ✅ |

تمام موارد پلن روز ۲ با موفقیت و به‌صورت کامل تست‌شده به پایان رسید.
