import requests, telebot, time, random, os, threading, re
from datetime import datetime
import pytz
from flask import Flask
from groq import Groq
from google_play_scraper import search, app as gplay
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ─── FLASK ───────────────────────────────
web_app = Flask(__name__)

@web_app.route('/')
def home(): return "Bot is Alive!"

def run_web():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ─── CONFIG ──────────────────────────────
SHEET_URL = "https://script.google.com/macros/s/AKfycbzI5eCCU_Gci6M0jFr5I_Ph48CqUvvP4_nkpngWtjFafVSr_i75yqKX37ZMG4qwG0_V/exec"
BOT_TOKEN = "8709829378:AAEJJQ8jm_oTyAcGenBrIfLi4KYHRVcSJbo"
GROQ_KEY  = "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"

bot  = telebot.TeleBot(BOT_TOKEN)
ai   = Groq(api_key=GROQ_KEY)

# ─── STATE ───────────────────────────────
state = {
    "status":         "IDLE",
    "keywords":       [],
    "kw_index":       0,
    "app_queue":      [],   # list of appIds for current keyword
    "app_index":      0,    # current position in app_queue
    "total_leads":    0,
    "scraped_ids":    set(),  # globally seen appIds (no re-fetch)
    "chat_id":        None,
    "scheduled_time": None,
    "tmp_url":        None,
    "tmp_email":      None,
}

GOV = ['gov','government','ministry','department','council',
       'national','authority','federal','municipal']

# ─── KEYBOARDS ───────────────────────────
def kb():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    s = state["status"]
    if s == "IDLE":
        m.add(KeyboardButton("🚀 Start"), KeyboardButton("📅 Schedule"))
        m.add(KeyboardButton("🧪 Spam Test"), KeyboardButton("📧 Senders"))
    elif s == "RUNNING":
        m.add(KeyboardButton("🛑 Pause"))
    elif s == "PAUSED":
        m.add(KeyboardButton("▶️ Resume"), KeyboardButton("⏹️ Reset"))
    elif s == "SCHEDULED":
        m.add(KeyboardButton("❌ Cancel Schedule"))
    return m

def back_kb():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    m.add(KeyboardButton("🔙 Back"))
    return m

def send(text, md="Markdown"):
    try:    bot.send_message(state["chat_id"], text, parse_mode=md)
    except:
        try: bot.send_message(state["chat_id"], text)
        except: pass

def parse_time(s):
    s = s.strip().upper()
    for f in ("%I:%M %p", "%H:%M"):
        try: return datetime.strptime(s, f).strftime("%H:%M")
        except: pass
    return None

# ═══════════════════════════════════════════════════
# STEP 1 ── SEARCH: collect all app IDs for keyword
# ═══════════════════════════════════════════════════
def step1_collect_ids(kw):
    """
    Search with 8 variations → collect all appIds → deduplicate → return list
    """
    queries = [kw, f"{kw} app", f"{kw} free", f"best {kw}",
               f"new {kw}", f"{kw} simple", f"{kw} lite", f"{kw} basic"]
    raw = []
    for q in queries:
        try:
            results = search(q, lang='en', country='us', n_hits=100)
            for r in results:
                raw.append(r['appId'])
            time.sleep(0.2)
        except Exception as e:
            print(f"Search error '{q}': {e}")

    # Deduplicate, keep order
    seen, ids = set(), []
    for i in raw:
        if i not in seen:
            seen.add(i)
            ids.append(i)

    return ids  # NOT reversed — natural order

# ═══════════════════════════════════════════════════
# STEP 2 ── FETCH: get full details of one app
# ═══════════════════════════════════════════════════
def step2_fetch_details(app_id):
    """
    Fetch complete app details from Play Store.
    Returns dict with all fields, or None if failed.
    """
    try:
        d = gplay(app_id, lang='en', country='us')
        return d
    except Exception as e:
        print(f"Fetch failed {app_id}: {e}")
        return None

