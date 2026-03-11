import requests, telebot, time, random, os, threading, re
from datetime import datetime
import pytz
from flask import Flask
from groq import Groq
from google_play_scraper import search, app as get_app_details
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ─────────────────────────────────────────
# FLASK
# ─────────────────────────────────────────
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot is Alive!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
SHEET_URL = "https://script.google.com/macros/s/AKfycbzI5eCCU_Gci6M0jFr5I_Ph48CqUvvP4_nkpngWtjFafVSr_i75yqKX37ZMG4qwG0_V/exec"
BOT_TOKEN = "8709829378:AAEJJQ8jm_oTyAcGenBrIfLi4KYHRVcSJbo"
GROQ_KEY  = "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"

bot    = telebot.TeleBot(BOT_TOKEN)
groq   = Groq(api_key=GROQ_KEY)

# ─────────────────────────────────────────
# STATE
# ─────────────────────────────────────────
state = {
    "status":            "IDLE",
    "keywords":          [],
    "kw_index":          0,       # which keyword we are on
    "app_queue":         [],      # list of appIds for current keyword
    "app_index":         0,       # which app in queue we are on
    "total_leads":       0,
    "scraped_ids":       set(),   # globally seen appIds
    "sent_emails":       set(),   # globally sent emails
    "chat_id":           None,
    "scheduled_time":    None,
    "tmp_url":           None,
    "tmp_email":         None,
}

GOV_WORDS = ['gov','government','ministry','department','council',
             'national','authority','federal','municipal','public sector']

# ─────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────
def get_keyboard():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    s = state["status"]
    if s == "IDLE":
        m.add(KeyboardButton("🚀 Start Automation"), KeyboardButton("📅 Schedule"))
        m.add(KeyboardButton("🧪 Spam Test"),        KeyboardButton("📧 Manage Senders"))
    elif s == "RUNNING":
        m.add(KeyboardButton("🛑 Stop"))
    elif s == "PAUSED":
        m.add(KeyboardButton("▶️ Resume"), KeyboardButton("⏹️ Full Reset"))
    elif s == "SCHEDULED":
        m.add(KeyboardButton("❌ Cancel Schedule"))
    return m

def back_kb():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    m.add(KeyboardButton("🔙 Back"))
    return m

def parse_time(s):
    s = s.strip().upper()
    for fmt in ("%I:%M %p", "%H:%M"):
        try: return datetime.strptime(s, fmt).strftime("%H:%M")
        except: pass
    return None

def msg(chat_id, text, md="Markdown"):
    try:    bot.send_message(chat_id, text, parse_mode=md)
    except:
        try: bot.send_message(chat_id, text)
        except: pass

# ─────────────────────────────────────────
# EMAIL HELPERS
# ─────────────────────────────────────────
def extract_email_from_text(text):
    found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', str(text))
    return found[0].lower() if found else ""

def get_best_email(d):
    """
    Returns (email, source)
    Priority: developerEmail > supportEmail > extract from website/policy/address
    """
    for field, src in [("developerEmail","dev"), ("supportEmail","support")]:
        v = str(d.get(field,"") or "").strip().lower()
        if v and "@" in v and "." in v:
            return v, src
    for field in ["developerWebsite","privacyPolicy","developerAddress"]:
        e = extract_email_from_text(d.get(field,"") or "")
        if e:
            return e, "extracted"
    return "", "none"

