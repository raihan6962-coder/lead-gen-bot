import requests, telebot, time, random, os, threading, re
from datetime import datetime
import pytz
from flask import Flask
from groq import Groq
from google_play_scraper import search, app as gplay
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ─── FLASK ───────────────────────────────────────────────────
web_app = Flask(__name__)

@web_app.route('/')
def home(): return "Bot is Alive!"

def run_web():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ─── CONFIG ──────────────────────────────────────────────────
SHEET_URL = "https://script.google.com/macros/s/AKfycbzI5eCCU_Gci6M0jFr5I_Ph48CqUvvP4_nkpngWtjFafVSr_i75yqKX37ZMG4qwG0_V/exec"
BOT_TOKEN = "8709829378:AAEJJQ8jm_oTyAcGenBrIfLi4KYHRVcSJbo"
GROQ_KEY  = "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"

bot = telebot.TeleBot(BOT_TOKEN)
ai  = Groq(api_key=GROQ_KEY)

# ─── STATE ───────────────────────────────────────────────────
state = {
    "status":          "IDLE",
    "generated_kws":   [],
    "kw_index":        0,
    "scraped_ids":     set(),
    "total_scraped":   0,
    "total_emailed":   0,
    "chat_id":         None,
    "tmp_url":         None,
    "tmp_email":       None,
    "tmp_test_email":  None,
    "current_set_id":  None,
    "qualified_count": 0,
    "seen_emails":     set(),
    "settings":        {},
    "kw_stats":        {},
    "ai_working":      True,
    "ai_fail_count":   0,
}

GOV = ['gov','government','ministry','department','council',
       'national','authority','federal','municipal']

AI_MODEL = "llama-3.3-70b-versatile"

# ════════════════════════════════════════════════════════════
#  CORE AI CALL — retry on rate-limit, auto-disable on hard fail
# ════════════════════════════════════════════════════════════
def call_ai(prompt, max_tokens=2000, retries=2):
    for attempt in range(retries + 1):
        try:
            r = ai.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=AI_MODEL,
                max_tokens=max_tokens,
                temperature=0.7,
            )
            state["ai_fail_count"] = 0
            state["ai_working"]    = True
            return r.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            print(f"[AI] attempt {attempt+1}: {err[:150]}")
            if "rate_limit" in err or "429" in err:
                wait = 20 * (attempt + 1)
                send(f"⏳ AI rate limit — waiting {wait}s then retrying...")
                time.sleep(wait)
                continue
            elif "organization_restricted" in err or "401" in err or "403" in err:
                send("❌ Groq API key issue. Switching to fallback mode.")
                state["ai_working"]    = False
                state["ai_fail_count"] += 1
                return None
            else:
                state["ai_fail_count"] += 1
                if attempt < retries:
                    time.sleep(5)
                    continue
                return None
    return None

# ─── KEYBOARDS ───────────────────────────────────────────────
def kb():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    s = state["status"]
    if s == "IDLE":
        m.add(KeyboardButton("🚀 Start Automation"))
        m.add(KeyboardButton("📅 Schedules"),  KeyboardButton("🔑 Keywords"))
        m.add(KeyboardButton("🧪 Spam Test"),  KeyboardButton("📧 Senders"))
        m.add(KeyboardButton("🔄 Refresh"))
    elif s in ["SCRAPING", "FILTERING", "EMAILING"]:
        m.add(KeyboardButton("🛑 Pause"), KeyboardButton("⏹️ Stop"))
    elif s == "PAUSED":
        m.add(KeyboardButton("▶️ Resume"), KeyboardButton("⏹️ Stop"), KeyboardButton("⏹️ Reset"))
    return m

def back_kb():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    m.add(KeyboardButton("🔙 Back"))
    return m

def send(text, md="Markdown"):
    if state["chat_id"]:
        try:    bot.send_message(state["chat_id"], text, parse_mode=md)
        except:
            try: bot.send_message(state["chat_id"], text)
            except: pass
    else:
        print(f"[NO CHAT_ID] {text}")

def parse_time(s):
    s = s.strip().upper()
    for f in ("%I:%M %p", "%H:%M"):
        try: return datetime.strptime(s, f).strftime("%H:%M")
        except: pass
    return None

def get_email(d):
    for field, src in [("developerEmail","dev"), ("supportEmail","support")]:
        v = str(d.get(field,'') or '').strip().lower()
        if v and '@' in v and '.' in v:
            return v, src
    for field in ["developerWebsite","privacyPolicy","developerAddress"]:
        v = str(d.get(field,'') or '')
        found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', v)
        if found: return found[0].lower(), "extracted"
    return "", "none"

# ════════════════════════════════════════════════════════════
#  SETTINGS
# ════════════════════════════════════════════════════════════
def get_settings():
    if state["settings"]:
        return state["settings"]
    try:
        r = requests.post(SHEET_URL, json={"action":"get_settings"}, timeout=20)
        if r.status_code == 200:
            state["settings"] = r.json()
            return state["settings"]
    except Exception as e:
        print(f"get_settings error: {e}")
    return {}

# ════════════════════════════════════════════════════════════
#  KEYWORD SET MANAGEMENT
# ════════════════════════════════════════════════════════════
def get_keyword_sets():
    try:
        r = requests.post(SHEET_URL, json={"action":"get_keyword_sets"}, timeout=15)
        if r.status_code == 200:
            return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        print(f"get_keyword_sets error: {e}")
    return []

def add_keyword_set(set_text):
    try:
        requests.post(SHEET_URL, json={"action":"add_keyword_set","set":set_text}, timeout=15)
    except Exception as e:
        print(f"add_keyword_set error: {e}")

