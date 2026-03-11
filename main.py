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
    "status":         "IDLE",   # IDLE / SCRAPING / FILTERING / EMAILING / PAUSED / SCHEDULED
    "keywords":       [],
    "kw_index":       0,
    "scraped_ids":    set(),    # globally seen appIds
    "total_scraped":  0,
    "total_emailed":  0,
    "chat_id":        None,
    "scheduled_time": None,
    "tmp_url":        None,
    "tmp_email":      None,
}

GOV = ['gov','government','ministry','department','council',
       'national','authority','federal','municipal']

# ─── KEYBOARDS ───────────────────────────────────────────────
def kb():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    s = state["status"]
    if s == "IDLE":
        m.add(KeyboardButton("🔍 Phase 1: Scrape"),  KeyboardButton("📊 Phase 2: Filter & Email"))
        m.add(KeyboardButton("📅 Schedule"),          KeyboardButton("🧪 Spam Test"))
        m.add(KeyboardButton("📧 Senders"))
    elif s in ["SCRAPING", "FILTERING", "EMAILING"]:
        m.add(KeyboardButton("🛑 Pause"))
    elif s == "PAUSED":
        m.add(KeyboardButton("▶️ Resume"),  KeyboardButton("⏹️ Reset"))
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
#  PHASE 1 — SCRAPE ALL APPS → SAVE RAW TO SHEET
#  No filtering here. Just collect maximum data.
# ════════════════════════════════════════════════════════════
def phase1_scrape():
    cid = state["chat_id"]
    state["status"] = "SCRAPING"

    try:
        # Load settings
        res          = requests.post(SHEET_URL, json={"action":"get_settings"}, timeout=20).json()
        niche        = str(res.get('niche','mobile apps'))
        kw_prompt    = str(res.get('keyword_prompt','Generate Play Store search terms for'))

        # Load already scraped IDs from sheet to avoid re-scraping
        try:
            existing = requests.post(SHEET_URL, json={"action":"get_scraped_ids"}, timeout=20).json()
            state["scraped_ids"] = set(existing) if isinstance(existing, list) else set()
        except:
            state["scraped_ids"] = set()

        send(f"✅ Already in DB: *{len(state['scraped_ids'])}* apps\n"
             f"Will skip these and only scrape new ones.")

        # Generate keywords if needed
        if not state["keywords"]:
            send("🧠 Generating keywords with AI...")
            p = f"""{kw_prompt}
Niche: {niche}
Give 200 unique short search terms (2-5 words) someone types in Google Play Store.
Comma separated ONLY. No 'keywords' word. No numbers. No bullets. No explanation."""

            r = ai.chat.completions.create(
                messages=[{"role":"user","content":p}],
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
                send("❌ Keyword generation failed. Try again.")
                state["status"] = "IDLE"
                bot.send_message(cid,".", reply_markup=kb())
                return

            state["keywords"]  = cleaned
            state["kw_index"]  = 0
            state["total_scraped"] = 0
            send(f"✅ *{len(cleaned)} keywords* ready! Starting scrape...")

        # ── Iterate keywords ──────────────────────────────────
        while state["kw_index"] < len(state["keywords"]):
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE": break

            kw = state["keywords"][state["kw_index"]]
            send(f"🔍 *KW {state['kw_index']+1}/{len(state['keywords'])}:* `{kw}`")

            # Search 8 variations
            raw_ids = []
            for q in [kw, f"{kw} app", f"{kw} free", f"best {kw}",
                      f"new {kw}", f"{kw} simple", f"{kw} lite", f"{kw} basic"]:
                try:
                    results = search(q, lang='en', country='us', n_hits=100)
                    for r in results: raw_ids.append(r['appId'])
                    time.sleep(0.2)
                except: continue

            # Deduplicate
            seen_kw, ids = set(), []
            for i in raw_ids:
                if i not in seen_kw and i not in state["scraped_ids"]:
                    seen_kw.add(i)
                    ids.append(i)

            send(f"📦 *{len(ids)}* new apps to fetch for `{kw}`")

            kw_count = 0
            batch    = []   # collect rows to batch-save (max 50 at a time)

            for app_id in ids:
                while state["status"] == "PAUSED": time.sleep(1)
                if state["status"] == "IDLE": break

                state["scraped_ids"].add(app_id)

                # Fetch full details
                try:
                    d = gplay(app_id, lang='en', country='us')
                except:
                    continue

                # Extract all useful fields
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

                # Add to batch
                batch.append({
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
                })

                kw_count += 1
                state["total_scraped"] += 1

                # Save to sheet in batches of 50
                if len(batch) >= 50:
                    try:
                        requests.post(SHEET_URL,
                            json={"action":"save_raw_batch","rows":batch},
                            timeout=30)
                        send(f"💾 Saved batch | Total scraped: *{state['total_scraped']}*")
                        batch = []
                    except Exception as e:
                        print(f"Batch save error: {e}")

                time.sleep(0.1)  # small delay to avoid rate limit

            # Save remaining batch
            if batch:
                try:
                    requests.post(SHEET_URL,
                        json={"action":"save_raw_batch","rows":batch},
                        timeout=30)
                except: pass

            send(f"✅ KW `{kw}` done — {kw_count} apps scraped\n"
                 f"Total in DB: *{state['total_scraped']}*")

            state["kw_index"] += 1

        # Phase 1 complete
        if state["status"] != "IDLE":
            send(f"🎉 *Phase 1 Complete!*\n"
                 f"Total apps scraped: *{state['total_scraped']}*\n"
                 f"All saved to *Raw Leads* sheet.\n\n"
                 f"Now press *📊 Phase 2: Filter & Email* to start sending!")
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb())

    except Exception as e:
        state["status"] = "IDLE"
        send(f"❌ Phase 1 Error: {e}")
        bot.send_message(cid, ".", reply_markup=kb())


