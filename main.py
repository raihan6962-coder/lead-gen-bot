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
    "status":         "IDLE",
    "generated_kws":  [],
    "kw_index":       0,
    "scraped_ids":    set(),
    "total_scraped":  0,
    "total_emailed":  0,
    "chat_id":        None,
    "tmp_url":        None,
    "tmp_email":      None,
    "tmp_test_email": None,
    "current_set_id": None,
    "qualified_count":0,
    "seen_emails":    set(),
    "settings":       {},
}

GOV = ['gov','government','ministry','department','council',
       'national','authority','federal','municipal']

# ─── KEYBOARDS ───────────────────────────────────────────────
def kb():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    s = state["status"]
    if s == "IDLE":
        m.add(KeyboardButton("🚀 Start Automation"))
        m.add(KeyboardButton("📅 Schedules"),        KeyboardButton("🔑 Keywords"))
        m.add(KeyboardButton("🧪 Spam Test"),        KeyboardButton("📧 Senders"))
        m.add(KeyboardButton("🔄 Refresh"))
    elif s in ["SCRAPING", "FILTERING", "EMAILING"]:
        m.add(KeyboardButton("🛑 Pause"),  KeyboardButton("⏹️ Stop"))
    elif s == "PAUSED":
        m.add(KeyboardButton("▶️ Resume"),  KeyboardButton("⏹️ Stop"),  KeyboardButton("⏹️ Reset"))
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
#  FETCH SETTINGS (cached for the run)
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
        requests.post(SHEET_URL, json={"action":"add_keyword_set", "set":set_text}, timeout=15)
    except Exception as e:
        print(f"add_keyword_set error: {e}")

def delete_keyword_set(set_id):
    try:
        requests.post(SHEET_URL, json={"action":"delete_keyword_set", "id":set_id}, timeout=15)
    except Exception as e:
        print(f"delete_keyword_set error: {e}")

def mark_keyword_set_used(set_id):
    try:
        requests.post(SHEET_URL, json={"action":"mark_keyword_set_used", "id":set_id}, timeout=15)
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
        if r.status_code == 200:
            return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        print(f"get_schedule_times error: {e}")
    return []

def add_schedule_time(time_str):
    try:
        requests.post(SHEET_URL, json={"action":"add_schedule_time", "time":time_str}, timeout=15)
    except Exception as e:
        print(f"add_schedule_time error: {e}")

def delete_schedule_time(time_str):
    try:
        requests.post(SHEET_URL, json={"action":"delete_schedule_time", "time":time_str}, timeout=15)
    except Exception as e:
        print(f"delete_schedule_time error: {e}")