def delete_keyword_set(set_id):
    try:
        requests.post(SHEET_URL, json={"action":"delete_keyword_set","id":set_id}, timeout=15)
    except Exception as e:
        print(f"delete_keyword_set error: {e}")

def mark_keyword_set_used(set_id):
    try:
        requests.post(SHEET_URL, json={"action":"mark_keyword_set_used","id":set_id}, timeout=15)
    except Exception as e:
        print(f"mark_keyword_set_used error: {e}")

def get_next_keyword_set():
    sets = get_keyword_sets()
    for s in sets:
        if s.get('status') == 'pending':
            return s.get('id'), s.get('set_text')
    return None, None

# ════════════════════════════════════════════════════════════
#  SCHEDULE MANAGEMENT
# ════════════════════════════════════════════════════════════
def get_schedule_times():
    try:
        r = requests.post(SHEET_URL, json={"action":"get_schedule_times"}, timeout=15)
        if r.status_code != 200:
            return []
        raw = r.json()
        if not isinstance(raw, list):
            return []
        cleaned = []
        for item in raw:
            t = str(item).strip()
            m = re.match(r'^(\d{1,2}):(\d{2})$', t)
            if m:
                cleaned.append(f"{int(m.group(1)):02d}:{m.group(2)}"); continue
            m = re.match(r'^(\d{1,2}):(\d{2}):\d{2}', t)
            if m:
                cleaned.append(f"{int(m.group(1)):02d}:{m.group(2)}"); continue
            m = re.search(r'(\d{1,2}):(\d{2}):\d{2}', t)
            if m:
                cleaned.append(f"{int(m.group(1)):02d}:{m.group(2)}"); continue
            try:
                fval = float(t)
                if 0.0 <= fval < 1.0:
                    total_min = round(fval * 24 * 60)
                    cleaned.append(f"{(total_min//60)%24:02d}:{total_min%60:02d}"); continue
            except: pass
            print(f"[Scheduler] Cannot parse: {repr(item)}")
        return cleaned
    except Exception as e:
        print(f"get_schedule_times error: {e}")
        return []

def add_schedule_time(time_str):
    try:
        requests.post(SHEET_URL, json={"action":"add_schedule_time","time":time_str}, timeout=15)
    except Exception as e:
        print(f"add_schedule_time error: {e}")

def delete_schedule_time(time_str):
    try:
        requests.post(SHEET_URL, json={"action":"delete_schedule_time","time":time_str}, timeout=15)
    except Exception as e:
        print(f"delete_schedule_time error: {e}")

# ════════════════════════════════════════════════════════════
#  FALLBACK KEYWORD GENERATOR — guaranteed 200 unique
# ════════════════════════════════════════════════════════════
def fallback_keywords(base):
    prefixes = [
        "best","top","new","popular","free","paid","pro","lite","simple",
        "easy","fast","smart","advanced","trending","official","trusted",
        "secure","reliable","cheap","premium","ultimate","ai","cloud","mobile",
    ]
    suffixes = [
        "app","free","pro","lite","2025","2024","online","offline","android",
        "download","latest","update","review","tutorial","guide","help","tips",
        "tricks","for beginners","expert","alternative","comparison","version",
        "beta","plus","max","mini","classic","gold","premium","no ads","ad free",
        "for android","for kids","for business","mobile","cloud","tool",
    ]
    result, seen = [], set()
    result.append(base); seen.add(base)
    for p in prefixes:
        kw = f"{p} {base}"
        if kw not in seen: seen.add(kw); result.append(kw)
    for s in suffixes:
        kw = f"{base} {s}"
        if kw not in seen: seen.add(kw); result.append(kw)
    for p in prefixes:
        for s in suffixes:
            if len(result) >= 200: break
            kw = f"{p} {base} {s}"
            if kw not in seen: seen.add(kw); result.append(kw)
        if len(result) >= 200: break
    return result[:200]

# ════════════════════════════════════════════════════════════
#  AI KEYWORD GENERATION
# ════════════════════════════════════════════════════════════
def generate_keywords_from_base(base):
    settings  = get_settings()
    kw_prompt = settings.get('keyword_prompt','Generate Google Play Store search terms for')
    send(f"🧠 AI generating 200 keywords for '{base}'...")

    prompt = f"""{kw_prompt} "{base}"

Generate 200 unique realistic search terms that users actually type into Google Play Store to find apps related to "{base}".

Requirements:
- Mix short (1-2 words) and medium (3-5 words) terms
- Include: free, pro, best, top, alternative, 2025, for android, no ads, etc.
- Include related niches, use cases, and problem-solving terms
- Comma separated list ONLY
- No numbers, no bullets, no explanations, no markdown"""

    result = call_ai(prompt, max_tokens=2500)
    if result:
        terms = []
        for t in result.replace('\n',',').split(','):
            t = re.sub(r'^\d+[\.\)\-\s]+','', t)
            t = t.replace('**','').replace('*','').replace('#','').strip()
            if 2 < len(t) < 60 and t not in terms:
                terms.append(t)
        if len(terms) >= 20:
            send(f"✅ AI generated {len(terms)} keywords.")
            return terms[:200]

    send("⚠️ AI generation failed. Using smart fallback...")
    fb = fallback_keywords(base)
    send(f"✅ Fallback generated {len(fb)} keywords.")
    return fb

# ════════════════════════════════════════════════════════════
#  SEARCH — 8 variations × 5 countries = 40 searches per keyword
# ════════════════════════════════════════════════════════════
SEARCH_TEMPLATES = [
    "{kw}", "best {kw}", "{kw} free", "{kw} app",
    "top {kw}", "new {kw}", "{kw} pro", "{kw} 2025",
]
COUNTRIES = ['us','in','gb','au','ca']