# ════════════════════════════════════════════════════════════
#  PHASE 2 — FILTER RAW LEADS → QUALIFIED → SEND EMAILS
# ════════════════════════════════════════════════════════════
def phase2_filter_and_email():
    cid = state["chat_id"]
    state["status"] = "FILTERING"

    try:
        # Load settings
        res          = requests.post(SHEET_URL, json={"action":"get_settings"}, timeout=20).json()
        max_installs = int(str(res.get('max_installs','100000')).replace(',','').strip())
        max_rating   = float(str(res.get('max_rating','4.5')).strip())
        contact_info = str(res.get('contact_info',''))
        email_prompt = str(res.get('email_prompt','Write a professional outreach email.'))

        send(f"📊 *Phase 2 Starting*\n"
             f"Filter: Rating ≤ `{max_rating}` | Installs ≤ `{max_installs:,}`\n"
             f"Loading raw data from sheet...")

        # Get all raw rows from sheet
        raw_data = requests.post(SHEET_URL,
            json={"action":"get_raw_leads"},
            timeout=30).json()

        if not raw_data or not isinstance(raw_data, list):
            send("❌ No raw data found. Run Phase 1 first!")
            state["status"] = "IDLE"
            bot.send_message(cid,".", reply_markup=kb())
            return

        send(f"📦 *{len(raw_data)}* raw apps loaded. Filtering now...")

        # ── FILTER ───────────────────────────────────────────
        qualified = []
        seen_emails = set()

        # Load already emailed emails
        try:
            existing = requests.post(SHEET_URL,
                json={"action":"get_qualified_emails"},
                timeout=20).json()
            seen_emails = set(existing) if isinstance(existing, list) else set()
        except:
            seen_emails = set()

        stats = {"gov":0, "rating":0, "installs":0, "no_email":0, "dup":0, "passed":0}

        for row in raw_data:
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE": break

            dev      = str(row.get('dev_name','') or '').lower()
            rating   = float(row.get('rating') or 0.0)
            installs = int(row.get('installs') or 0)
            email    = str(row.get('email','') or '').strip().lower()

            # Government skip
            if any(g in dev for g in GOV):
                stats["gov"] += 1
                continue

            # Rating filter: <= max_rating (0.0 = new app = allowed)
            if rating > max_rating:
                stats["rating"] += 1
                continue

            # Install filter
            if installs > max_installs:
                stats["installs"] += 1
                continue

            # Email check
            if not email or '@' not in email:
                stats["no_email"] += 1
                continue

            # Duplicate check
            if email in seen_emails:
                stats["dup"] += 1
                continue

            seen_emails.add(email)
            qualified.append(row)
            stats["passed"] += 1

        send(f"✅ *Filter Complete!*\n"
             f"Passed: *{stats['passed']}*\n"
             f"Rating fail: {stats['rating']} | Install fail: {stats['installs']}\n"
             f"No email: {stats['no_email']} | Duplicate: {stats['dup']} | Gov: {stats['gov']}\n\n"
             f"Saving qualified leads & starting emails...")

        if not qualified:
            send("⚠️ No qualified leads found. Adjust filter settings in Sheet and try again.")
            state["status"] = "IDLE"
            bot.send_message(cid,".", reply_markup=kb())
            return

        # Save all qualified leads to Qualified Leads tab
        try:
            requests.post(SHEET_URL,
                json={"action":"save_qualified_batch","rows":qualified},
                timeout=30)
        except Exception as e:
            send(f"⚠️ Could not save qualified leads: {e}")

        # ── EMAIL PHASE ──────────────────────────────────────
        state["status"]       = "EMAILING"
        state["total_emailed"] = 0

        send(f"📧 *Starting email phase...*\n"
             f"Sending to *{len(qualified)}* qualified leads\nWaiting 1-2 min between each.")

        for row in qualified:
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE" or state["total_emailed"] >= 200: break

            # Check sender
            try:
                senders   = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
                available = [s for s in senders if int(s.get('sent',0)) < int(s.get('limit',1))]
            except:
                time.sleep(3)
                continue

            if not available:
                send("⚠️ All senders hit daily limit! Pausing.")
                state["status"] = "PAUSED"
                break

            sender = available[0]
            email  = str(row.get('email',''))
            esrc   = str(row.get('email_source','dev'))

            # Build personalized email using stored data
            subject, body = build_email(row, contact_info, email_prompt, sender['email'])

            # Send
            try:
                r2   = requests.post(sender['url'],
                         json={"action":"send_email","to":email,"subject":subject,"body":body},
                         timeout=30)
                resp = r2.text.strip()
            except Exception as se:
                resp = f"Error: {se}"

            if resp == "Success":
                try:
                    requests.post(SHEET_URL,
                        json={"action":"increment_sender","email":sender['email']},
                        timeout=15)
                    # Mark as emailed in sheet
                    requests.post(SHEET_URL,
                        json={"action":"mark_emailed","email":email},
                        timeout=15)
                except: pass

                state["total_emailed"] += 1
                etag = {"dev":"📧","support":"📩","extracted":"📬"}.get(esrc,"📬")

                send(f"✅ *Email #{state['total_emailed']} Sent!*\n"
                     f"App: {row.get('app_name','?')}\n"
                     f"{etag} To: `{email}`\n"
                     f"Via: {sender['email']}")

                # Wait 1–2 min
                wait = random.randint(60, 120)
                send(f"⏳ Waiting *{wait}s*...")
                for _ in range(wait):
                    if state["status"] != "EMAILING": break
                    time.sleep(1)
            else:
                send(f"❌ Failed to `{email}`: {resp}")

        if state["status"] == "EMAILING":
            send(f"🎉 *Phase 2 Complete!*\n"
                 f"Total emails sent: *{state['total_emailed']}*")
            state["status"] = "IDLE"
            bot.send_message(cid,".", reply_markup=kb())

    except Exception as e:
        state["status"] = "IDLE"
        send(f"❌ Phase 2 Error: {e}")
        bot.send_message(cid,".", reply_markup=kb())