# ════════════════════════════════════════════════════════════
#  AI KEYWORD GENERATION with CLEAN FALLBACK
# ════════════════════════════════════════════════════════════
def fallback_keywords(base):
    """Generate exactly 200 unique, realistic search keywords without random numbers."""
    templates = [
        "{base}", "best {base}", "top {base}", "new {base}", "{base} app",
        "{base} free", "{base} pro", "{base} lite", "{base} 2025", "popular {base}",
        "{base} for android", "{base} download", "{base} latest", "{base} update",
        "{base} reviews", "{base} problems", "{base} complaints",
        "apps like {base}", "similar to {base}", "{base} alternative",
        "best {base} apps", "top rated {base}", "{base} version",
        "{base} online", "{base} offline", "{base} premium", "{base} paid",
        "{base} rating", "{base} store", "{base} guide", "{base} tutorial",
        "{base} help", "{base} support", "{base} community", "{base} forum",
        "top 10 {base}", "best {base} 2025", "new {base} apps", "trending {base}",
        "{base} for beginners", "{base} expert", "{base} pro version",
        "{base} tips", "{base} tricks", "{base} hacks", "{base} secrets",
        "{base} features", "{base} comparison", "{base} vs", "{base} alternatives",
        "{base} review", "{base} ratings", "{base} score", "{base} installs",
        "{base} users", "{base} feedback", "{base} suggestions",
        "{base} issues", "{base} bugs", "{base} crash", "{base} fix",
        "{base} solution", "{base} workaround",
        "{base} news", "{base} blog", "{base} official", "{base} website",
        "{base} login", "{base} signup", "{base} account", "{base} profile",
        "{base} settings", "{base} preferences", "{base} options",
        "{base} dark mode", "{base} light mode", "{base} theme",
        "{base} widget", "{base} shortcut", "{base} launcher",
        "{base} icon pack", "{base} wallpaper", "{base} background",
        "{base} notification", "{base} sound", "{base} ringtone",
        "{base} alarm", "{base} timer", "{base} stopwatch",
        "{base} calculator", "{base} converter", "{base} translator",
        "{base} dictionary", "{base} thesaurus", "{base} encyclopedia",
        "{base} game", "{base} quiz", "{base} puzzle", "{base} challenge",
        "{base} multiplayer", "{base} single player", "{base} offline game",
        "{base} online game", "{base} strategy", "{base} action",
        "{base} adventure", "{base} role playing", "{base} simulation",
        "{base} sports", "{base} racing", "{base} fighting",
        "{base} card game", "{base} board game", "{base} word game",
        "{base} trivia", "{base} knowledge", "{base} education",
        "{base} learning", "{base} course", "{base} training",
        "{base} certification", "{base} exam", "{base} test",
        "{base} practice", "{base} flashcard",
        "free {base}", "paid {base}", "cheap {base}", "expensive {base}",
        "simple {base}", "easy {base}", "advanced {base}", "powerful {base}",
        "fast {base}", "secure {base}", "reliable {base}", "trusted {base}",
        "official {base}", "original {base}", "genuine {base}",
        "{base} by google", "{base} by microsoft", "{base} by amazon",
        "google {base}", "microsoft {base}", "amazon {base}",
        "{base} for business", "{base} for personal", "{base} for work",
        "{base} for study", "{base} for kids", "{base} for adults",
        "{base} with ads", "{base} no ads", "{base} ad free",
        "{base} in english", "{base} in spanish", "{base} in hindi",
        "{base} 2024", "{base} 2023", "{base} old version",
        "{base} previous version", "{base} classic", "{base} retro",
        "get {base}", "install {base}", "use {base}", "try {base}",
        "{base} demo", "{base} trial", "{base} sample", "{base} preview",
        "{base} beta", "{base} alpha", "{base} stable",
        "{base} community edition", "{base} professional",
        "{base} ultimate", "{base} premium", "{base} gold",
        "{base} plus", "{base} extra", "{base} max",
        "{base} mini", "{base} micro", "{base} nano",
        "smart {base}", "intelligent {base}", "ai {base}", "artificial intelligence {base}",
        "machine learning {base}", "deep learning {base}", "neural {base}",
        "blockchain {base}", "crypto {base}", "bitcoin {base}",
        "cloud {base}", "web {base}", "mobile {base}",
        "desktop {base}", "pc {base}", "mac {base}", "linux {base}",
        "windows {base}", "ios {base}", "android {base}"
    ]
    result = []
    seen = set()
    i = 0
    while len(result) < 200:
        tmpl = templates[i % len(templates)]
        keyword = tmpl.format(base=base)
        keyword = re.sub(r'\s+', ' ', keyword).strip()
        if 2 < len(keyword) < 60 and keyword not in seen:
            seen.add(keyword)
            result.append(keyword)
        i += 1
    return result[:200]

def generate_keywords_from_base(base):
    """Use Groq with the keyword prompt from settings. Fallback if fails."""
    settings = get_settings()
    kw_prompt = settings.get('keyword_prompt', 'Generate Play Store search terms for')
    send(f"🧠 Generating 200 keywords from '{base}' using prompt: {kw_prompt[:50]}...")
    prompt = f"""{kw_prompt} "{base}"
Return 200 unique, short search terms (2-5 words each) that people might type into Google Play Store.
Comma separated list only. No numbers, no bullets, no explanations."""
    try:
        r = ai.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=2000
        )
        raw = r.choices[0].message.content.replace('\n',',').replace('\r',',')
        terms = []
        for t in raw.split(','):
            t = re.sub(r'^\d+[\.\)\-\s]+','', t)
            t = t.replace('**','').replace('*','').replace('#','').strip()
            if 2 < len(t) < 60 and t not in terms:
                terms.append(t)
        if len(terms) < 10:
            send("⚠️ AI returned too few keywords. Using fallback.")
            terms = fallback_keywords(base)
        else:
            terms = terms[:200]
        send(f"✅ Generated {len(terms)} keywords.")
        return terms
    except Exception as e:
        send(f"❌ Groq failed: {e}. Using fallback generator.")
        fallback = fallback_keywords(base)
        send(f"✅ Generated {len(fallback)} fallback keywords.")
        return fallback