def fetch_website_text(url, max_chars=600):
    if not url or "http" not in str(url): return ""
    try:
        r = requests.get(url, timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        t = re.sub(r'<[^>]+>',' ', r.text)
        t = re.sub(r'\s+',' ', t).strip()
        return t[:max_chars]
    except:
        return ""

# ─────────────────────────────────────────
# PHASE 1 — SEARCH: collect maximum app IDs
# Uses 8 query variations per keyword
# Reversed so low-ranked (often low-rated) apps come first
# ─────────────────────────────────────────
def collect_app_ids(kw):
    variations = [
        kw,
        f"{kw} app",
        f"{kw} free",
        f"best {kw}",
        f"new {kw}",
        f"{kw} simple",
        f"{kw} lite",
        f"{kw} basic",
    ]
    raw_ids = []
    for q in variations:
        try:
            results = search(q, lang='en', country='us', n_hits=100)
            for r in results:
                raw_ids.append(r['appId'])
            time.sleep(0.2)
        except Exception as e:
            print(f"Search error '{q}': {e}")
            continue

    # Deduplicate while preserving order
    seen, unique = set(), []
    for aid in raw_ids:
        if aid not in seen:
            seen.add(aid)
            unique.append(aid)

    # Reverse: worst-ranked apps first (low rating targets)
    unique.reverse()
    return unique

# ─────────────────────────────────────────
# PHASE 2 — FETCH FULL DETAILS for one app
# ─────────────────────────────────────────
def fetch_full_details(app_id):
    """
    Fetch ALL available fields for an app.
    Returns dict or None if failed.
    """
    try:
        d = get_app_details(
            app_id,
            lang='en',
            country='us'
        )
        return d
    except Exception as e:
        print(f"Fetch error {app_id}: {e}")
        return None

# ─────────────────────────────────────────
# PHASE 3 — FILTER using full details
# ─────────────────────────────────────────
def apply_filter(d, max_rating, max_installs):
    """
    Returns (passes: bool, fail_reason: str)
    Checks all criteria using fully-fetched app data.
    """
    if d is None:
        return False, "fetch_failed"

    # Government/official app skip
    dev = str(d.get('developer','') or '').lower()
    if any(g in dev for g in GOV_WORDS):
        return False, "government"

    # Rating check  (0.0 = new app = allowed, that's fine)
    rating = float(d.get('score') or 0.0)
    if rating > max_rating:
        return False, f"rating_{rating}"

    # Install check
    raw_inst = d.get('minInstalls') or d.get('realInstalls') or 0
    installs = int(raw_inst) if raw_inst else 0
    if installs > max_installs:
        return False, f"installs_{installs}"

    # Must have some kind of contact email
    email, src = get_best_email(d)
    if not email:
        return False, "no_email"

    return True, "ok"

# ─────────────────────────────────────────
# PHASE 4 — PERSONALIZED EMAIL
# Uses description + website for personalization
# ─────────────────────────────────────────
def build_email(d, contact_info, email_prompt, sender_email):
    app_name = str(d.get('title','Unknown App'))
    dev_name = str(d.get('developer','') or '').strip()
    if not dev_name or len(dev_name) < 2 or len(dev_name) > 35:
        dev_name = "Developer"

    rating      = float(d.get('score') or 0.0)
    raw_inst    = d.get('minInstalls') or d.get('realInstalls') or 0
    installs    = int(raw_inst) if raw_inst else 0
    genre       = str(d.get('genre','') or '')
    description = str(d.get('description','') or '')[:600]
    website_url = str(d.get('developerWebsite','') or '')
    privacy_url = str(d.get('privacyPolicy','') or '')
    summary     = str(d.get('summary','') or '')

    # Grab website text for extra personalization
    site_text = fetch_website_text(website_url) if website_url else ""

    contact_html = str(contact_info).replace('\n','<br>')

    prompt = f"""{email_prompt}

You are writing a personalized cold outreach email to an independent app developer.

Context about their app:
- App Name: {app_name}
- Developer / Studio: {dev_name}
- Category: {genre}
- Short Summary: {summary}
- Full Description (excerpt): {description}
- Developer Website Content: {site_text if site_text else "Not available"}

Instructions:
1. Start EXACTLY with: Dear {dev_name},
2. In the opening line, mention ONE specific thing you noticed about their app
   (use the description/summary/website content above — make it feel genuine).
3. Briefly introduce the value proposition from our side.
4. End with a simple call-to-action.
5. Keep it under 200 words total. Sound human, warm, and concise.
6. Plain text only — NO markdown (**bold**, *italic*, # headers).
7. Use <br> for line breaks.
8. Do NOT mention their rating or install numbers.

Output format (nothing else, no preamble):
SUBJECT: [subject line]
BODY: [email body]"""

    try:
        resp    = groq.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=700
        )
        content = resp.choices[0].message.content.strip()

        if "SUBJECT:" in content and "BODY:" in content:
            subject  = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
            raw_body = content.split("BODY:")[1].strip()
        else:
            lines    = content.split('\n')
            subject  = lines[0].replace("Subject:","").replace("SUBJECT:","").strip()
            raw_body = '\n'.join(lines[1:]).strip()

        body = raw_body.replace('**','').replace('*','')
        body = body.replace('\n\n','<br><br>').replace('\n','<br>')

        html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#333;max-width:600px;margin:0 auto;">
{body}<br><br>
{contact_html}<br><br>
<hr style="border:0;border-top:1px solid #eee;margin:20px 0;">
<p style="text-align:center;font-size:11px;color:#aaa;">
<a href="mailto:{sender_email}?subject=Unsubscribe&body=Please remove me." style="color:#aaa;">Unsubscribe</a>
</p></div>"""

        return subject, html

    except Exception as e:
        print(f"Email build error: {e}")
        html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#333;max-width:600px;margin:0 auto;">
Dear {dev_name},<br><br>
I came across your app <b>{app_name}</b> and was genuinely impressed by what you have built.<br>
I would love to explore how we might collaborate — I think there is a great opportunity here.<br><br>
{contact_html}<br><br>
<hr style="border:0;border-top:1px solid #eee;margin:20px 0;">
<p style="text-align:center;font-size:11px;color:#aaa;">
<a href="mailto:{sender_email}?subject=Unsubscribe&body=Please remove me." style="color:#aaa;">Unsubscribe</a>
</p></div>"""
        return f"Quick question about {app_name}", html