# ─── PERSONALIZED EMAIL BUILDER ──────────────────────────────
def build_email(row, contact_info, email_prompt, sender_email):
    app_name    = str(row.get('app_name','Unknown App'))
    dev_name    = str(row.get('dev_name','') or '').strip()
    if not dev_name or len(dev_name) < 2 or len(dev_name) > 35:
        dev_name = "Developer"

    genre       = str(row.get('genre','') or '')
    summary     = str(row.get('summary','') or '')
    description = str(row.get('description','') or '')[:500]
    website_url = str(row.get('website','') or '')

    # Try to fetch website for extra personalization
    site_text = ""
    if website_url and "http" in website_url:
        try:
            r = requests.get(website_url, timeout=5,
                headers={"User-Agent":"Mozilla/5.0"})
            t = re.sub(r'<[^>]+',' ', r.text)
            site_text = re.sub(r'\s+',' ', t).strip()[:400]
        except: pass

    contact_html = str(contact_info).replace('\n','<br>')

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
2. Mention ONE specific thing about their app (from above info)
3. Under 150 words total
4. Plain text only — no markdown, no bold, no headers
5. Use <br> for line breaks
6. Do NOT mention rating or install count
7. Clear call to action at end

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
            subject  = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
            raw_body = content.split("BODY:")[1].strip()
        else:
            lines    = content.split('\n')
            subject  = lines[0].replace("Subject:","").replace("SUBJECT:","").strip()
            raw_body = '\n'.join(lines[1:]).strip()

        body = raw_body.replace('**','').replace('*','')
        body = body.replace('\n\n','<br><br>').replace('\n','<br>')

        html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#333;max-width:600px;margin:0 auto;">
{body}<br><br>{contact_html}<br><br>
<hr style="border:0;border-top:1px solid #eee;margin:16px 0;">
<p style="text-align:center;font-size:11px;color:#bbb;">
<a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me." style="color:#bbb;">Unsubscribe</a>
</p></div>"""
        return subject, html

    except Exception as e:
        print(f"Email build error: {e}")
        html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#333;max-width:600px;margin:0 auto;">
Dear {dev_name},<br><br>I came across your app <b>{app_name}</b> and would love to explore a collaboration.<br><br>
{contact_html}<br><br>
<hr style="border:0;border-top:1px solid #eee;margin:16px 0;">
<p style="text-align:center;font-size:11px;color:#bbb;">
<a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me." style="color:#bbb;">Unsubscribe</a>
</p></div>"""
        return f"Quick question about {app_name}", html