# ═══════════════════════════════════════════════════
# STEP 3 ── FILTER: check rating, installs, email
# ═══════════════════════════════════════════════════
def step3_filter(d, max_rating, max_installs):
    """
    Rules:
    - rating <= max_rating  (0.0 also passes — new apps)
    - installs <= max_installs
    - must have some contact email
    - skip government apps

    Returns (passes: bool, reason: str, email: str, email_src: str)
    """
    # Government check
    dev = str(d.get('developer', '') or '').lower()
    if any(g in dev for g in GOV):
        return False, "government", "", ""

    # Rating: 0.0 means no rating yet (new app) → allow
    rating = float(d.get('score') or 0.0)
    if rating > max_rating:
        return False, f"rating {rating:.1f} > {max_rating}", "", ""

    # Installs
    raw_inst = d.get('minInstalls') or d.get('realInstalls') or 0
    installs = int(raw_inst) if raw_inst else 0
    if installs > max_installs:
        return False, f"installs {installs:,} > {max_installs:,}", "", ""

    # Email — priority: developerEmail > supportEmail > extracted
    email, src = get_email(d)
    if not email:
        return False, "no email", "", ""

    return True, "ok", email, src

def get_email(d):
    """Return (email, source) with priority: dev > support > extracted"""
    for field, src in [("developerEmail", "dev"), ("supportEmail", "support")]:
        v = str(d.get(field, '') or '').strip().lower()
        if v and '@' in v and '.' in v:
            return v, src
    # Try extracting from text fields
    for field in ["developerWebsite", "privacyPolicy", "developerAddress"]:
        v = str(d.get(field, '') or '')
        found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', v)
        if found:
            return found[0].lower(), "extracted"
    return "", "none"

# ═══════════════════════════════════════════════════
# STEP 4 ── DUPLICATE CHECK: ask Sheet if email exists
# ═══════════════════════════════════════════════════
def step4_duplicate_check(email):
    """
    Ask Sheet's Leads tab if this email already exists.
    Returns True if duplicate (already exists), False if new.
    """
    try:
        res = requests.post(SHEET_URL, json={
            "action": "check_duplicate",
            "email":  email
        }, timeout=10).json()
        return res.get("exists", False)
    except:
        # If Sheet unreachable, use local set as fallback
        return False