# ════════════════════════════════════════════════════════════
#  FILTER A SINGLE APP
# ════════════════════════════════════════════════════════════
def is_qualified(app_dict, max_rating, max_installs, seen_emails):
    dev = str(app_dict.get('dev_name','') or '').lower()
    rating = float(app_dict.get('rating') or 0.0)
    installs = int(app_dict.get('installs') or 0)
    email = str(app_dict.get('email','') or '').strip().lower()

    if any(g in dev for g in GOV):
        return False, "gov"
    if rating == 0.0:
        return False, "zero_rating"
    if rating > max_rating:
        return False, "rating"
    if installs > max_installs:
        return False, "installs"
    if not email or '@' not in email:
        return False, "no_email"
    if email in seen_emails:
        return False, "dup"
    return True, "passed"

# ════════════════════════════════════════════════════════════
#  SAVE A SINGLE QUALIFIED LEAD
# ════════════════════════════════════════════════════════════
def save_qualified_lead(row):
    try:
        requests.post(SHEET_URL,
            json={"action":"save_qualified_batch","rows":[row]},
            timeout=15)
        return True
    except Exception as e:
        print(f"Error saving qualified lead: {e}")
        return False

# ════════════════════════════════════════════════════════════
#  PHASE 1 — SCRAPE (simplified for speed and yield)
# ════════════════════════════════════════════════════════════
def phase1_scrape():
    cid = state["chat_id"]
    if not cid:
        print("Cannot start phase1: no chat_id")
        return
    state["status"] = "SCRAPING"
    bot.send_message(cid, "🔄 Automation started. Use buttons below.", reply_markup=kb())

    try:
        settings = get_settings()
        max_installs = int(str(settings.get('max_installs','100000')).replace(',','').strip())
        max_rating   = float(str(settings.get('max_rating','4.5')).strip())

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
            bot.send_message(cid, ".", reply_markup=kb())
            return

        state["current_set_id"] = set_id
        state["qualified_count"] = 0

        generated = generate_keywords_from_base(base_kw)
        if not generated:
            send("❌ No keywords generated. Aborting.")
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb())
            return

        state["generated_kws"] = generated
        state["kw_index"] = 0
        state["total_scraped"] = 0

        send(f"✅ Already in DB: *{len(state['scraped_ids'])}* apps\n"
             f"Already qualified emails: *{len(state['seen_emails'])}*\n"
             f"Starting scrape with {len(generated)} keywords.")

        # SIMPLIFIED search variations – just 4 high‑value ones to get 150‑200 apps per keyword
        search_variations = [
            "{kw}",
            "best {kw}",
            "top {kw}",
            "new {kw}"
        ]

        while state["kw_index"] < len(state["generated_kws"]):
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE":  # Stop pressed
                return

            kw = state["generated_kws"][state["kw_index"]]
            send(f"🔍 *KW {state['kw_index']+1}/{len(state['generated_kws'])}:* `{kw}`")

            raw_ids = []
            for q_template in search_variations:
                q = q_template.format(kw=kw)
                try:
                    # Use n_hits=500 to get maximum results
                    results = search(q, lang='en', country='us', n_hits=500)
                    for r in results: raw_ids.append(r['appId'])
                    # Minimal delay: 0.5-1 second
                    time.sleep(random.uniform(0.5, 1.0))
                except Exception as e:
                    print(f"Search error for '{q}': {e}")
                    continue

            seen_kw, ids = set(), []
            for i in raw_ids:
                if i not in seen_kw and i not in state["scraped_ids"]:
                    seen_kw.add(i)
                    ids.append(i)

            send(f"📦 *{len(ids)}* new apps to fetch for `{kw}`")

            kw_count = 0
            batch_raw = []
            qualified_from_kw = 0

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
                dev         = str(d.get('developer','') or '')
                title       = str(d.get('title','Unknown'))
                description = str(d.get('description','') or '')[:1000]
                summary     = str(d.get('summary','') or '')
                genre       = str(d.get('genre','') or '')
                website     = str(d.get('developerWebsite','') or '')
                updated     = str(d.get('updated','') or '')
                url         = str(d.get('url','') or '')
                privacy     = str(d.get('privacyPolicy','') or '')

                app_dict = {
                    "app_id":      app_id,
                    "app_name":    title,
                    "dev_name":    dev,
                    "email":       email,
                    "email_source":esrc,
                    "rating":      rating,
                    "installs":    installs,
                    "genre":       genre,
                    "summary":     summary,
                    "description": description,
                    "website":     website,
                    "privacy":     privacy,
                    "link":        url,
                    "updated":     updated,
                    "keyword":     kw
                }

                batch_raw.append(app_dict)

                qual, reason = is_qualified(app_dict, max_rating, max_installs, state["seen_emails"])
                if qual:
                    if save_qualified_lead(app_dict):
                        state["seen_emails"].add(email)
                        state["qualified_count"] += 1
                        qualified_from_kw += 1

                kw_count += 1
                state["total_scraped"] += 1

                if len(batch_raw) >= 50:
                    try:
                        requests.post(SHEET_URL,
                            json={"action":"save_raw_batch","rows":batch_raw},
                            timeout=30)
                        send(f"💾 Saved raw batch | Total scraped: *{state['total_scraped']}*")
                        batch_raw = []
                    except Exception as e:
                        print(f"Batch save error: {e}")

                # Fast delay: 0.1-0.3 seconds
                time.sleep(random.uniform(0.1, 0.3))

            if batch_raw:
                try:
                    requests.post(SHEET_URL,
                        json={"action":"save_raw_batch","rows":batch_raw},
                        timeout=30)
                except: pass

            send(f"✅ KW `{kw}` done — {kw_count} apps scraped, {qualified_from_kw} qualified\n"
                 f"Total scraped: *{state['total_scraped']}*, Total qualified so far: *{state['qualified_count']}*")

            state["kw_index"] += 1

        if state["status"] != "IDLE" and state["current_set_id"]:
            mark_keyword_set_used(state["current_set_id"])
            state["current_set_id"] = None
            send(f"🎉 *Phase 1 Complete!* Total scraped: *{state['total_scraped']}*, Qualified leads: *{state['qualified_count']}*")

            if state["status"] == "SCRAPING" and state["qualified_count"] > 0:
                send("⏩ Automatically starting Phase 2 (Emailing qualified leads)...")
                state["status"] = "EMAILING"
                bot.send_message(cid, ".", reply_markup=kb())
                threading.Thread(target=phase2_email_only, daemon=True).start()
                return
            elif state["qualified_count"] == 0:
                send("⚠️ No qualified leads found. Add more keywords or adjust filters.")
                state["status"] = "IDLE"
                bot.send_message(cid, ".", reply_markup=kb())

        if state["status"] != "PAUSED":
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb())

    except Exception as e:
        state["status"] = "IDLE"
        send(f"❌ Phase 1 Error: {e}")
        bot.send_message(cid, ".", reply_markup=kb())

