# خطة: مسار المحتوى التعليمي (أحاديث وقصص الأنبياء)

**التاريخ:** 30 سبتمبر 2026
**الحالة:** مواصفة معتمدة، بانتظار التنفيذ
**الأصل:** المواصفة المقدَّمة في المحادثة + `thinking.md`

---

## 1. الهدف

إضافة **مسار محتوى** فوق مسار الخلفية الطبيعية القائم: أذكار وأحاديث وقصص
الأنبياء تُبثّ في أوقات محدّدة من اليوم، مع بوابة انتباه تمنع البثّ في
وقت الضجيج، وإيقاف تلقائي عند مقاطعة الحديث، وتراجع في السرد، وسجل
لمنع التكرار.

المحرك القائم يبقى صاحب القرار الوحيد. مسار المحتوى **متكامل معه** لا
متنافس معه.

---

## 2. الوضع الراهن: ما هو موجود وما هو جديد

| العنصر | الحالة |
|---|---|
| مسار الخلفية الطبيعية بمستوى منخفض | موجود — `cultural_calendar.py` + `period_sounds` |
| الإنقاص التلقائي | موجود — `engine.py` فرع `DUCKED` بمنحنى مربوط بالعتبة، وعمقه من `EngineConfig.duck_depth` (60/70/80) |
| كشف الكلام بالطاقة + `webrtcvad` اختياري | موجود — `vad.py`، لكن `analyze_pcm` **غير موصول بالميكروفون** |
| جدول 24 ساعة بسبع فترات | موجود — `cultural_calendar.py` |
| قفل الصلاة بأولوية مطلقة | موجود — `prayer_engine.py` |
| العبور المتقاطع والتدرّج الناعم | موجود — `real_player.py` |
| قناة مستقلة ثانية | موجودة — `ALERT_CHANNEL` لنغمة الأذان |
| **مسار المحتوى (سرد)** | **جديد** |
| **بوابة الانتباه (تأجيل فوق 50 ديسيبل)** | **جديدة** |
| **إيقاف تلقائي + تراجع 3ث + استئناف بعد 5ث** | **جديد** — لا يوجد أي بحث في الموضع اليوم |
| **سجل التشغيل والتسلسل التصاعدي** | **جديد** |
| **أزرار التشغيل والتخطي والكتم** | **جديدة** |
| **قاعدة بيانات** | **جديدة** — لا يوجد أي مخزن بيانات في المشروع |

---

## 3. ما يُرفض من المواصفة ولماذا

### 3.1Faithful to spec — the two sample scripts

`BiophilicSoundEngine` و`SmartAudioDuckingEngine` يعيدان تنفيذ قرارات
المحرك القائم، وحلقاتهما спиحية تنافس نبضة الواجهة (200 مللي ثانية) على
مشغّل الصوت نفسه. وجود صانع قرار ثانٍ يعني أن **أولوية الصلاة لم تعد
مضمونة**، وهي القاعدة الوحيدة التي بُني عليها المشروع.

**القرار:** توسيع `engine.py`، لا إضافة محرك موازٍ.

### 3.2 The dual audio tree

شجرة `audio_library/{morning,midday,...}/{ambient,effects}` تضع خلفية
طبيعة ثانية، والخلفية الطبيعية محسومة أصلاً: تسعة ملفات مولَّدة في
`assets/` وخريطة `period_sounds` في الإعدادات. شجرتان تعنيان مصدرَي حقيقة
لشيء واحد.

**القرار:** مجلد واحد للمحتوى فقط. الخلفية تبقى كما هي.

### 3.3 The per-clip aggregate columns

المواصفة تطلب `last_played_timestamp` و`play_count` كأعمدة في
`playback_history`. هما تجميع لعدد من الصفوف، وتخزينهما يعني قراءةً
وتعديلاً ثم كتابةً — ونبضة واحدة تكفي لتضارب نبضتين.

**القرار:** جدول `playback_log` بصف لكل تشغيل، والتجميعان استعلامان
SQLite. نفس الإجابات، بلا عدّاد يتقادم.

---

## 4. القرارات المعمارية

### 4.1 محتوى بلا صوت — والفصل محفوظ

`content_engine.py` يقرر ولا يلمس صوتاً. `real_content.py` ينفّذ ولا
يقرر. نفس الخط الفاصل القائم في المشروع.