# ═══════════════════════════════════════════════════
# STEP 5 ── BUILD PERSONALIZED EMAIL
# ═══════════════════════════════════════════════════
def step5_build_email(d, contact_info, email_prompt, sender_email):
    app_name = str(d.get('title', 'Unknown App'))
    dev_name = str(d.get('developer', '') or '').strip()
    if not dev_name or len(dev_name) < 2 or len(dev_name) > 35:
        dev_name = "Developer"

    genre       = str(d.get('genre', '') or '')
    summary     = str(d.get('summary', '') or '')
    description = str(d.get('description', '') or '')[:500]
    website_url = str(d.get('developerWebsite', '') or '')

    # Fetch website text for better personalization
    site_text = ""
    if website_url and "http" in website_url:
        try:
            r = requests.get(website_url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            t = re.sub(r'<[^>]+>', ' ', r.text)
            site_text = re.sub(r'\s+', ' ', t).strip()[:500]
        except:
            pass

    contact_html = str(contact_info).replace('\n', '<br>')

    prompt = f"""{email_prompt}

Write a short personalized cold email to this app developer.

Their App:
- Name: {app_name}
- Developer: {dev_name}
- Category: {genre}
- Summary: {summary}
- Description: {description}
- Website info: {site_text or "not available"}

Rules:
1. Start EXACTLY with: Dear {dev_name},
2. Mention ONE specific thing about their app (from description/summary above)
3. Keep it under 150 words
4. Plain text only — no markdown, no bold, no headers
5. Use <br> for line breaks
6. Do NOT mention their rating or install count
7. End with a clear call to action

Output format (nothing else):
SUBJECT: [subject]
BODY: [email starting with Dear {dev_name},]"""

    try:
        r = ai.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=600
        )
        content = r.choices[0].message.content.strip()

        if "SUBJECT:" in content and "BODY:" in content:
            subject  = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
            raw_body = content.split("BODY:")[1].strip()
        else:
            lines    = content.split('\n')
            subject  = lines[0].replace("Subject:", "").replace("SUBJECT:", "").strip()
            raw_body = '\n'.join(lines[1:]).strip()

        body = raw_body.replace('**', '').replace('*', '')
        body = body.replace('\n\n', '<br><br>').replace('\n', '<br>')

        html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#333;max-width:600px;margin:0 auto;">
{body}<br><br>{contact_html}<br><br>
<hr style="border:0;border-top:1px solid #eee;margin:16px 0;">
<p style="text-align:center;font-size:11px;color:#bbb;">
<a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me please." style="color:#bbb;">Unsubscribe</a>
</p></div>"""

        return subject, html

    except Exception as e:
        print(f"Email build error: {e}")
        html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#333;max-width:600px;margin:0 auto;">
Dear {dev_name},<br><br>I came across your app <b>{app_name}</b> and would love to explore a collaboration.<br><br>
{contact_html}<br><br>
<hr style="border:0;border-top:1px solid #eee;margin:16px 0;">
<p style="text-align:center;font-size:11px;color:#bbb;">
<a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me please." style="color:#bbb;">Unsubscribe</a>
</p></div>"""
        return f"Quick question about {app_name}", html