# ════════════════════════════════════════════════════════════
#  PHASE 2 — EMAIL (automatic sender switching)
# ════════════════════════════════════════════════════════════
def phase2_email_only():
    cid = state["chat_id"]
    if not cid:
        print("Cannot start phase2: no chat_id")
        return

    try:
        settings = get_settings()
        email_prompt = settings.get('email_prompt', 'Write a professional outreach email.')

        send("📧 *Starting email phase...* Loading pending qualified leads from sheet.")

        pending = get_pending_qualified_leads()
        if not pending:
            send("⚠️ No pending qualified leads found.")
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb())
            return

        send(f"📧 *Sending to {len(pending)} qualified leads*\nWaiting 1-2 min between each.")

        state["total_emailed"] = 0

        for row in pending:
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE": break

            try:
                senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
                available = [s for s in senders if int(s.get('sent',0)) < int(s.get('limit',1))]
            except Exception as e:
                print(f"Error fetching senders: {e}")
                time.sleep(3)
                continue

            if not available:
                debug = "⚠️ No available senders. Current statuses:\n"
                for s in senders:
                    debug += f"{s['email']}: {s.get('sent',0)}/{s.get('limit',0)}\n"
                send(debug)
                send("⚠️ All senders hit daily limit! Pausing.")
                state["status"] = "PAUSED"
                bot.send_message(cid, ".", reply_markup=kb())
                break

            sender = available[0]
            email = str(row.get('email',''))
            esrc = str(row.get('email_source','dev'))

            subject, body_html = build_clean_email(row, sender['email'], email_prompt)

            try:
                r2 = requests.post(sender['url'],
                     json={"action":"send_email","to":email,"subject":subject,"body":body_html},
                     timeout=30)
                resp = r2.text.strip()
            except Exception as se:
                resp = f"Connection error: {se}"

            if resp == "Success":
                try:
                    requests.post(SHEET_URL,
                        json={"action":"increment_sender","email":sender['email']},
                        timeout=15)
                    requests.post(SHEET_URL,
                        json={"action":"mark_emailed","email":email},
                        timeout=15)
                except Exception as e:
                    print(f"Error updating sheet after send: {e}")

                state["total_emailed"] += 1
                etag = {"dev":"📧","support":"📩","extracted":"📬"}.get(esrc,"📬")

                send(f"✅ *Email #{state['total_emailed']} Sent!*\n"
                     f"App: {row.get('app_name','?')}\n"
                     f"{etag} To: `{email}`\n"
                     f"Via: {sender['email']}")

                wait = random.randint(60, 120)
                send(f"⏳ Waiting *{wait}s*...")
                for _ in range(wait):
                    if state["status"] != "EMAILING": break
                    time.sleep(1)
            else:
                send(f"❌ Failed to `{email}`: {resp}")

        if state["status"] == "EMAILING":
            send(f"🎉 *Email Phase Complete!* Total emails sent: *{state['total_emailed']}*")
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
    except:
        pass
    return []