# ─────────────────────────────────────────
# CORE ENGINE
# ─────────────────────────────────────────
def engine(max_installs, max_rating, contact_info, email_prompt):
    global state
    cid = state["chat_id"]

    # ── per-session counters ──
    c = {"fetched":0, "gov":0, "rating":0, "installs":0,
         "no_email":0, "dup":0, "sent":0, "fail":0}

    # ══════════════════════════════════════
    # OUTER LOOP — keywords
    # ══════════════════════════════════════
    while state["kw_index"] < len(state["keywords"]):

        # pause / stop check
        while state["status"] == "PAUSED": time.sleep(1)
        if state["status"] == "IDLE" or state["total_leads"] >= 200: break

        kw = state["keywords"][state["kw_index"]]

        # ── Build app queue for this keyword if not done yet ──
        if not state["app_queue"]:
            msg(cid, f"🔍 *Keyword {state['kw_index']+1}/{len(state['keywords'])}:* `{kw}`\nCollecting app IDs...")

            ids = collect_app_ids(kw)
            # Remove globally seen
            ids = [i for i in ids if i not in state["scraped_ids"]]

            if not ids:
                msg(cid, f"⚠️ No new apps for `{kw}`. Skipping...")
                state["kw_index"] += 1
                continue

            state["app_queue"] = ids
            state["app_index"] = 0
            msg(cid, f"📦 *{len(ids)} apps* queued for `{kw}`\nFetching full details + filtering now...")

        # ══════════════════════════════════
        # INNER LOOP — apps in queue
        # ══════════════════════════════════
        while state["app_index"] < len(state["app_queue"]):

            # pause / stop check
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE" or state["total_leads"] >= 200: break

            app_id = state["app_queue"][state["app_index"]]
            state["app_index"] += 1

            # Skip globally scraped
            if app_id in state["scraped_ids"]: continue
            state["scraped_ids"].add(app_id)

            # ── PHASE 2: Fetch FULL details ──
            d = fetch_full_details(app_id)
            if d is None: continue
            c["fetched"] += 1

            # ── PHASE 3: Filter ──
            passes, reason = apply_filter(d, max_rating, max_installs)

            if not passes:
                if "government" in reason:  c["gov"]      += 1
                elif "rating"   in reason:  c["rating"]   += 1
                elif "installs" in reason:  c["installs"] += 1
                elif "no_email" in reason:  c["no_email"] += 1
                continue

            email, esrc = get_best_email(d)

            # Duplicate email check
            if email in state["sent_emails"]:
                c["dup"] += 1
                continue

            # ── Check sender availability ──
            try:
                senders   = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
                available = [s for s in senders if int(s.get('sent',0)) < int(s.get('limit',1))]
            except:
                time.sleep(3)
                continue

            if not available:
                msg(cid, "⚠️ All senders hit daily limit! Pausing.")
                state["status"] = "PAUSED"
                break

            sender = available[0]

            # Pull display info
            title    = str(d.get('title','Unknown'))
            dev      = str(d.get('developer','Developer'))
            rating   = float(d.get('score') or 0.0)
            raw_inst = d.get('minInstalls') or d.get('realInstalls') or 0
            installs = int(raw_inst) if raw_inst else 0
            etag     = {"dev":"📧 Dev","support":"📩 Support","extracted":"📬 Extracted"}.get(esrc,"📬")

            msg(cid,
                f"✨ *Lead Found!*\n"
                f"App: *{title}*\n"
                f"Dev: {dev}\n"
                f"Rating: `{rating}` | Installs: `{installs:,}`\n"
                f"{etag}: `{email}`\n"
                f"🖊 Building personalized email...")

            # ── PHASE 4: Build personalized email ──
            subject, body = build_email(d, contact_info, email_prompt, sender['email'])

            # ── Save lead to Sheet ──
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
                    "link":         d.get('url',''),
                    "category":     d.get('genre',''),
                    "website":      d.get('developerWebsite',''),
                    "updated":      str(d.get('updated',''))
                }, timeout=15)
            except: pass

            state["sent_emails"].add(email)

            # ── Send email ──
            try:
                r2   = requests.post(sender['url'],
                         json={"action":"send_email","to":email,"subject":subject,"body":body},
                         timeout=30)
                resp = r2.text.strip()
            except Exception as se:
                resp = f"Error: {se}"

            if resp == "Success":
                try:
                    requests.post(SHEET_URL, json={"action":"increment_sender","email":sender['email']}, timeout=15)
                except: pass

                state["total_leads"] += 1
                c["sent"] += 1

                msg(cid,
                    f"✅ *Lead #{state['total_leads']} Sent!*\n"
                    f"To: `{email}` ({esrc})\n"
                    f"Via: {sender['email']}\n"
                    f"Progress: App {state['app_index']}/{len(state['app_queue'])} | KW {state['kw_index']+1}/{len(state['keywords'])}")

                # ── Wait 1-2 min, then resume exact position ──
                wait = random.randint(60, 120)
                msg(cid, f"⏳ Waiting *{wait}s* then resuming exact position...")
                for _ in range(wait):
                    if state["status"] != "RUNNING": break
                    time.sleep(1)

            else:
                c["fail"] += 1
                msg(cid, f"❌ Send failed `{email}`: {resp}")

        # ── End of app queue for this keyword ──
        if state["status"] == "RUNNING":
            msg(cid,
                f"📌 *Keyword done:* `{kw}`\n"
                f"Fetched: {c['fetched']} | Sent: {c['sent']}\n"
                f"No email: {c['no_email']} | Dup: {c['dup']}\n"
                f"Rating↑: {c['rating']} | Installs↑: {c['installs']}")

            # Reset queue, advance keyword
            state["app_queue"] = []
            state["app_index"] = 0
            state["kw_index"] += 1

    # ── All done ──
    if state["status"] == "RUNNING":
        msg(cid,
            f"🎉 *All Done!*\n"
            f"Total leads sent: *{state['total_leads']}*\n"
            f"Apps fetched: {c['fetched']}\n"
            f"No email: {c['no_email']} | Duplicate: {c['dup']}\n"
            f"Rating fail: {c['rating']} | Install fail: {c['installs']}")
        state["status"] = "IDLE"
        bot.send_message(cid, "✅ Automation complete!", reply_markup=get_keyboard())