def get_search_ids_for_keyword(kw):
    raw_ids = []
    for tmpl in SEARCH_TEMPLATES:
        q = tmpl.format(kw=kw)
        for country in COUNTRIES:
            try:
                results = search(q, lang='en', country=country, n_hits=500)
                for r in results:
                    raw_ids.append(r['appId'])
                time.sleep(random.uniform(0.3, 0.6))
            except Exception as e:
                print(f"Search error '{q}' [{country}]: {e}")
                continue
    seen_kw, new_ids = set(), []
    for i in raw_ids:
        if i not in seen_kw and i not in state["scraped_ids"]:
            seen_kw.add(i)
            new_ids.append(i)
    return new_ids

# ════════════════════════════════════════════════════════════
#  FILTER
# ════════════════════════════════════════════════════════════
def is_qualified(app_dict, max_rating, max_installs, seen_emails, stats):
    dev      = str(app_dict.get('dev_name','') or '').lower()
    rating   = float(app_dict.get('rating') or 0.0)
    installs = int(app_dict.get('installs') or 0)
    email    = str(app_dict.get('email','') or '').strip().lower()

    if any(g in dev for g in GOV):
        stats["gov"] += 1;         return False, "gov"
    if rating == 0.0:
        stats["zero_rating"] += 1; return False, "zero_rating"
    if rating > max_rating:
        stats["rating"] += 1;      return False, "rating"
    if installs > max_installs:
        stats["installs"] += 1;    return False, "installs"
    if not email or '@' not in email:
        stats["no_email"] += 1;    return False, "no_email"
    if email in seen_emails:
        stats["dup"] += 1;         return False, "dup"
    stats["passed"] += 1
    return True, "passed"

def save_qualified_lead(row):
    try:
        requests.post(SHEET_URL,
            json={"action":"save_qualified_batch","rows":[row]}, timeout=15)
        return True
    except Exception as e:
        print(f"save_qualified_lead error: {e}")
        return False

# ════════════════════════════════════════════════════════════
#  PHASE 1 — SCRAPE
# ════════════════════════════════════════════════════════════
def phase1_scrape():
    cid = state["chat_id"]
    if not cid:
        print("Cannot start phase1: no chat_id"); return

    state["status"]        = "SCRAPING"
    state["ai_working"]    = True
    state["ai_fail_count"] = 0
    bot.send_message(cid, "🔄 Automation started. Use buttons below.", reply_markup=kb())

    try:
        settings     = get_settings()
        max_installs = int(str(settings.get('max_installs','500000')).replace(',','').strip())
        max_rating   = float(str(settings.get('max_rating','4.8')).strip())

        try:
            existing = requests.post(SHEET_URL, json={"action":"get_scraped_ids"}, timeout=20).json()
            state["scraped_ids"] = set(existing) if isinstance(existing, list) else set()
        except:
            state["scraped_ids"] = set()

        try:
            existing_emails = requests.post(SHEET_URL, json={"action":"get_qualified_emails"}, timeout=20).json()
            state["seen_emails"] = set(existing_emails) if isinstance(existing_emails, list) else set()
        except:
            state["seen_emails"] = set()

        set_id, base_kw = get_next_keyword_set()
        if not set_id:
            send("❌ No pending keyword sets. Add some first.")
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb()); return

        state["current_set_id"]  = set_id
        state["qualified_count"] = 0
        state["kw_stats"]        = {}

        generated = generate_keywords_from_base(base_kw)
        if not generated:
            send("❌ No keywords generated. Aborting.")
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb()); return

        state["generated_kws"] = generated
        state["kw_index"]      = 0
        state["total_scraped"] = 0

        send(f"✅ Already in DB: *{len(state['scraped_ids'])}* apps\n"
             f"Existing qualified emails: *{len(state['seen_emails'])}*\n"
             f"Starting scrape with *{len(generated)}* keywords\n"
             f"Filter: rating ≤ {max_rating} | installs ≤ {max_installs:,}")

        while state["kw_index"] < len(state["generated_kws"]):
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE": return

            kw = state["generated_kws"][state["kw_index"]]
            send(f"🔍 *KW {state['kw_index']+1}/{len(state['generated_kws'])}:* `{kw}`")

            ids = get_search_ids_for_keyword(kw)
            send(f"📦 *{len(ids)}* new apps found for `{kw}`")

            kw_count, qualified_from_kw = 0, 0
            batch_raw    = []
            filter_stats = {"gov":0,"zero_rating":0,"rating":0,"installs":0,"no_email":0,"dup":0,"passed":0}

            for app_id in ids:
                while state["status"] == "PAUSED": time.sleep(1)
                if state["status"] == "IDLE": break

                state["scraped_ids"].add(app_id)
                try:
                    d = gplay(app_id, lang='en', country='us')
                except:
                    continue

                email, esrc = get_email(d)
                rating      = float(d.get('score') or 0.0)
                raw_inst    = d.get('minInstalls') or d.get('realInstalls') or 0
                installs    = int(raw_inst) if raw_inst else 0

                app_dict = {
                    "app_id":       app_id,
                    "app_name":     str(d.get('title','Unknown')),
                    "dev_name":     str(d.get('developer','') or ''),
                    "email":        email,
                    "email_source": esrc,
                    "rating":       rating,
                    "installs":     installs,
                    "genre":        str(d.get('genre','') or ''),
                    "summary":      str(d.get('summary','') or ''),
                    "description":  str(d.get('description','') or '')[:1000],
                    "website":      str(d.get('developerWebsite','') or ''),
                    "privacy":      str(d.get('privacyPolicy','') or ''),
                    "link":         str(d.get('url','') or ''),
                    "updated":      str(d.get('updated','') or ''),
                    "keyword":      kw,
                }

                batch_raw.append(app_dict)

                qual, _ = is_qualified(app_dict, max_rating, max_installs, state["seen_emails"], filter_stats)
                if qual:
                    if save_qualified_lead(app_dict):
                        state["seen_emails"].add(email)
                        state["qualified_count"] += 1
                        qualified_from_kw += 1

                kw_count               += 1
                state["total_scraped"] += 1

                if len(batch_raw) >= 50:
                    try:
                        requests.post(SHEET_URL,
                            json={"action":"save_raw_batch","rows":batch_raw}, timeout=30)
                        send(f"💾 Batch saved | Scraped: *{state['total_scraped']}* | Qualified: *{state['qualified_count']}*")
                        batch_raw = []
                    except Exception as e:
                        print(f"Batch save error: {e}")

                time.sleep(random.uniform(0.05, 0.15))

            if batch_raw:
                try:
                    requests.post(SHEET_URL,
                        json={"action":"save_raw_batch","rows":batch_raw}, timeout=30)
                except: pass

            state["kw_stats"][kw] = {"apps": kw_count, "qualified": qualified_from_kw}
            send(f"✅ `{kw}` done — {kw_count} apps | {qualified_from_kw} qualified\n"
                 f"📊 Gov:{filter_stats['gov']} ZeroRating:{filter_stats['zero_rating']} "
                 f"HighRating:{filter_stats['rating']} HighInstalls:{filter_stats['installs']} "
                 f"NoEmail:{filter_stats['no_email']} Dup:{filter_stats['dup']} ✅Passed:{filter_stats['passed']}\n"
                 f"Total: *{state['total_scraped']}* scraped | *{state['qualified_count']}* qualified")

            state["kw_index"] += 1

        if state["status"] != "IDLE" and state["current_set_id"]:
            mark_keyword_set_used(state["current_set_id"])
            state["current_set_id"] = None
            send(f"🎉 *Phase 1 Complete!*\n"
                 f"Total scraped: *{state['total_scraped']}*\n"
                 f"Qualified leads: *{state['qualified_count']}*")

            if state["status"] == "SCRAPING" and state["qualified_count"] > 0:
                send("⏩ Auto-starting Phase 2 — sending emails...")
                state["status"] = "EMAILING"
                bot.send_message(cid, ".", reply_markup=kb())
                threading.Thread(target=phase2_email_only, daemon=True).start()
                return
            elif state["qualified_count"] == 0:
                send("⚠️ No qualified leads found.\n"
                     "Tip: Increase max\\_installs or max\\_rating in Settings sheet.")
                state["status"] = "IDLE"
                bot.send_message(cid, ".", reply_markup=kb())

        if state["status"] not in ["PAUSED","EMAILING"]:
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb())

    except Exception as e:
        state["status"] = "IDLE"
        send(f"❌ Phase 1 Error: {e}")
        bot.send_message(cid, ".", reply_markup=kb())