# ─── CLEAN EMAIL BUILDER (uses email prompt) ─────────────────
def build_clean_email(row, sender_email, email_prompt):
    app_name    = str(row.get('app_name','Unknown App'))
    dev_name    = str(row.get('dev_name','') or '').strip()
    if not dev_name or len(dev_name) < 2 or len(dev_name) > 35:
        dev_name = "Developer"
    rating      = float(row.get('rating') or 0.0)
    genre       = str(row.get('genre','') or '')
    website_url = str(row.get('website','') or '')
    description = str(row.get('description','') or '')[:300]

    if rating < 3.5:
        urgency = "critical"
    elif rating < 4.0:
        urgency = "moderate"
    else:
        urgency = "noticing some recent reviews?"

    genre_lower = genre.lower()
    if any(word in genre_lower for word in ['finance','bank','payment','fintech']):
        impact = "hurts user trust and new sign-ups"
    elif any(word in genre_lower for word in ['shopping','delivery','ecommerce']):
        impact = "can make users switch to competitors"
    elif any(word in genre_lower for word in ['game','gaming']):
        impact = "reduces daily active users and ranking"
    else:
        impact = "affects downloads and retention"

    compliment = ""
    if website_url and "http" in website_url:
        try:
            r = requests.get(website_url, timeout=5, headers={"User-Agent":"Mozilla/5.0"})
            text = re.sub(r'<[^>]+',' ', r.text)
            text = re.sub(r'\s+',' ', text).strip()[:300]
            match = re.search(r'(\d+[\+,]?\s*(users?|downloads?|customers?))', text, re.I)
            if match:
                compliment = f"Congrats on {match.group(0)}!"
            else:
                sentences = description.split('.')
                if sentences and len(sentences[0]) > 10:
                    compliment = f"I like your focus on {sentences[0].lower()[:50]}."
        except:
            compliment = f"Your app in the {genre} space looks interesting."
    else:
        compliment = f"Your app in the {genre} space looks interesting."

    if not compliment:
        compliment = f"Your app in the {genre} space looks interesting."

    prompt = f"""{email_prompt}

Write a short personalized cold email to this developer.

Their App:
- Name: {app_name}
- Developer: {dev_name}
- Category: {genre}
- Current rating: {rating} ({urgency})
- Summary: {row.get('summary','')}
- Description: {description}
- Website info: {compliment}

Rules:
1. Start EXACTLY with: Dear {dev_name},
2. Mention ONE specific thing about their app (from the compliment above)
3. Under 150 words total
4. Plain text only — no markdown, no bold, no headers
5. Use <br> for line breaks
6. Do NOT mention rating or install count
7. Clear call to action at end (soft CTA)

Output format only:
SUBJECT: [subject]
BODY: [email starting with Dear {dev_name},]"""

    try:
        r = ai.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=600
        )
        content = r.choices[0].message.content.strip()

        if "SUBJECT:" in content and "BODY:" in content:
            subject = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
            raw_body = content.split("BODY:")[1].strip()
        else:
            lines = content.split('\n')
            subject = lines[0].replace("Subject:","").replace("SUBJECT:","").strip()
            raw_body = '\n'.join(lines[1:]).strip()

        body = raw_body.replace('**','').replace('*','')
        body_html = body.replace('\n\n','<br><br>').replace('\n','<br>')
        unsubscribe = f'<br><br><hr style="border:0;border-top:1px solid #eee;margin:16px 0;"><p style="text-align:center;font-size:11px;color:#bbb;"><a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me." style="color:#bbb;">Unsubscribe</a></p>'
        full_html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#333;max-width:600px;margin:0 auto;">
{body_html}{unsubscribe}</div>"""
        return subject, full_html

    except Exception as e:
        print(f"Email build error: {e}")
        fallback = f"""Dear {dev_name},<br><br>