# ═══════════════════════════════════════════════════
# CORE ENGINE
# ═══════════════════════════════════════════════════
def engine(max_installs, max_rating, contact_info, email_prompt):
    cid = state["chat_id"]

    # Stats counters
    c = {"fetched": 0, "rating_fail": 0, "install_fail": 0,
         "no_email": 0, "gov": 0, "duplicate": 0, "sent": 0, "send_fail": 0}

    # ── Outer loop: keywords ──────────────────────
    while state["kw_index"] < len(state["keywords"]):
        while state["status"] == "PAUSED": time.sleep(1)
        if state["status"] == "IDLE" or state["total_leads"] >= 200: break

        kw = state["keywords"][state["kw_index"]]

        # ── STEP 1: Collect app IDs for this keyword ──
        if not state["app_queue"]:
            send(f"🔍 *Keyword {state['kw_index']+1}/{len(state['keywords'])}:* `{kw}`\n"
                 f"Searching Play Store...")

            ids = step1_collect_ids(kw)
            # Remove globally already-fetched IDs
            ids = [i for i in ids if i not in state["scraped_ids"]]

            if not ids:
                send(f"⚠️ No new apps found for `{kw}`. Moving to next keyword...")
                state["kw_index"] += 1
                continue

            state["app_queue"] = ids
            state["app_index"] = 0
            send(f"📦 *{len(ids)} apps* found for `{kw}`\n"
                 f"Now fetching details + filtering...")

        # ── Inner loop: process each app ─────────────
        while state["app_index"] < len(state["app_queue"]):
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE" or state["total_leads"] >= 200: break

            app_id = state["app_queue"][state["app_index"]]
            state["app_index"] += 1

            # Skip if already fetched globally
            if app_id in state["scraped_ids"]: continue
            state["scraped_ids"].add(app_id)

            # ── STEP 2: Fetch full app details ──
            d = step2_fetch_details(app_id)
            if d is None: continue
            c["fetched"] += 1

            # ── STEP 3: Filter ──
            passes, reason, email, esrc = step3_filter(d, max_rating, max_installs)

            if not passes:
                if "rating"    in reason: c["rating_fail"]  += 1
                elif "installs" in reason: c["install_fail"] += 1
                elif "no email" in reason: c["no_email"]     += 1
                elif "gov"      in reason: c["gov"]          += 1
                continue

            # ── STEP 4: Duplicate check via Sheet ──
            if step4_duplicate_check(email):
                c["duplicate"] += 1
                send(f"♻️ Duplicate skipped: `{email}`")
                continue

            # ── Check sender availability ──
            try:
                senders   = requests.post(SHEET_URL, json={"action": "get_senders"}, timeout=15).json()
                available = [s for s in senders if int(s.get('sent', 0)) < int(s.get('limit', 1))]
            except:
                time.sleep(3)
                continue

            if not available:
                send("⚠️ All senders hit daily limit! Pausing automation.")
                state["status"] = "PAUSED"
                break

            sender = available[0]

            # Pull app info for display
            title    = str(d.get('title', 'Unknown'))
            dev      = str(d.get('developer', 'Developer'))
            rating   = float(d.get('score') or 0.0)
            raw_inst = d.get('minInstalls') or d.get('realInstalls') or 0
            installs = int(raw_inst) if raw_inst else 0
            etag     = {"dev": "📧 Dev", "support": "📩 Support", "extracted": "📬 Extracted"}.get(esrc, "📬")

            send(f"✨ *Lead passed filter!*\n"
                 f"App: *{title}*\n"
                 f"Dev: {dev}\n"
                 f"Rating: `{rating}` | Installs: `{installs:,}`\n"
                 f"{etag}: `{email}`\n"
                 f"Building personalized email...")

            # ── STEP 5: Build personalized email ──
            subject, body = step5_build_email(d, contact_info, email_prompt, sender['email'])

            # ── Save to Sheet ──
            try:
                requests.post(SHEET_URL, json={
                    "action":       "save_lead",
                    "app_name":     title,
                    "dev_name":     dev,
                    "email":        email,
                    "email_source": esrc,
                    "subject":      subject,
                    "body":         body,
                    "installs":     installs,
                    "rating":       rating,
                    "link":         d.get('url', ''),
                    "category":     d.get('genre', ''),
                    "website":      d.get('developerWebsite', ''),
                    "updated":      str(d.get('updated', ''))
                }, timeout=15)
            except: pass

            # ── Send email ──
            try:
                r2   = requests.post(
                    sender['url'],
                    json={"action": "send_email", "to": email, "subject": subject, "body": body},
                    timeout=30
                )
                resp = r2.text.strip()
            except Exception as se:
                resp = f"Error: {se}"

            if resp == "Success":
                try:
                    requests.post(SHEET_URL, json={"action": "increment_sender", "email": sender['email']}, timeout=15)
                except: pass

                state["total_leads"] += 1
                c["sent"] += 1

                send(f"✅ *Lead #{state['total_leads']} Sent!*\n"
                     f"To: `{email}` ({esrc})\n"
                     f"Via: {sender['email']}\n"
                     f"KW: `{kw}` | App {state['app_index']}/{len(state['app_queue'])}")

                # Wait 1–2 min then resume exact position
                wait = random.randint(60, 120)
                send(f"⏳ Waiting *{wait}s* then continuing...")
                for _ in range(wait):
                    if state["status"] != "RUNNING": break
                    time.sleep(1)

            else:
                c["send_fail"] += 1
                send(f"❌ Send failed to `{email}`: {resp}")

        # ── End of queue for this keyword ──
        if state["status"] == "RUNNING":
            send(f"📌 *Done: `{kw}`*\n"
                 f"Fetched: {c['fetched']} | Sent: {c['sent']}\n"
                 f"Rating↑: {c['rating_fail']} | Installs↑: {c['install_fail']}\n"
                 f"No email: {c['no_email']} | Duplicate: {c['duplicate']}")

            state["app_queue"] = []
            state["app_index"] = 0
            state["kw_index"]  += 1

    # ── All done ──
    if state["status"] == "RUNNING":
        send(f"🎉 *Automation Complete!*\n"
             f"Total leads: *{state['total_leads']}*\n"
             f"Fetched: {c['fetched']} | Sent: {c['sent']}\n"
             f"No email: {c['no_email']} | Dup: {c['duplicate']}\n"
             f"Rating fail: {c['rating_fail']} | Install fail: {c['install_fail']}")
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], "✅ Done!", reply_markup=kb())