### 4.2 الموضع يملكه المحرك

المحرك يجمع `position_sec` من طوابع النبضات المحقونة ويقارنه بمدة المقطع
المخزّنة. المشغّل لا يُبلّغ عن موضعه أبداً.

**لماذا:** يبقى كل مؤقّت قابلاً للاختبار بحقن الوقت بلا `sleep`، ويعمل
المساران الحقيقي والوهمي بالتطابق.

### 4.3 لا فرع ثامن في سلسلة الأولويات

`content_engine.py` **متعاون** لا منافس. `engine.py` يستدعيه في فرع
`_with_content` بعد كل فرع يُخرج خلفية. فرعان يخرجان كتماً (الصلاة والنقاش
الحامي) يتجاوزانه. الصلاة تبقى مغلقة على كل شيء.

### 4.4 الاستبدال لا الضرب في نسبة الخلفية

عند تشغيل المحتوى تُستبدل `volume_ratio` الخلفية بـ `content_ambient_ratio`.
لو ضُربت لقيمتها مع منحنى الخفض لأصبحت الخلفية صامتة تماماً: 0.20 × 0.10.

---

## 5. التفاصيل وحدةً بوحدة

### 5.1 `config.py` — كل القيم في الإعدادات

خمسة نوافذ مربوطة بـ `DayPeriod` **القائمة**، لا بنظام فترات موازٍ:

| المفتاح | الفترات | المحتوى | ملاحظة |
|---|---|---|---|
| `fajr_dhikr` | `fajr_sabah` | حديث 2-4 د | محجوب حتى ~35 د بعد الأذان بقفل الصلاة |
| `duha_wisdom` | `duha_work` | 60-90 ث | |
| `maqil_story` | `maqil` | قصة 10-20 د | تسلسل تصاعدي |
| `evening_ethic` | `samra` | 2-3 د | **لا** `maghrib_isha` — تلك فترة عبادة مكتومة |
| `night_calm` | `night_sleep` | ذكر قصير | يخضع لهدوء النوم |

حقول جديدة:

```
content_enabled: bool = True
content_gate_max_db: float = 50.0        # فوقه يؤجَّل البثّ
content_gate_max_postpones: int = 3      # بعدها تُهجر النافذة لليوم
# تأجيلٌ بعدد التكرار لا بمدة: `content_gate_delay_min` حُذف
# لأنه لم يُنفَّذ، وعدّاد الجلسات أصدق من مؤقّت.
content_rewind_sec: float = 3.0
content_resume_quiet_sec: float = 5.0
content_ambient_ratio: float = 0.20      # الخلفية تحت المحتوى (خفض 80%)
content_volume: float = 0.85
content_min_gap_min: int = 30            # لا مقطعين متتاليين
```

`content_windows` خريطة: المفتاح ← `periods`, `label`, `kind`,
`min_room_silence_sec`, `once_per_day`.

### 5.2 `audio_types.py` — توسيع تزايدي

```python
class ContentAction(str, Enum):
    NONE = "none"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    FINISHED = "finished"
```

أربعة حقول اختيارية على `PlaybackCommand` بقيم افتراضية، فمواضع الإنشاء
الحالية والاختبارات القائمة تبقى بلا تغيير:

```
content_file: Optional[str] = None
content_action: ContentAction = ContentAction.NONE
content_volume: float = 0.0
content_position_sec: float = 0.0
```

`__str__` يمتدّ ليعرض حالة المحتوى.

### 5.3 `content_engine.py` — آلة الحالات

```
IDLE ──(نافذة مستحقة + لم تُبثّ اليوم + مرشّح موجود)──┐
   │                                                    │
   ├─(الغرفة لا تهدأ)──> WAITING_QUIET ──(تجاوز 3 تأجيلات)──> POSTPONED
   │                              │
   │                        (هدوء min_room_silence_sec)
   │                              v
   └──────────────────────> PLAYING <──(5ث هدوء)── PAUSED_INTERRUPT
                                 │  ^                    │
                    (كلام)──────┘  └────(تراجع 3ث)──────┘
                                 │
                          (انتهى المقطع)
                                 v
                            FINISHED
```