# ─── SPAM TEST ───────────────────────────────────────────────
def run_spam_test(test_email):
    send("🔄 Running Spam Test...")
    try:
        senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
        if not senders:
            send("❌ No senders! Add one first.")
            bot.send_message(state["chat_id"],".", reply_markup=kb())
            return
        sender = senders[0]
        res    = requests.post(SHEET_URL, json={"action":"get_settings"}, timeout=15).json()
        fake   = {
            "app_name":"Demo Budget Tracker","dev_name":"Indie Studio",
            "rating":3.1,"installs":2000,
            "description":"A simple app to track daily expenses and savings goals.",
            "summary":"Personal budget tracker","website":"","genre":"Finance"
        }
        subject, body = build_email(fake,
            str(res.get('contact_info','')),
            str(res.get('email_prompt','Write a professional outreach email.')),
            sender['email'])
        r2 = requests.post(sender['url'],
             json={"action":"send_email","to":test_email,"subject":subject,"body":body},
             timeout=30)
        if r2.text.strip() == "Success":
            send(f"✅ Test sent to `{test_email}` via {sender['email']}")
        else:
            send(f"❌ Failed: {r2.text}")
        bot.send_message(state["chat_id"],".", reply_markup=kb())
    except Exception as e:
        send(f"❌ Error: {e}")
        bot.send_message(state["chat_id"],".", reply_markup=kb())


# ─── SCHEDULER ───────────────────────────────────────────────
def run_scheduler():
    tz = pytz.timezone('Asia/Dhaka')
    while True:
        try:
            if state["status"] == "SCHEDULED" and state["scheduled_time"] and state["chat_id"]:
                if datetime.now(tz).strftime("%H:%M") == state["scheduled_time"]:
                    state["status"] = "SCRAPING"
                    send("⏰ Scheduled! Starting Phase 1...")
                    bot.send_message(state["chat_id"],".", reply_markup=kb())
                    threading.Thread(target=phase1_scrape, daemon=True).start()
                    time.sleep(61)
        except Exception as e:
            print(f"Scheduler: {e}")
        time.sleep(10)