# ─────────────────────────────────────────
# START ENGINE
# ─────────────────────────────────────────
def start_engine():
    cid = state["chat_id"]
    try:
        send("🔄 Loading settings from Sheet...")

        res = requests.post(SHEET_URL, json={"action": "get_settings"}, timeout=20).json()

        try:    max_installs = int(str(res.get('max_installs', '100000')).replace(',', '').strip())
        except: max_installs = 100000

        try:    max_rating = float(str(res.get('max_rating', '4.5')).strip())
        except: max_rating = 4.5

        contact_info = str(res.get('contact_info', ''))
        email_prompt = str(res.get('email_prompt', 'Write a professional outreach email.'))
        niche        = str(res.get('niche', 'mobile apps'))
        kw_prompt    = str(res.get('keyword_prompt', 'Generate Play Store search terms for'))

        send(f"✅ *Settings:*\n"
             f"Max Rating: `{max_rating}` | Max Installs: `{max_installs:,}`")

        # Generate keywords if fresh start
        if not state["keywords"]:
            send("🧠 Generating keywords with AI...")

            p = f"""{kw_prompt}
Niche: {niche}

Give 200 unique short search terms (2-5 words) someone types in Google Play Store.
Comma separated ONLY. No 'keywords' word. No numbers. No bullets. No explanation."""

            r = ai.chat.completions.create(
                messages=[{"role": "user", "content": p}],
                model="llama-3.1-8b-instant",
                max_tokens=2000
            )
            raw = r.choices[0].message.content.replace('\n', ',').replace('\r', ',')
            cleaned = []
            for k in raw.split(','):
                k = re.sub(r'^\d+[\.\)\-\s]+', '', k)
                k = k.replace('keyword', '').replace('**', '').replace('*', '').replace('#', '')
                k = k.replace('"', '').replace("'", '').strip()
                if 2 < len(k) < 60: cleaned.append(k)

            if not cleaned:
                send("❌ Keyword generation failed. Try again.")
                state["status"] = "IDLE"
                bot.send_message(cid, ".", reply_markup=kb())
                return

            state["keywords"]    = cleaned
            state["kw_index"]    = 0
            state["app_queue"]   = []
            state["app_index"]   = 0
            state["total_leads"] = 0
            state["scraped_ids"] = set()

            send(f"✅ *{len(cleaned)} keywords* ready!")

        threading.Thread(
            target=engine,
            args=(max_installs, max_rating, contact_info, email_prompt),
            daemon=True
        ).start()

    except Exception as e:
        state["status"] = "IDLE"
        send(f"❌ Error: {e}")
        bot.send_message(cid, ".", reply_markup=kb())

# ─────────────────────────────────────────
# SPAM TEST
# ─────────────────────────────────────────
def run_spam_test(test_email):
    send("🔄 Running Spam Test...")
    try:
        senders = requests.post(SHEET_URL, json={"action": "get_senders"}, timeout=15).json()
        if not senders:
            send("❌ No senders! Add one first.")
            bot.send_message(state["chat_id"], ".", reply_markup=kb())
            return

        sender = senders[0]
        res    = requests.post(SHEET_URL, json={"action": "get_settings"}, timeout=15).json()

        fake = {
            'title': 'Demo Budget Tracker', 'developer': 'Indie Studio',
            'score': 3.1, 'minInstalls': 2000,
            'description': 'A simple app to track daily expenses and savings.',
            'summary': 'Personal budget tracker', 'developerWebsite': '',
            'genre': 'Finance', 'developerEmail': test_email,
            'url': '', 'updated': '', 'privacyPolicy': ''
        }

        subject, body = step5_build_email(
            fake,
            str(res.get('contact_info', '')),
            str(res.get('email_prompt', 'Write a professional outreach email.')),
            sender['email']
        )

        r2 = requests.post(sender['url'],
             json={"action": "send_email", "to": test_email, "subject": subject, "body": body},
             timeout=30)

        if r2.text.strip() == "Success":
            send(f"✅ Test sent!\nTo: `{test_email}`\nVia: {sender['email']}")
        else:
            send(f"❌ Failed: {r2.text}")

        bot.send_message(state["chat_id"], ".", reply_markup=kb())
    except Exception as e:
        send(f"❌ Error: {e}")
        bot.send_message(state["chat_id"], ".", reply_markup=kb())