- **البوابة:** `db > content_gate_max_db` → تأجيل وإعادة فحص، بحدّ
  `content_gate_max_postpones` جلسات. بعدها → `POSTPONED` وتُسجَّل
  النافذة كغير مستوفاة لليوم. (المواصفة الأولى كتبت تأجيلاً
  `content_gate_delay_min` بالمدة؛ نُفِّذ بالعدّاد لأن المدة لم
  تُربَط بالشيفرة، والحقل حُذف منها.)
- **المقاطعة:** `is_speech` → `PAUSE` مع `position -= content_rewind_sec`
  ومثبت عند الصفر.
- **الاستئناف:** 5 ثوانٍ هدوء متصل → `RESUME` من الموضع المتراجع.
- **الانتهاء:** `position >= duration` → `FINISHED` وصف في `playback_log`.
- **السقف:** `content_min_gap_min` يمنع مقطعين متتاليين في نافذة واحدة.
- `reset()` يمسح كل مؤقّت وكل حالة، حسب قاعدة المشروع.

الخلفية تبقى خافتة أثناء `PAUSED_INTERRUPT` أيضاً، نصاً للمواصفة:
«يُبقي صوت الطبيعة خافتاً في الخلفية».

### 5.4 `content_store.py` — SQLite عبر المكتبة القياسية

```sql
CREATE TABLE audio_content (
  id                   TEXT PRIMARY KEY,
  title                TEXT NOT NULL,
  file_path            TEXT NOT NULL,      -- نسبي لمجلد المكتبة
  kind                 TEXT NOT NULL,      -- hadith | story | dhikr | wisdom
  window_key           TEXT NOT NULL,
  sequence_index       INTEGER,            -- NULL = بلا ترتيب ثابت
  duration_sec         REAL NOT NULL,
  min_room_silence_sec REAL NOT NULL,
  ducking_ratio        REAL NOT NULL,
  tags                 TEXT NOT NULL DEFAULT '',
  enabled              INTEGER NOT NULL DEFAULT 1,
  added_at             TEXT NOT NULL
);

CREATE TABLE playback_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  audio_id     TEXT NOT NULL,
  window_key   TEXT NOT NULL,   -- مُنسّخ: حقيقة وقت التشغيل
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  outcome      TEXT NOT NULL,   -- completed | interrupted | skipped | abandoned
  position_sec REAL NOT NULL DEFAULT 0
);
CREATE INDEX idx_log_audio   ON playback_log(audio_id);
CREATE INDEX idx_log_started ON playback_log(started_at);
CREATE INDEX idx_log_window  ON playback_log(window_key, started_at);
```

**اختيار المقطع التالي** — مساران حسب وجود `sequence_index`:

- **مع تسلسل** (قصص الأنبياء): أدنى `sequence_index` بلا نتيجة `completed`.
  هذا هو التقدّم التصاعدي من آدم إلى النبي ﷺ.
- **بلا تسلسل** (أحاديث): الأقدم تشغيلاً، فالأقل عدداً، فالمعرّف.

**منع التكرار:** `once_per_day` يُفحص باستعلام عدّ صفوف `playback_log`
بنافذة المحتوى في اليوم الجاري.

المسار: `user_data_dir() / "content.db"` — يُضاف إلى `.gitignore`
مع `content.db-journal` و`content.db-wal`.

### 5.5 `content_library.py` — الفحص وقياس المدة

- يمسح مجلد المحتوى ويُدخل/يحدّث الصفوف.
- **قياس المدة:**
  - `wav` — ترويسة `wave` القياسية، مجانية وبلا فك ترميز.
  - `mp3` / `ogg` / `flac` — `Sound.get_length()` مرة واحدة ثم يُحرَّر
    المرجع.
- **الكاش:** المدة تُقاس عند الإضافة فقط وتُخزَّن. لا تُعاد إلا إذا تغيّر
  زمن تعديل الملف أو حجمه.
- **ملاحظة الذاكرة:** فكّ مقطع mp3 بطول 20 د يستهلك ~211 ميغابايت
  لحظياً. التخفيف: القياس عند الإضافة لا عند كل فحص، والملف يُقاس مرة
  واحدة في العمر.
- مكتبة فارغة = حالة صريحة في الواجهة، **لا صمت**.

### 5.6 `real_content.py` — غلاف `mixer.music`

مُتحقَّق منه على pygame 2.6.1: `set_pos` و`get_pos` و`pause` و`unpause`
موجودة، والقناة 0 تبقى مشغولة أثناء تشغيل `music` على القناة ذاتها.