I came across {app_name} and would love to explore a collaboration.<br><br>
Best regards,<br>
Abu Raihan<br>
Play Store Review Service Specialist<br><br>
WhatsApp: +8801902911261<br>
Telegram: https://t.me/abu_raihan69"""
        subject = f"Quick question about {app_name}"
        unsubscribe = f'<br><br><hr style="border:0;border-top:1px solid #eee;margin:16px 0;"><p style="text-align:center;font-size:11px;color:#bbb;"><a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me." style="color:#bbb;">Unsubscribe</a></p>'
        full_html = f"<div>{fallback}{unsubscribe}</div>"
        return subject, full_html

# ─── SPAM TEST with sender selection ────────────────────────
def run_spam_test_with_sender(test_email, sender):
    settings = get_settings()
    email_prompt = settings.get('email_prompt', 'Write a professional outreach email.')
    fake_row = {
        "app_name":"Demo Budget Tracker",
        "dev_name":"Indie Studio",
        "rating":3.1,
        "genre":"Finance",
        "website":"",
        "description":"A simple app to track daily expenses.",
        "summary":"Personal budget tracker"
    }
    subject, body = build_clean_email(fake_row, sender['email'], email_prompt)
    try:
        r2 = requests.post(sender['url'],
             json={"action":"send_email","to":test_email,"subject":subject,"body":body},
             timeout=30)
        resp = r2.text.strip()
        if resp == "Success":
            send(f"✅ Test sent to `{test_email}` via {sender['email']}")
        else:
            send(f"❌ Failed: {resp}")
    except Exception as e:
        send(f"❌ Error: {e}")

def show_sender_selection(test_email):
    try:
        senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
        if not senders:
            send("❌ No senders available. Add one first.")
            bot.send_message(state["chat_id"], ".", reply_markup=kb())
            return
        mk = InlineKeyboardMarkup()
        for s in senders:
            mk.add(InlineKeyboardButton(s['email'], callback_data=f"testsend_{s['email']}"))
        mk.add(InlineKeyboardButton("🔙 Cancel", callback_data="cancel_test"))
        bot.send_message(state["chat_id"], "📧 Choose a sender for the test email:", reply_markup=mk)
        state["tmp_test_email"] = test_email
        state["status"] = "WAITING_TEST_SENDER"
    except Exception as e:
        send(f"❌ Error fetching senders: {e}")
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], ".", reply_markup=kb())

# ─── SCHEDULER ───────────────────────────────────────────────
def run_scheduler():
    tz = pytz.timezone('Asia/Dhaka')
    print("⏰ Scheduler thread started.")
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
                        threading.Thread(target=phase1_scrape, daemon=True).start()
                        break

        except Exception as e:
            print(f"[Scheduler] Error: {e}")

        time.sleep(10)

# ─── REFRESH COMMAND ─────────────────────────────────────────
def refresh_status():
    sets = get_keyword_sets()
    pending = [s for s in sets if s.get('status') == 'pending']
    send(f"🔄 *Refresh*\n"
         f"Status: {state['status']}\n"
         f"Pending keyword sets: {len(pending)}\n"
         f"Total scraped so far: {state['total_scraped']}\n"
         f"Total emailed: {state['total_emailed']}")

# ─── BOT HANDLERS ────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def welcome(message):
    state["chat_id"] = message.chat.id
    state["status"]  = "IDLE"
    state["settings"] = {}
    bot.reply_to(message,
        "👋 *Welcome Boss!*\n\n"
        "*🚀 Start Automation:* Runs full workflow – scrape + filter + email – using next pending keyword set.\n"
        "*📅 Schedules:* Set multiple daily run times (automation will start automatically).\n"
        "*🔑 Keywords:* Add keyword sets like `[crypto wallet] [travel app]` – they are used one by one.\n"
        "*📧 Senders:* Manage email sender accounts.\n"
        "*🧪 Spam Test:* Test email with sender selection.\n"
        "*🔄 Refresh:* Show current status.\n\n"
        "Use the buttons below.",
        parse_mode="Markdown", reply_markup=kb())