# ─────────────────────────────────────────
# START ENGINE
# ─────────────────────────────────────────
def start_engine():
    global state
    cid = state["chat_id"]
    try:
        msg(cid, "🔄 Fetching Sheet settings...")

        res = requests.post(SHEET_URL, json={"action":"get_settings"}, timeout=20).json()

        try:    max_installs = int(str(res.get('max_installs','100000')).replace(',','').strip())
        except: max_installs = 100000

        try:    max_rating = float(str(res.get('max_rating','4.5')).strip())
        except: max_rating = 4.5

        contact_info = str(res.get('contact_info',''))
        email_prompt = str(res.get('email_prompt','Write a professional outreach email.'))
        niche        = str(res.get('niche','mobile apps'))
        kw_prompt    = str(res.get('keyword_prompt','Generate Play Store search terms for'))

        # Load sent emails from Sheet DB
        try:
            db = requests.post(SHEET_URL, json={"action":"get_existing_emails"}, timeout=20).json()
            state["sent_emails"] = set(db) if isinstance(db, list) else set()
        except:
            state["sent_emails"] = set()

        msg(cid,
            f"✅ *Settings loaded*\n"
            f"Rating ≤ `{max_rating}` | Installs ≤ `{max_installs:,}`\n"
            f"Known emails in DB: `{len(state['sent_emails'])}`")

        # Generate keywords if starting fresh
        if not state["keywords"]:
            msg(cid, "🧠 Generating keywords with AI...")

            kp = f"""{kw_prompt}
Niche: {niche}

Give me 200 unique short search terms (2-5 words) someone types in Google Play Store.
- Comma separated ONLY
- No word 'keywords', no numbers, no bullets, no explanation
- Just the comma-separated list"""

            r = groq.chat.completions.create(
                messages=[{"role":"user","content":kp}],
                model="llama-3.1-8b-instant",
                max_tokens=2000
            )
            raw = r.choices[0].message.content.replace('\n',',').replace('\r',',')
            cleaned = []
            for k in raw.split(','):
                k = re.sub(r'^\d+[\.\)\-\s]+','',k)
                k = k.replace('keyword','').replace('**','').replace('*','').replace('#','')
                k = k.replace('"','').replace("'",'').strip()
                if 2 < len(k) < 60: cleaned.append(k)

            if not cleaned:
                msg(cid, "❌ Keyword generation failed. Try again.")
                state["status"] = "IDLE"
                bot.send_message(cid, ".", reply_markup=get_keyboard())
                return

            state["keywords"]   = cleaned
            state["kw_index"]   = 0
            state["app_queue"]  = []
            state["app_index"]  = 0
            state["total_leads"]= 0
            state["scraped_ids"]= set()

            msg(cid, f"✅ *{len(cleaned)} keywords* generated!\nStarting (low-rated apps first)...")

        threading.Thread(
            target=engine,
            args=(max_installs, max_rating, contact_info, email_prompt),
            daemon=True
        ).start()

    except Exception as e:
        state["status"] = "IDLE"
        msg(cid, f"❌ Error: {e}")
        bot.send_message(cid, ".", reply_markup=get_keyboard())