# ════════════════════════════════════════════════════════════
#  PHASE 2 — EMAIL
# ════════════════════════════════════════════════════════════
def phase2_email_only():
    cid = state["chat_id"]
    if not cid:
        print("Cannot start phase2: no chat_id"); return

    try:
        settings     = get_settings()
        email_prompt = settings.get('email_prompt','Write a professional cold outreach email.')

        send("📧 *Starting email phase...* Loading pending leads.")
        pending = get_pending_qualified_leads()
        if not pending:
            send("⚠️ No pending qualified leads found.")
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb()); return

        send(f"📧 *Sending to {len(pending)} qualified leads*\n⏳ 1-2 min gap between each.")
        state["total_emailed"] = 0

        for row in pending:
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE": break

            try:
                senders   = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
                available = [s for s in senders if int(s.get('sent',0)) < int(s.get('limit',1))]
            except Exception as e:
                print(f"Error fetching senders: {e}")
                time.sleep(3); continue

            if not available:
                info = "⚠️ All senders hit daily limit!\n"
                for s in senders:
                    info += f"  {s['email']}: {s.get('sent',0)}/{s.get('limit',0)}\n"
                send(info)
                state["status"] = "PAUSED"
                bot.send_message(cid, ".", reply_markup=kb()); break

            sender = available[0]
            email  = str(row.get('email',''))
            esrc   = str(row.get('email_source','dev'))

            subject, body_html = build_clean_email(row, sender['email'], email_prompt)

            try:
                r2   = requests.post(sender['url'],
                         json={"action":"send_email","to":email,"subject":subject,"body":body_html},
                         timeout=30)
                resp = r2.text.strip()
            except Exception as se:
                resp = f"Connection error: {se}"

            if resp == "Success":
                try:
                    requests.post(SHEET_URL, json={"action":"increment_sender","email":sender['email']}, timeout=15)
                    requests.post(SHEET_URL, json={"action":"mark_emailed","email":email}, timeout=15)
                except Exception as e:
                    print(f"Sheet update error: {e}")

                state["total_emailed"] += 1
                etag = {"dev":"📧","support":"📩","extracted":"📬"}.get(esrc,"📬")
                send(f"✅ *Email #{state['total_emailed']} Sent!*\n"
                     f"App: {row.get('app_name','?')}\n"
                     f"{etag} To: `{email}`\n"
                     f"Via: {sender['email']}")

                wait = random.randint(60, 120)
                send(f"⏳ Waiting *{wait}s* before next...")
                for _ in range(wait):
                    if state["status"] != "EMAILING": break
                    time.sleep(1)
            else:
                send(f"❌ Failed to `{email}`: {resp}")

        if state["status"] == "EMAILING":
            send(f"🎉 *Email Phase Complete!* Total sent: *{state['total_emailed']}*")
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb())
        elif state["status"] == "PAUSED":
            bot.send_message(cid, "⏸️ Paused during email phase.", reply_markup=kb())

    except Exception as e:
        state["status"] = "IDLE"
        send(f"❌ Email Phase Error: {e}")
        bot.send_message(cid, ".", reply_markup=kb())