@bot.callback_query_handler(func=lambda c: True)
def callbacks(call):
    cid = call.message.chat.id
    d   = call.data
    if d == "back":
        state["status"] = "IDLE"
        bot.send_message(cid,"🔙 Main Menu.", reply_markup=kb())
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
        bot.send_message(cid, f"📝 Deploy in Apps Script then send URL:\n\n`{code}`",
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
            bot.send_message(cid, f"🗑️ Deleted sender *{e2}*", parse_mode="Markdown")
        except:
            bot.send_message(cid, "❌ Failed to delete sender.")
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
        bot.send_message(cid, "🔑 Send keyword sets like:\n`[crypto wallet] [travel app] [fitness tracker]`\nEach bracket will be one set.", reply_markup=back_kb())
    elif d.startswith("del_keyword_"):
        kid = d.split("del_keyword_")[1]
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Delete", callback_data=f"cfm_keyword_{kid}"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
        bot.send_message(cid, f"Delete this keyword set?", parse_mode="Markdown", reply_markup=mk)
    elif d.startswith("cfm_keyword_"):
        kid = d.split("cfm_keyword_")[1]
        delete_keyword_set(kid)
        bot.send_message(cid, f"🗑️ Deleted keyword set.", parse_mode="Markdown")
    elif d == "cancel":
        bot.send_message(cid,"Cancelled.")
    elif d == "cancel_test":
        state["status"] = "IDLE"
        state["tmp_test_email"] = None
        bot.send_message(cid, "Test cancelled.", reply_markup=kb())
    elif d.startswith("testsend_"):
        sender_email = d.split("testsend_")[1]
        test_email = state.get("tmp_test_email")
        if not test_email:
            bot.send_message(cid, "❌ No test email in memory. Start over.")
            state["status"] = "IDLE"
            return
        try:
            senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
            sender = next((s for s in senders if s['email'] == sender_email), None)
            if not sender:
                bot.send_message(cid, "❌ Sender not found.")
                state["status"] = "IDLE"
                return
        except:
            bot.send_message(cid, "❌ Failed to fetch sender.")
            state["status"] = "IDLE"
            return
        bot.send_message(cid, f"Sending test to *{test_email}* via {sender_email}...", parse_mode="Markdown")
        threading.Thread(target=run_spam_test_with_sender, args=(test_email, sender), daemon=True).start()
        state["status"] = "IDLE"
        state["tmp_test_email"] = None

@bot.message_handler(func=lambda m: True)
def handle(message):
    text = message.text.strip()
    state["chat_id"] = message.chat.id

    if text == "🔙 Back":
        state["status"]    = "IDLE"
        state["tmp_url"]   = None
        state["tmp_email"] = None
        state["tmp_test_email"] = None
        bot.reply_to(message,"🔙 Main Menu.", reply_markup=kb())
        return

    if state["status"] == "WAITING_URL":
        if "script.google.com" in text:
            state["tmp_url"] = text
            state["status"]  = "WAITING_EMAIL"
            bot.reply_to(message,"✅ URL saved! Send *email address*.",
                parse_mode="Markdown", reply_markup=back_kb())
        else:
            bot.reply_to(message,"❌ Invalid URL.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_EMAIL":
        if "@" in text:
            state["tmp_email"] = text
            state["status"]    = "WAITING_LIMIT"
            bot.reply_to(message,"✅ Email saved! Send *daily limit* (e.g. 20).",
                parse_mode="Markdown", reply_markup=back_kb())
        else:
            bot.reply_to(message,"❌ Invalid email.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_LIMIT":
        if text.isdigit():
            try:
                requests.post(SHEET_URL, json={
                    "action":"add_sender","email":state["tmp_email"],
                    "url":state["tmp_url"],"limit":int(text)
                }, timeout=15)
                bot.reply_to(message, f"🎉 Sender *{state['tmp_email']}* added! {text}/day",
                    parse_mode="Markdown", reply_markup=kb())
            except:
                bot.reply_to(message, "❌ Failed to add sender. Check sheet connection.")
            state["status"]    = "IDLE"
            state["tmp_url"]   = None
            state["tmp_email"] = None
        else:
            bot.reply_to(message,"❌ Send a number.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_SCHEDULE":
        p = parse_time(text)
        if p:
            add_schedule_time(p)
            bot.reply_to(message, f"✅ Schedule added at *{p}* daily (Dhaka)!",
                parse_mode="Markdown", reply_markup=kb())
            state["status"] = "IDLE"
        else:
            bot.reply_to(message,"❌ Format: 02:30 PM or 14:30", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_KEYWORD":
        sets = re.findall(r'\[(.*?)\]', text)
        if sets:
            for s in sets:
                s = s.strip()
                if s:
                    add_keyword_set(s)
            bot.reply_to(message, f"✅ Added {len(sets)} keyword set(s).",
                parse_mode="Markdown", reply_markup=kb())
        else:
            bot.reply_to(message,"❌ No brackets found. Use like: `[crypto wallet]`", reply_markup=back_kb())
        state["status"] = "IDLE"
        return

    elif state["status"] == "WAITING_TEST_EMAIL":
        if "@" in text:
            show_sender_selection(text)
        else:
            bot.reply_to(message,"❌ Invalid email. Try again.", reply_markup=back_kb())
        return

    if text == "🚀 Start Automation":
        if state["status"] == "IDLE":
            threading.Thread(target=phase1_scrape, daemon=True).start()

    elif text == "🛑 Pause":
        if state["status"] in ["SCRAPING","FILTERING","EMAILING"]:
            state["status"] = "PAUSED"
            bot.reply_to(message,"🛑 *Paused.* Progress saved.", reply_markup=kb())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            if state["generated_kws"] and state["kw_index"] < len(state["generated_kws"]):
                state["status"] = "SCRAPING"
            else:
                state["status"] = "EMAILING"
            bot.reply_to(message,"▶️ *Resuming...*", reply_markup=kb())

    elif text == "⏹️ Stop":
        if state["status"] in ["SCRAPING","FILTERING","EMAILING","PAUSED"]:
            state["status"] = "IDLE"
            state["generated_kws"] = []
            state["kw_index"] = 0
            state["current_set_id"] = None
            state["qualified_count"] = 0
            bot.reply_to(message,"⏹️ *Stopped.* Current keyword set remains pending.", reply_markup=kb())

    elif text == "⏹️ Reset":
        state.update({
            "status":"IDLE",
            "generated_kws":[],
            "kw_index":0,
            "scraped_ids":set(),
            "total_scraped":0,
            "total_emailed":0,
            "current_set_id": None,
            "qualified_count":0,
            "seen_emails":set(),
            "settings":{}
        })
        bot.reply_to(message,"⏹️ *Fully reset.*", reply_markup=kb())

    elif text == "🔄 Refresh":
        refresh_status()

    elif text == "📅 Schedules":
        times = get_schedule_times()
        mk = InlineKeyboardMarkup()
        txt = "📋 *Scheduled times (Dhaka):*\n\n"
        if not times:
            txt += "_None set._\n"
        else:
            for t in times:
                txt += f"• {t}\n"
                mk.add(InlineKeyboardButton(f"🗑️ {t}", callback_data=f"del_schedule_{t}"))
        mk.add(InlineKeyboardButton("➕ Add Time", callback_data="add_schedule"))
        mk.add(InlineKeyboardButton("🔙 Back", callback_data="back"))
        bot.reply_to(message, txt, parse_mode="Markdown", reply_markup=mk)

    elif text == "🔑 Keywords":
        sets = get_keyword_sets()
        mk = InlineKeyboardMarkup()
        txt = "🔑 *Keyword sets:*\n\n"
        if not sets:
            txt += "_None added._\n"
        else:
            for s in sets:
                status_icon = "✅" if s.get('status') == 'used' else "⏳"
                txt += f"{status_icon} `{s.get('set_text')}`\n"
                if s.get('status') == 'pending':
                    mk.add(InlineKeyboardButton(f"🗑️ {s.get('set_text')[:20]}", callback_data=f"del_keyword_{s.get('id')}"))
        mk.add(InlineKeyboardButton("➕ Add Set", callback_data="add_keyword"))
        mk.add(InlineKeyboardButton("🔙 Back", callback_data="back"))
        bot.reply_to(message, txt, parse_mode="Markdown", reply_markup=mk)

    elif text == "🧪 Spam Test":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TEST_EMAIL"
            bot.reply_to(message,"📧 Send the email address for the test.", reply_markup=back_kb())

    elif text == "📧 Senders":
        try:
            senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
        except:
            bot.reply_to(message,"❌ Cannot reach Sheet.", reply_markup=kb())
            return
        mk  = InlineKeyboardMarkup()
        txt = "📋 *Senders:*\n\n"
        if not senders:
            txt += "_None yet._\n"
        else:
            for i,s in enumerate(senders):
                txt += f"{i+1}. `{s.get('email')}` — {s.get('sent',0)}/{s.get('limit',0)}\n"
                mk.add(InlineKeyboardButton(f"🗑️ {s.get('email')}", callback_data=f"del_sender_{s.get('email')}"))
        mk.add(InlineKeyboardButton("➕ Add Sender", callback_data="add_sender"))
        mk.add(InlineKeyboardButton("🔙 Back",       callback_data="back"))
        bot.reply_to(message, txt, parse_mode="Markdown", reply_markup=mk)

# ─── MAIN ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Starting...")
    threading.Thread(target=run_web,       daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    while True:
        try:
            print("🤖 Polling...")
            bot.polling(none_stop=True, interval=0, timeout=30)
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)