# ─── BOT HANDLERS ────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def welcome(message):
    state["chat_id"] = message.chat.id
    state["status"]  = "IDLE"
    bot.reply_to(message,
        "👋 *Welcome Boss!*\n\n"
        "*🔍 Phase 1:* Scrape all apps → save to Raw Leads sheet\n"
        "*📊 Phase 2:* Filter → save Qualified Leads → send emails",
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
    elif d.startswith("del_"):
        e2 = d.split("del_")[1]
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Delete", callback_data=f"cfm_{e2}"),
               InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
        bot.send_message(cid, f"Delete *{e2}*?", parse_mode="Markdown", reply_markup=mk)
    elif d.startswith("cfm_"):
        e2 = d.split("cfm_")[1]
        requests.post(SHEET_URL, json={"action":"delete_sender","email":e2}, timeout=15)
        bot.send_message(cid, f"🗑️ Deleted *{e2}*", parse_mode="Markdown")
    elif d == "cancel":
        bot.send_message(cid,"Cancelled.")

@bot.message_handler(func=lambda m: True)
def handle(message):
    text = message.text.strip()
    state["chat_id"] = message.chat.id

    if text == "🔙 Back":
        state["status"]    = "IDLE"
        state["tmp_url"]   = None
        state["tmp_email"] = None
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
            requests.post(SHEET_URL, json={
                "action":"add_sender","email":state["tmp_email"],
                "url":state["tmp_url"],"limit":int(text)
            }, timeout=15)
            bot.reply_to(message, f"🎉 Sender *{state['tmp_email']}* added! {text}/day",
                parse_mode="Markdown", reply_markup=kb())
            state["status"]    = "IDLE"
            state["tmp_url"]   = None
            state["tmp_email"] = None
        else:
            bot.reply_to(message,"❌ Send a number.", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_TIME":
        p = parse_time(text)
        if p:
            state["scheduled_time"] = p
            state["status"]         = "SCHEDULED"
            bot.reply_to(message, f"✅ Scheduled at *{p}* daily (Dhaka)!",
                parse_mode="Markdown", reply_markup=kb())
        else:
            bot.reply_to(message,"❌ Format: 02:30 PM or 14:30", reply_markup=back_kb())
        return

    elif state["status"] == "WAITING_TEST":
        if "@" in text:
            state["status"] = "IDLE"
            bot.reply_to(message, f"Sending test to *{text}*...", parse_mode="Markdown")
            threading.Thread(target=run_spam_test, args=(text,), daemon=True).start()
        else:
            bot.reply_to(message,"❌ Invalid email.", reply_markup=back_kb())
        return

    # ── Main buttons ──────────────────────────────────────────
    if text == "🔍 Phase 1: Scrape":
        if state["status"] == "IDLE":
            threading.Thread(target=phase1_scrape, daemon=True).start()

    elif text == "📊 Phase 2: Filter & Email":
        if state["status"] == "IDLE":
            threading.Thread(target=phase2_filter_and_email, daemon=True).start()

    elif text == "🛑 Pause":
        if state["status"] in ["SCRAPING","FILTERING","EMAILING"]:
            state["status"] = "PAUSED"
            bot.reply_to(message,"🛑 *Paused.* Progress saved.",
                parse_mode="Markdown", reply_markup=kb())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            # Determine which phase to resume
            # We store last active phase in status before pausing
            state["status"] = "EMAILING"  # default resume to email phase
            bot.reply_to(message,"▶️ *Resuming...*",
                parse_mode="Markdown", reply_markup=kb())

    elif text == "⏹️ Reset":
        state.update({
            "status":"IDLE","keywords":[],"kw_index":0,
            "scraped_ids":set(),"total_scraped":0,"total_emailed":0
        })
        bot.reply_to(message,"⏹️ *Fully reset.*", parse_mode="Markdown", reply_markup=kb())

    elif text == "📅 Schedule":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TIME"
            bot.reply_to(message,"⏰ Send time (*02:30 PM* or *14:30*)",
                parse_mode="Markdown", reply_markup=back_kb())

    elif text == "❌ Cancel Schedule":
        state["status"]         = "IDLE"
        state["scheduled_time"] = None
        bot.reply_to(message,"❌ Cancelled.", reply_markup=kb())

    elif text == "🧪 Spam Test":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TEST"
            bot.reply_to(message,"📧 Send test email address.", reply_markup=back_kb())

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
                mk.add(InlineKeyboardButton(f"🗑️ {s.get('email')}", callback_data=f"del_{s.get('email')}"))
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