# ─────────────────────────────────────────
# SPAM TEST
# ─────────────────────────────────────────
def run_spam_test(test_email):
    cid = state["chat_id"]
    msg(cid, "🔄 Running Spam Test...")
    try:
        senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
        if not senders:
            msg(cid, "❌ No senders! Add one first.")
            bot.send_message(cid, ".", reply_markup=get_keyboard())
            return

        sender = senders[0]
        res    = requests.post(SHEET_URL, json={"action":"get_settings"}, timeout=15).json()

        # Fake app data for test
        fake = {
            'title':'Demo Budget Tracker','developer':'Indie Studio',
            'score':3.1,'minInstalls':2000,
            'description':'A simple app to track daily expenses, income, and savings goals for individuals.',
            'summary':'Personal budget tracker for daily use',
            'developerWebsite':'','genre':'Finance',
            'developerEmail':test_email,'url':'','updated':'','privacyPolicy':''
        }

        subject, body = build_email(
            fake,
            str(res.get('contact_info','')),
            str(res.get('email_prompt','Write a professional outreach email.')),
            sender['email']
        )

        r2 = requests.post(sender['url'],
             json={"action":"send_email","to":test_email,"subject":subject,"body":body},
             timeout=30)

        if r2.text.strip() == "Success":
            msg(cid, f"✅ Test email sent!\nTo: `{test_email}`\nVia: {sender['email']}")
        else:
            msg(cid, f"❌ Failed: {r2.text}")

        bot.send_message(cid, ".", reply_markup=get_keyboard())

    except Exception as e:
        msg(cid, f"❌ Error: {e}")
        bot.send_message(cid, ".", reply_markup=get_keyboard())

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
                    msg(state["chat_id"], "⏰ Scheduled time! Starting...")
                    bot.send_message(state["chat_id"], ".", reply_markup=get_keyboard())
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
    bot.reply_to(message, "👋 *Welcome Boss!*", parse_mode="Markdown", reply_markup=get_keyboard())

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    cid = call.message.chat.id
    d   = call.data

    if d == "back_to_main":
        state["status"] = "IDLE"
        bot.send_message(cid, "🔙 Main Menu.", reply_markup=get_keyboard())

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
        bot.send_message(cid, f"📝 Deploy in Apps Script then send URL:\n\n`{code}`",
            parse_mode="Markdown", reply_markup=back_kb())
        state["status"] = "WAITING_URL"

    elif d.startswith("del_"):
        e2 = d.split("del_")[1]
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Delete", callback_data=f"cfm_{e2}"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel_del"))
        bot.send_message(cid, f"Delete *{e2}*?", parse_mode="Markdown", reply_markup=mk)

    elif d.startswith("cfm_"):
        e2 = d.split("cfm_")[1]
        requests.post(SHEET_URL, json={"action":"delete_sender","email":e2}, timeout=15)
        bot.send_message(cid, f"🗑️ Deleted *{e2}*", parse_mode="Markdown")

    elif d == "cancel_del":
        bot.send_message(cid, "Cancelled.")