# ─────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────
def run_scheduler():
    tz = pytz.timezone('Asia/Dhaka')
    while True:
        try:
            if state["status"] == "SCHEDULED" and state["scheduled_time"] and state["chat_id"]:
                if datetime.now(tz).strftime("%H:%M") == state["scheduled_time"]:
                    state["status"] = "RUNNING"
                    send("⏰ Scheduled time! Starting...")
                    bot.send_message(state["chat_id"], ".", reply_markup=kb())
                    start_engine()
                    time.sleep(61)
        except Exception as e:
            print(f"Scheduler: {e}")
        time.sleep(10)

# ─────────────────────────────────────────
# BOT HANDLERS
# ─────────────────────────────────────────
@bot.message_handler(commands=['start'])
def welcome(message):
    state["chat_id"] = message.chat.id
    state["status"]  = "IDLE"
    bot.reply_to(message, "👋 *Welcome Boss!*", parse_mode="Markdown", reply_markup=kb())

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
    } catch(err) {
      return ContentService.createTextOutput("Error: " + err);
    }
  }
}"""
        bot.send_message(cid, f"📝 Deploy in Apps Script then send the URL:\n\n`{code}`",
            parse_mode="Markdown", reply_markup=back_kb())
        state["status"] = "WAITING_URL"

    elif d.startswith("del_"):
        e2 = d.split("del_")[1]
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Delete", callback_data=f"cfm_{e2}"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
        bot.send_message(cid, f"Delete *{e2}*?", parse_mode="Markdown", reply_markup=mk)

    elif d.startswith("cfm_"):
        e2 = d.split("cfm_")[1]
        requests.post(SHEET_URL, json={"action": "delete_sender", "email": e2}, timeout=15)
        bot.send_message(cid, f"🗑️ Deleted *{e2}*", parse_mode="Markdown")

    elif d == "cancel":
        bot.send_message(cid, "Cancelled.")

@bot.message_handler(func=lambda m: True)
def handle(message):
    text = message.text.strip()
    state["chat_id"] = message.chat.id

    if text == "🔙 Back":
        state["status"]    = "IDLE"
        state["tmp_url"]   = None
        state["tmp_email"] = None
        bot.reply_to(message, "🔙 Main Menu.", reply_markup=kb())
        return

    # ── Sender setup flow ──
    if state["status"] == "WAITING_URL":
        if "script.google.com" in text:
            state["tmp_url"] = text
            state["status"]  = "WAITING_EMAIL"
            bot.reply_to(message, "✅ URL saved! Send *email address*.", parse_mode="Markdown", reply_markup=back_kb())
        else:
            bot.reply_to(message, "❌ Invalid URL.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_EMAIL":
        if "@" in text:
            state["tmp_email"] = text
            state["status"]    = "WAITING_LIMIT"
            bot.reply_to(message, "✅ Email saved! Send *daily limit* (e.g. 20).", parse_mode="Markdown", reply_markup=back_kb())
        else:
            bot.reply_to(message, "❌ Invalid email.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_LIMIT":
        if text.isdigit():
            requests.post(SHEET_URL, json={
                "action": "add_sender", "email": state["tmp_email"],
                "url":    state["tmp_url"],  "limit": int(text)
            }, timeout=15)
            bot.reply_to(message, f"🎉 Sender *{state['tmp_email']}* added! Limit: {text}/day",
                parse_mode="Markdown", reply_markup=kb())
            state["status"]    = "IDLE"
            state["tmp_url"]   = None
            state["tmp_email"] = None
        else:
            bot.reply_to(message, "❌ Send a number.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_TIME":
        p = parse_time(text)
        if p:
            state["scheduled_time"] = p
            state["status"]         = "SCHEDULED"
            bot.reply_to(message, f"✅ Scheduled at *{p}* daily (Dhaka)!", parse_mode="Markdown", reply_markup=kb())
        else:
            bot.reply_to(message, "❌ Format: 02:30 PM or 14:30", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_TEST":
        if "@" in text:
            state["status"] = "IDLE"
            bot.reply_to(message, f"Sending test to *{text}*...", parse_mode="Markdown")
            threading.Thread(target=run_spam_test, args=(text,), daemon=True).start()
        else:
            bot.reply_to(message, "❌ Invalid email.", reply_markup=back_kb())
        return

    # ── Main menu buttons ──
    if text == "📧 Senders":
        try:
            senders = requests.post(SHEET_URL, json={"action": "get_senders"}, timeout=15).json()
        except:
            bot.reply_to(message, "❌ Cannot reach Sheet.", reply_markup=kb())
            return
        mk  = InlineKeyboardMarkup()
        txt = "📋 *Senders:*\n\n"
        if not senders:
            txt += "_None yet._\n"
        else:
            for i, s in enumerate(senders):
                txt += f"{i+1}. `{s.get('email')}` — {s.get('sent',0)}/{s.get('limit',0)}\n"
                mk.add(InlineKeyboardButton(f"🗑️ {s.get('email')}", callback_data=f"del_{s.get('email')}"))
        mk.add(InlineKeyboardButton("➕ Add Sender", callback_data="add_sender"))
        mk.add(InlineKeyboardButton("🔙 Back",       callback_data="back"))
        bot.reply_to(message, txt, parse_mode="Markdown", reply_markup=mk)

    elif text == "🚀 Start":
        if state["status"] in ["IDLE", "SCHEDULED"]:
            state["status"] = "RUNNING"
            bot.reply_to(message, "🚀 *Starting...*", parse_mode="Markdown", reply_markup=kb())
            threading.Thread(target=start_engine, daemon=True).start()

    elif text == "🛑 Pause":
        if state["status"] == "RUNNING":
            state["status"] = "PAUSED"
            bot.reply_to(message, "🛑 *Paused.* Progress saved — keyword & app position remembered.",
                parse_mode="Markdown", reply_markup=kb())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            state["status"] = "RUNNING"
            bot.reply_to(message, "▶️ *Resuming from exact position...*",
                parse_mode="Markdown", reply_markup=kb())

    elif text == "⏹️ Reset":
        state.update({
            "status": "IDLE", "keywords": [], "kw_index": 0,
            "app_queue": [], "app_index": 0,
            "total_leads": 0, "scraped_ids": set()
        })
        bot.reply_to(message, "⏹️ *Fully reset.*", parse_mode="Markdown", reply_markup=kb())

    elif text == "📅 Schedule":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TIME"
            bot.reply_to(message, "⏰ Send time (*02:30 PM* or *14:30*)",
                parse_mode="Markdown", reply_markup=back_kb())

    elif text == "❌ Cancel Schedule":
        state["status"]         = "IDLE"
        state["scheduled_time"] = None
        bot.reply_to(message, "❌ Cancelled.", reply_markup=kb())

    elif text == "🧪 Spam Test":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TEST"
            bot.reply_to(message, "📧 Send test email address.", reply_markup=back_kb())

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
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