def get_pending_qualified_leads():
    try:
        r = requests.post(SHEET_URL, json={"action":"get_pending_qualified_leads"}, timeout=30)
        if r.status_code == 200:
            return r.json() if isinstance(r.json(), list) else []
    except: pass
    return []

# ════════════════════════════════════════════════════════════
#  AI EMAIL BUILDER — personalized, genre-aware
# ════════════════════════════════════════════════════════════
def build_clean_email(row, sender_email, email_prompt):
    app_name    = str(row.get('app_name','Unknown App'))
    dev_name    = str(row.get('dev_name','') or '').strip()
    if not dev_name or len(dev_name) < 2 or len(dev_name) > 40:
        dev_name = "Developer"
    rating      = float(row.get('rating') or 0.0)
    genre       = str(row.get('genre','') or '')
    website_url = str(row.get('website','') or '')
    description = str(row.get('description','') or '')[:400]
    summary     = str(row.get('summary','') or '')

    # Rating context
    if rating < 3.5:
        urgency_note = f"currently rated {rating:.1f} stars — critically low"
    elif rating < 4.0:
        urgency_note = f"currently rated {rating:.1f} stars — below average"
    else:
        urgency_note = f"currently rated {rating:.1f} stars"

    # Genre-specific angle
    genre_lower = genre.lower()
    if any(w in genre_lower for w in ['finance','bank','payment','fintech','money']):
        service_angle = "Finance apps lose user trust rapidly when ratings drop."
    elif any(w in genre_lower for w in ['shopping','delivery','ecommerce','store']):
        service_angle = "Low ratings in shopping apps push customers straight to competitors."
    elif any(w in genre_lower for w in ['game','gaming','puzzle','casual']):
        service_angle = "Game ratings directly impact Play Store ranking and daily installs."
    elif any(w in genre_lower for w in ['health','fitness','medical','workout']):
        service_angle = "Health apps need strong ratings to earn user trust and retention."
    elif any(w in genre_lower for w in ['education','learning','kids','school']):
        service_angle = "Educational apps rely on ratings to gain parent and school adoption."
    else:
        service_angle = "Play Store ratings directly affect your app's visibility and downloads."

    # Personalization from website
    website_note = ""
    if website_url and "http" in website_url:
        try:
            resp = requests.get(website_url, timeout=5, headers={"User-Agent":"Mozilla/5.0"})
            text = re.sub(r'<[^>]+',' ', resp.text)
            text = re.sub(r'\s+',' ', text).strip()[:400]
            match = re.search(r'(\d[\d,]+\+?\s*(users?|downloads?|customers?|installs?))', text, re.I)
            if match:
                website_note = f"Impressive — I saw you have {match.group(0)}!"
        except: pass

    if not website_note:
        if summary and len(summary) > 15:
            website_note = f"Your app's focus on '{summary[:60]}' caught my attention."
        elif description:
            first = description.split('.')[0].strip()
            if len(first) > 15:
                website_note = f"I liked your approach: '{first[:70]}'."
        else:
            website_note = f"I came across {app_name} on the Play Store."

    prompt = f"""{email_prompt}

Write a short personalized cold outreach email to an Android app developer.

App Info:
- App Name: {app_name}
- Developer: {dev_name}
- Category: {genre}
- Rating: {urgency_note}
- Summary: {summary}
- Personalization detail: {website_note}
- Why it matters: {service_angle}

My service: I help Android developers improve Play Store ratings through genuine user review management strategies.

STRICT Rules:
1. Start EXACTLY with: Dear {dev_name},
2. Line 2: genuine personalized compliment using the personalization detail
3. Line 3-4: briefly mention the rating opportunity (NOT as an insult)
4. Line 5-6: how my service helps, one concrete benefit
5. Final line: soft CTA — invite them to reply or WhatsApp
6. MAX 150 words
7. ALL line breaks must use <br>
8. Zero markdown, zero bold, zero asterisks
9. Sign off: Abu Raihan | Play Store Review Specialist | WhatsApp: +8801902911261 | Telegram: t.me/abu_raihan69

Output — ONLY this exact format, nothing else:
SUBJECT: [subject line]
BODY: [email starting with Dear {dev_name},]"""

    result = call_ai(prompt, max_tokens=600)

    if result and "SUBJECT:" in result and "BODY:" in result:
        try:
            subject  = result.split("SUBJECT:")[1].split("BODY:")[0].strip()
            raw_body = result.split("BODY:")[1].strip()
            body     = raw_body.replace('**','').replace('*','').replace('##','').replace('#','').strip()
            body_html = body.replace('\n\n','<br><br>').replace('\n','<br>')
            unsubscribe = (f'<br><br><hr style="border:0;border-top:1px solid #eee;margin:16px 0;">'
                           f'<p style="text-align:center;font-size:11px;color:#bbb;">'
                           f'<a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me." '
                           f'style="color:#bbb;">Unsubscribe</a></p>')
            full_html = (f'<div style="font-family:Arial,sans-serif;font-size:14px;'
                         f'line-height:1.7;color:#333;max-width:600px;margin:0 auto;">'
                         f'{body_html}{unsubscribe}</div>')
            return subject, full_html
        except Exception as e:
            print(f"Email parse error: {e}")

    # Fallback email when AI fails
    fallback_body = (
        f"Dear {dev_name},<br><br>"
        f"{website_note}<br><br>"
        f"I noticed {app_name} is {urgency_note}. {service_angle}<br><br>"
        f"I help Android app developers improve their Play Store ratings through genuine "
        f"review management. I'd love to share a quick strategy that's worked for similar apps.<br><br>"
        f"Would you be open to a short chat?<br><br>"
        f"Best regards,<br>"
        f"Abu Raihan<br>"
        f"Play Store Review Specialist<br>"
        f"WhatsApp: +8801902911261<br>"
        f"Telegram: t.me/abu_raihan69"
    )
    subject     = f"Quick idea to boost {app_name}'s Play Store rating"
    unsubscribe = (f'<br><br><hr style="border:0;border-top:1px solid #eee;margin:16px 0;">'
                   f'<p style="text-align:center;font-size:11px;color:#bbb;">'
                   f'<a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me." '
                   f'style="color:#bbb;">Unsubscribe</a></p>')
    full_html   = (f'<div style="font-family:Arial,sans-serif;font-size:14px;'
                   f'line-height:1.7;color:#333;max-width:600px;margin:0 auto;">'
                   f'{fallback_body}{unsubscribe}</div>')
    return subject, full_html