⚠️ **`music` لا يمتلك `get_length`.** لهذا السبب لا يُحمَّل المقطع أبداً
في `Sound` إلا لقياس المدة.

**تعارض يجب حسمه:** `real_player._pg_crossfade` يلجأ إلى `mixer.music`
حين لا يكون `Channel` كائناً (بناء pygame قديمة). إن لجأ، صار `music`
مشغولاً ولن يصلح للمحتوى. الحل: `RealPlayer` يرفع `music_claimed` عند
اللجوء، و`RealContentPlayer` يستقبل مشغّل الخلفية في مُهيّئه ويتعطّل
بسبب مسجّل إن كان `music_claimed` صحيحاً.

بديل عند التعطّل: `winsound` بلا بحث في الموضع، أو سجل فقط.

### 5.7 `engine.py` — بوابة لا منطق جديد

في فرع الصلاة: `self.content.force_stop(...)` قبل الإرجاع.
في فرع النقاش الحامي: `self.content.force_pause(...)`.

كل فرع يُخرج خلفية يمرّ بـ `_with_content(cmd, ...)` بدل `self._cmd(...)`
مباشرة:

```python
def _with_content(self, cmd, frame, ts, now, period):
    cc = self.content.update(frame, ts, now, period, enabled=self.config.content_enabled)
    if cc.action is ContentAction.NONE:
        return cmd
    cmd.content_file = cc.file
    cmd.content_action = cc.action
    cmd.content_volume = cc.volume
    cmd.content_position_sec = cc.position_sec
    if cc.is_audible and not cmd.is_muted:
        cmd.volume_ratio = self.config.content_ambient_ratio
        cmd.reason = f"{cmd.reason} | {cc.reason}"
    return cmd
```

`is_audible` صحيح في `PLAYING` و`PAUSED_INTERRUPT`.

### 5.8 `vad.py` + `mic_input.py` — توصيل webrtcvad

- `MicInput` يحتفظ بآخر كتلة PCM بجانب القيمة بالدييسيبل، مع `read_pcm()`.
- `app._tick` يوجّه PCM إلى `analyze_pcm` عند وجوده، وإلا إلى
  `analyze_frame` بالقيمة اليدوية.
- الأولوية للسيناريو المثبَّت: يُستخدم `analyze_frame` دائماً حتى لا
  يتخطّى الزر مسار PCM.
- **العدّاد يقرأ `frame.db_level`** لا القيمة قبل التحليل — فما رآه
  المحرك هو ما يُعرض.
- `VadProcessor` يقبل حَقَن `_webrtc_vad` ليختبره بحificial.
- ترتيب الحسابات في `app._tick` لا يتغير، والقراران يبقيا في خيط الواجهة.

⚠️ **هذا المسار لن يُختبر على هذا الجهاز.** لا توجد عجلة py3.12 لـ
`webrtcvad`، وبناء المصدر يفشل دون أدوات Visual C++ (القيد المعروف رقم 1
في `ROADMAP.md`). الاختبارات تحقنه ببديل. ويبقى خاملاً خلف الاحتياطي
الطاقي القائم حتى تُثبَّت أدوات البناء.

### 5.9 `app.py` — لوحة المحتوى

- العنوان الحالي، الحالة، الموضع، والتالي.
- **أزرار:** «شغّل الآن» · «تخطّي» · «كتم المحتوى».
- «شغّل الآن» يتجاوز البوابة — طلب المجلس صريح.
- مسار المكتبة، عدد الملفات، «إعادة الفحص»، وزر «افتح المجلد».
- سطر في السجل لكل انتقال حالة.

### 5.10 `simulate.py` — سيناريوان جديدان

- **مقاطعة في منتصف السرد:** قصة جارية ← كلام ← تراجع 3ث ← استئناف بعد 5ث.
- **تأجيل بمجلس صاخب:** نافذة مستحقة فوق 50 ديسيبل ← ثلاث تأجيلات ←
  `POSTPONED`.

---

## 6. الإعدادات المستمرة

مفاتيح جديدة في `settings.py` بنفس التنقية النمطية:

```
content_enabled: bool = True
content_muted: bool = False
content_volume: float = 0.85   (0.0 - 1.0)
content_library_dir: str = ""  (فارغ = الافتراضي)
```