@bot.message_handler(func=lambda m: True)
def handle(message):
    text = message.text.strip()
    state["chat_id"] = message.chat.id

    if text == "🔙 Back":
        state["status"]    = "IDLE"
        state["tmp_url"]   = None
        state["tmp_email"] = None
        bot.reply_to(message, "🔙 Main Menu.", reply_markup=get_keyboard())
        return

    # ── Sender setup ──
    if state["status"] == "WAITING_URL":
        if "script.google.com" in text:
            state["tmp_url"]  = text
            state["status"]   = "WAITING_EMAIL"
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
                "action":"add_sender","email":state["tmp_email"],
                "url":state["tmp_url"],"limit":int(text)
            }, timeout=15)
            bot.reply_to(message, f"🎉 Sender *{state['tmp_email']}* added! {text}/day",
                parse_mode="Markdown", reply_markup=get_keyboard())
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
            bot.reply_to(message, f"✅ Scheduled at *{p}* daily (Dhaka)!", parse_mode="Markdown", reply_markup=get_keyboard())
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

    # ── Main menu ──
    if text == "📧 Manage Senders":
        try:
            senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
        except:
            bot.reply_to(message, "❌ Cannot reach Sheet.", reply_markup=get_keyboard())
            return
        mk  = InlineKeyboardMarkup()
        txt = "📋 *Senders:*\n\n"
        if not senders:
            txt += "_None yet._\n"
        else:
            for i,s in enumerate(senders):
                txt += f"{i+1}. `{s.get('email')}` — {s.get('sent',0)}/{s.get('limit',0)}\n"
                mk.add(InlineKeyboardButton(f"🗑️ {s.get('email')}", callback_data=f"del_{s.get('email')}"))
        mk.add(InlineKeyboardButton("➕ Add Sender", callback_data="add_sender"))
        mk.add(InlineKeyboardButton("🔙 Back",       callback_data="back_to_main"))
        bot.reply_to(message, txt, parse_mode="Markdown", reply_markup=mk)

    elif text == "🚀 Start Automation":
        if state["status"] in ["IDLE","SCHEDULED"]:
            state["status"] = "RUNNING"
            bot.reply_to(message, "🚀 *Starting...*", parse_mode="Markdown", reply_markup=get_keyboard())
            threading.Thread(target=start_engine, daemon=True).start()

    elif text == "🛑 Stop":
        if state["status"] == "RUNNING":
            state["status"] = "PAUSED"
            bot.reply_to(message,
                "🛑 *Paused.*\nProgress saved — keyword & app position remembered.",
                parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            state["status"] = "RUNNING"
            bot.reply_to(message, "▶️ *Resuming from exact position...*", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "⏹️ Full Reset":
        state.update({
            "status":"IDLE","keywords":[],"kw_index":0,
            "app_queue":[],"app_index":0,"total_leads":0,"scraped_ids":set()
        })
        bot.reply_to(message, "⏹️ *Fully reset.*", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "📅 Schedule":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TIME"
            bot.reply_to(message, "⏰ Send time (*02:30 PM* or *14:30*)", parse_mode="Markdown", reply_markup=back_kb())

    elif text == "❌ Cancel Schedule":
        state["status"]         = "IDLE"
        state["scheduled_time"] = None
        bot.reply_to(message, "❌ Cancelled.", reply_markup=get_keyboard())

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