# ─── SPAM TEST ────────────────────────────────────────────────
def run_spam_test_with_sender(test_email, sender):
    settings     = get_settings()
    email_prompt = settings.get('email_prompt','Write a professional cold outreach email.')
    fake_row = {
        "app_name":    "Demo Budget Tracker",
        "dev_name":    "Indie Studio",
        "rating":      3.1,
        "genre":       "Finance",
        "website":     "",
        "description": "A simple app to track daily expenses and manage personal budgets.",
        "summary":     "Personal budget tracker with smart expense categories",
    }
    subject, body = build_clean_email(fake_row, sender['email'], email_prompt)
    try:
        r2   = requests.post(sender['url'],
                 json={"action":"send_email","to":test_email,"subject":subject,"body":body},
                 timeout=30)
        resp = r2.text.strip()
        if resp == "Success":
            send(f"✅ Test sent to `{test_email}` via {sender['email']}\n📌 Subject: {subject}")
        else:
            send(f"❌ Failed: {resp}")
    except Exception as e:
        send(f"❌ Error: {e}")

def show_sender_selection(test_email):
    try:
        senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
        if not senders:
            send("❌ No senders available. Add one first.")
            bot.send_message(state["chat_id"], ".", reply_markup=kb()); return
        mk = InlineKeyboardMarkup()
        for s in senders:
            mk.add(InlineKeyboardButton(s['email'], callback_data=f"testsend_{s['email']}"))
        mk.add(InlineKeyboardButton("🔙 Cancel", callback_data="cancel_test"))
        bot.send_message(state["chat_id"], "📧 Choose a sender for the test:", reply_markup=mk)
        state["tmp_test_email"] = test_email
        state["status"]         = "WAITING_TEST_SENDER"
    except Exception as e:
        send(f"❌ Error: {e}")
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], ".", reply_markup=kb())

# ════════════════════════════════════════════════════════════
#  SCHEDULER
# ════════════════════════════════════════════════════════════
def run_scheduler():
    tz = pytz.timezone('Asia/Dhaka')
    print("⏰ Scheduler started.")
    triggered_today = {}

    while True:
        try:
            now_dt = datetime.now(tz)
            now_hm = now_dt.strftime("%H:%M")
            today  = now_dt.strftime("%Y-%m-%d")

            if state["status"] == "IDLE" and state["chat_id"]:
                times = get_schedule_times()
                print(f"[Scheduler] now={now_hm} | schedules={times}")
                for t in times:
                    if t == now_hm and triggered_today.get(t) != today:
                        triggered_today[t] = today
                        send(f"⏰ Scheduled time *{t}* — starting automation...")
                        bot.send_message(state["chat_id"], ".", reply_markup=kb())
                        threading.Thread(target=phase1_scrape, daemon=True).start()
                        break
        except Exception as e:
            print(f"[Scheduler] Error: {e}")
        time.sleep(10)

# ─── REFRESH ─────────────────────────────────────────────────
def refresh_status():
    sets    = get_keyword_sets()
    pending = [s for s in sets if s.get('status') == 'pending']
    ai_st   = "✅ Working" if state["ai_working"] else "❌ Offline (fallback mode)"
    send(f"🔄 *Status Report*\n\n"
         f"Bot: `{state['status']}`\n"
         f"AI: {ai_st}\n"
         f"Pending keyword sets: *{len(pending)}*\n"
         f"Scraped this run: *{state['total_scraped']}*\n"
         f"Qualified: *{state['qualified_count']}*\n"
         f"Emailed: *{state['total_emailed']}*")

# ─── BOT HANDLERS ────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def welcome(message):
    state["chat_id"]  = message.chat.id
    state["status"]   = "IDLE"
    state["settings"] = {}
    bot.reply_to(message,
        "👋 *Welcome Boss!*\n\n"
        "*🚀 Start Automation:* AI scrape + filter + personalized email — fully automatic.\n"
        "*📅 Schedules:* Set daily auto-start times (Dhaka timezone).\n"
        "*🔑 Keywords:* Add sets like `[crypto wallet] [travel app]` — used one by one.\n"
        "*📧 Senders:* Manage Gmail sender accounts.\n"
        "*🧪 Spam Test:* Send a test AI-generated email.\n"
        "*🔄 Refresh:* Show bot status & AI health.\n\n"
        "Use the buttons below 👇",
        parse_mode="Markdown", reply_markup=kb())