---

## 7. أسئلة مفتوحة

**موضع المكتبة:** `%LOCALAPPDATA%\ContextAudio\content` أم مجلد
`content/` في المستودع؟

- **المحلي:** ينجو من إعادة البناء، مناسب لنسخة الـ exe، ولا يُدخَل به
  شيء في git. ويحتاج المستخدم إلى فتحه مرة واحدة.
- **المستودع:** أسهل في التطوير، لكنه يُدهَر مع `git clean` ويدخل في
  الحالة القذرة.

**التوصية:** المحلي، مع إظهار المسار في الواجهة وزر «افتح المجلد».

---

## 8. المخاطر

| الخطر | الأثر | التخفيف |
|---|---|---|
| `webrtcvad` غير قابل للتثبيت هنا | مسار غير مُختبر حيّاً | حقن بديل في الاختبارات؛ الاحتياطي الطاقي يعمل |
| فكّ `mp3` طويل يستهلك ~211 ميغابايت | نبضة ذاكرة عند الإضافة | القياس مرة واحدة وتخزينه |
| `mixer.music` مشغول بالعبور المتقاطع | لا مسار للمحتوى | حارس `music_claimed` + سبب مسجّل |
| استنزاف الذاكرة بملفات طويلة | بطء التطبيق | `music` يبثّ من القرص، لا `Sound` |
| تضارب نبضتين على تجميع التاريخ | عدّاد خاطئ | التجميع استعلام لا عمود |
| فترة `maghrib_isha` مكتومة | لا محتوى في المغرب | `evening_ethic` على `samra` |

---

## 9. خطة التحقق

قبل إعلان العمل منتهياً:

```
py_compile              كل الوحدات
flake8 --select=F       نظيف
tests/test_engine.py   decision logic + آلة حالات المحتوى
tests/test_desktop.py   المخزن + المكتبة + المشغّل + webrtcvad + الواجهة
simulate.py             8 سيناريوهات
اختبار دخان للواجهة    لوحة المحتوى + زرّان + إعادة فحص
```

**اختبارات يجب أن تفشل لأسباب خاطئة** (قاعدة المشروع: «اختبار لا يستطيع
الفشل للسبب الذي أُصلح ليس اختبار ارتداد»):

- تأجيل فوق العتبة، وإقصاؤه بعدثلاثة.
- استئناف ContentAction لا يبدأ من الصفر بعد التراجع.
- `force_stop` عند الصلاة يُنهي الحالة ولا يترك مؤقّتاً معلّقاً.
- `reset()` يصفّر عدّاد الموضع وعدّاد التأجيل معاً.
- استعلام التسلسل يختار **أول غير مشغول**، لا أول صف.
- تجميع `playback_log` بعد إدراج صفين — لا صف واحد.
- حارس `music_claimed` يمنع تشغيل المحتوى عند/nodejsQJ rise.
- تفنّي مدة الملف تغيّر زمنه ← إعادة القياس.

---

## 10. ترتيب التنفيذ

1. `config.py` + `audio_types.py` — الأساسات، بلا سلوك.
2. `content_store.py` — المخطط والاستعلامات (قابلة للاختبار وحدها).
3. `content_library.py` — الفحص وقياس المدة.
4. `content_engine.py` — آلة الحالات بحقن الوقت.
5. `engine.py` — الفرع والبوابة.
6. `real_content.py` + `real_player.py` (حارس `music_claimed`).
7. `vad.py` + `mic_input.py` + `app.py` — توصيل PCM.
8. `app.py` — لوحة المحتوى والأزرار.
9. `settings.py` + `.gitignore`.
10. الاختبارات على كل مرحلة، لا في النهاية.
11. `simulate.py` + `README.md` + `ROADMAP.md`.

---

## 11. ما لن يُنفَّذ

- توليد ملفات صوتية للسرد — لا يمكن توليد صوت بشري. المكتبة تبدأ فارغة
  ويضع المستخدم ملفاته.
- محرك ثانٍ مستقل بـ `BiophilicSoundEngine`.
- شجرة خلفيات ثانية.
- نظام ملفات كامل (That is a separate future task).
- `webrtcvad` ضمن `requirements.txt` — يبقى في `requirements-optional.txt`
  لنفس سبب Visual C++.