@bot.callback_query_handler(func=lambda c: True)
def callbacks(call):
    cid = call.message.chat.id
    d   = call.data

    if d == "back":
        state["status"] = "IDLE"
        bot.send_message(cid, "🔙 Main Menu.", reply_markup=kb())

    elif d == "add_sender":
        code = """function doPost(e) {
  var data = JSON.parse(e.postData.contents);
  if (data.action == "send_email") {
    try {
      GmailApp.sendEmail(data.to, data.subject, "", {htmlBody: data.body});
      return ContentService.createTextOutput("Success");
    } catch(err) { return ContentService.createTextOutput("Error: " + err); }
  }
}"""
        bot.send_message(cid, f"📝 Deploy this in Apps Script, then send the URL:\n\n`{code}`",
            parse_mode="Markdown", reply_markup=back_kb())
        state["status"] = "WAITING_URL"

    elif d.startswith("del_sender_"):
        e2 = d.split("del_sender_")[1]
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Delete", callback_data=f"cfm_sender_{e2}"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
        bot.send_message(cid, f"Delete sender *{e2}*?", parse_mode="Markdown", reply_markup=mk)

    elif d.startswith("cfm_sender_"):
        e2 = d.split("cfm_sender_")[1]
        try:
            requests.post(SHEET_URL, json={"action":"delete_sender","email":e2}, timeout=15)
            bot.send_message(cid, f"🗑️ Deleted *{e2}*", parse_mode="Markdown")
        except:
            bot.send_message(cid, "❌ Failed to delete.")

    elif d == "add_schedule":
        state["status"] = "WAITING_SCHEDULE"
        bot.send_message(cid, "⏰ Send time (*02:30 PM* or *14:30*)", reply_markup=back_kb())

    elif d.startswith("del_schedule_"):
        tm = d.split("del_schedule_")[1]
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Delete", callback_data=f"cfm_schedule_{tm}"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
        bot.send_message(cid, f"Delete schedule *{tm}*?", parse_mode="Markdown", reply_markup=mk)

    elif d.startswith("cfm_schedule_"):
        tm = d.split("cfm_schedule_")[1]
        delete_schedule_time(tm)
        bot.send_message(cid, f"🗑️ Deleted schedule *{tm}*", parse_mode="Markdown")

    elif d == "add_keyword":
        state["status"] = "WAITING_KEYWORD"
        bot.send_message(cid,
            "🔑 Send keyword sets like:\n`[crypto wallet] [travel app] [fitness tracker]`\nEach bracket = one set.",
            reply_markup=back_kb())

    elif d.startswith("del_keyword_"):
        kid = d.split("del_keyword_")[1]
        mk  = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Delete", callback_data=f"cfm_keyword_{kid}"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
        bot.send_message(cid, "Delete this keyword set?", reply_markup=mk)

    elif d.startswith("cfm_keyword_"):
        kid = d.split("cfm_keyword_")[1]
        delete_keyword_set(kid)
        bot.send_message(cid, "🗑️ Keyword set deleted.")

    elif d == "cancel":
        bot.send_message(cid, "Cancelled.")

    elif d == "cancel_test":
        state["status"]         = "IDLE"
        state["tmp_test_email"] = None
        bot.send_message(cid, "Test cancelled.", reply_markup=kb())

    elif d.startswith("testsend_"):
        sender_email = d.split("testsend_")[1]
        test_email   = state.get("tmp_test_email")
        if not test_email:
            bot.send_message(cid, "❌ No test email in memory. Start over.")
            state["status"] = "IDLE"; return
        try:
            senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
            sender  = next((s for s in senders if s['email'] == sender_email), None)
            if not sender:
                bot.send_message(cid, "❌ Sender not found.")
                state["status"] = "IDLE"; return
        except:
            bot.send_message(cid, "❌ Failed to fetch sender.")
            state["status"] = "IDLE"; return
        bot.send_message(cid, f"Sending test to *{test_email}* via {sender_email}...", parse_mode="Markdown")
        threading.Thread(target=run_spam_test_with_sender, args=(test_email, sender), daemon=True).start()
        state["status"]         = "IDLE"
        state["tmp_test_email"] = None

@bot.message_handler(func=lambda m: True)
def handle(message):
    text = message.text.strip()
    state["chat_id"] = message.chat.id

    if text == "🔙 Back":
        state["status"]         = "IDLE"
        state["tmp_url"]        = None
        state["tmp_email"]      = None
        state["tmp_test_email"] = None
        bot.reply_to(message, "🔙 Main Menu.", reply_markup=kb()); return

    if state["status"] == "WAITING_URL":
        if "script.google.com" in text:
            state["tmp_url"] = text
            state["status"]  = "WAITING_EMAIL"
            bot.reply_to(message, "✅ URL saved! Send the *email address* of this sender.",
                parse_mode="Markdown", reply_markup=back_kb())
        else:
            bot.reply_to(message, "❌ Invalid. Must be a Google Apps Script URL.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_EMAIL":
        if "@" in text:
            state["tmp_email"] = text
            state["status"]    = "WAITING_LIMIT"
            bot.reply_to(message, "✅ Email saved! Send *daily send limit* (e.g. 20).",
                parse_mode="Markdown", reply_markup=back_kb())
        else:
            bot.reply_to(message, "❌ Invalid email.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_LIMIT":
        if text.isdigit():
            try:
                requests.post(SHEET_URL, json={
                    "action":"add_sender","email":state["tmp_email"],
                    "url":state["tmp_url"],"limit":int(text)
                }, timeout=15)
                bot.reply_to(message, f"🎉 Sender *{state['tmp_email']}* added! Limit: {text}/day",
                    parse_mode="Markdown", reply_markup=kb())
            except:
                bot.reply_to(message, "❌ Failed. Check sheet connection.", reply_markup=kb())
            state["status"]    = "IDLE"
            state["tmp_url"]   = None
            state["tmp_email"] = None
        else:
            bot.reply_to(message, "❌ Send a number.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_SCHEDULE":
        p = parse_time(text)
        if p:
            add_schedule_time(p)
            bot.reply_to(message, f"✅ Schedule set for *{p}* daily (Dhaka time)!",
                parse_mode="Markdown", reply_markup=kb())
            state["status"] = "IDLE"
        else:
            bot.reply_to(message, "❌ Format: 02:30 PM or 14:30", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_KEYWORD":
        sets = re.findall(r'\[(.*?)\]', text)
        if sets:
            for s in sets:
                s = s.strip()
                if s: add_keyword_set(s)
            bot.reply_to(message, f"✅ Added {len(sets)} keyword set(s).",
                parse_mode="Markdown", reply_markup=kb())
        else:
            bot.reply_to(message, "❌ No brackets found. Example: `[crypto wallet]`",
                reply_markup=back_kb())
        state["status"] = "IDLE"; return

    elif state["status"] == "WAITING_TEST_EMAIL":
        if "@" in text:
            show_sender_selection(text)
        else:
            bot.reply_to(message, "❌ Invalid email. Try again.", reply_markup=back_kb())
        return

    # ── Main buttons ──
    if text == "🚀 Start Automation":
        if state["status"] == "IDLE":
            threading.Thread(target=phase1_scrape, daemon=True).start()

    elif text == "🛑 Pause":
        if state["status"] in ["SCRAPING","FILTERING","EMAILING"]:
            state["status"] = "PAUSED"
            bot.reply_to(message, "🛑 *Paused.* Progress saved.", reply_markup=kb())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            if state["generated_kws"] and state["kw_index"] < len(state["generated_kws"]):
                state["status"] = "SCRAPING"
            else:
                state["status"] = "EMAILING"
            bot.reply_to(message, "▶️ *Resuming...*", reply_markup=kb())

    elif text == "⏹️ Stop":
        if state["status"] in ["SCRAPING","FILTERING","EMAILING","PAUSED"]:
            state["status"]          = "IDLE"
            state["generated_kws"]   = []
            state["kw_index"]        = 0
            state["current_set_id"]  = None
            state["qualified_count"] = 0
            bot.reply_to(message, "⏹️ *Stopped.* Keyword set remains pending.", reply_markup=kb())

    elif text == "⏹️ Reset":
        state.update({
            "status":"IDLE","generated_kws":[],"kw_index":0,
            "scraped_ids":set(),"total_scraped":0,"total_emailed":0,
            "current_set_id":None,"qualified_count":0,
            "seen_emails":set(),"settings":{},"ai_working":True,"ai_fail_count":0
        })
        bot.reply_to(message, "⏹️ *Fully reset.*", reply_markup=kb())

    elif text == "🔄 Refresh":
        refresh_status()

    elif text == "📅 Schedules":
        times = get_schedule_times()
        mk    = InlineKeyboardMarkup()
        txt   = "📋 *Scheduled times (Dhaka):*\n\n"
        if not times:
            txt += "_None set._\n"
        else:
            for t in times:
                txt += f"• {t}\n"
                mk.add(InlineKeyboardButton(f"🗑️ Delete {t}", callback_data=f"del_schedule_{t}"))
        mk.add(InlineKeyboardButton("➕ Add Time", callback_data="add_schedule"))
        mk.add(InlineKeyboardButton("🔙 Back",     callback_data="back"))
        bot.reply_to(message, txt, parse_mode="Markdown", reply_markup=mk)

    elif text == "🔑 Keywords":
        sets = get_keyword_sets()
        mk   = InlineKeyboardMarkup()
        txt  = "🔑 *Keyword sets:*\n\n"
        if not sets:
            txt += "_None added._\n"
        else:
            for s in sets:
                icon = "✅" if s.get('status') == 'used' else "⏳"
                txt += f"{icon} `{s.get('set_text','')}`\n"
                if s.get('status') == 'pending':
                    mk.add(InlineKeyboardButton(
                        f"🗑️ {s.get('set_text','')[:22]}",
                        callback_data=f"del_keyword_{s.get('id')}"))
        mk.add(InlineKeyboardButton("➕ Add Set", callback_data="add_keyword"))
        mk.add(InlineKeyboardButton("🔙 Back",    callback_data="back"))
        bot.reply_to(message, txt, parse_mode="Markdown", reply_markup=mk)

    elif text == "🧪 Spam Test":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TEST_EMAIL"
            bot.reply_to(message, "📧 Send the email address you want to test with.",
                reply_markup=back_kb())

    elif text == "📧 Senders":
        try:
            senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
        except:
            bot.reply_to(message, "❌ Cannot reach Sheet.", reply_markup=kb()); return
        mk  = InlineKeyboardMarkup()
        txt = "📋 *Senders:*\n\n"
        if not senders:
            txt += "_None yet._\n"
        else:
            for i, s in enumerate(senders):
                txt += f"{i+1}. `{s.get('email')}` — {s.get('sent',0)}/{s.get('limit',0)}\n"
                mk.add(InlineKeyboardButton(f"🗑️ {s.get('email')}", callback_data=f"del_sender_{s.get('email')}"))
        mk.add(InlineKeyboardButton("➕ Add Sender", callback_data="add_sender"))
        mk.add(InlineKeyboardButton("🔙 Back",       callback_data="back"))
        bot.reply_to(message, txt, parse_mode="Markdown", reply_markup=mk)

# ─── MAIN ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Starting Lead Gen Bot...")
    threading.Thread(target=run_web,       daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    while True:
        try:
            print("🤖 Polling...")
            bot.polling(none_stop=True, interval=0, timeout=30)
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)
