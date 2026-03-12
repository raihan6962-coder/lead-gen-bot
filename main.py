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
    "status":         "IDLE",   # IDLE / SCRAPING / FILTERING / EMAILING / PAUSED
    "generated_kws":  [],       # list of generated keywords for current base set
    "kw_index":       0,        # index in generated_kws
    "scraped_ids":    set(),    # globally seen appIds
    "total_scraped":  0,
    "total_emailed":  0,
    "chat_id":        None,
    "tmp_url":        None,
    "tmp_email":      None,
    "current_set_id": None,     # id of keyword set being processed
    "qualified_count":0,        # counter for qualified leads collected so far
    "seen_emails":    set(),    # emails already in qualified leads (to avoid duplicates)
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
#  KEYWORD SET MANAGEMENT (via sheet)
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
#  SCHEDULE MANAGEMENT (via sheet)
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
#  AI KEYWORD GENERATION
# ════════════════════════════════════════════════════════════
def generate_keywords_from_base(base):
    """Use Groq to generate 200 related search terms from a base keyword."""
    send(f"🧠 Generating 200 keywords from '{base}'...")
    prompt = f"""Generate 200 unique, short search terms (2-5 words each) related to "{base}" that people might type into Google Play Store.
Return them as a comma-separated list. No numbers, no bullets, no explanations. Just the terms."""
    try:
        r = ai.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=2000
        )
        raw = r.choices[0].message.content.replace('\n',',').replace('\r',',')
        # Clean and split
        terms = []
        for t in raw.split(','):
            t = re.sub(r'^\d+[\.\)\-\s]+','', t)
            t = t.replace('**','').replace('*','').replace('#','').strip()
            if 2 < len(t) < 60 and t not in terms:
                terms.append(t)
        if len(terms) < 10:
            # Fallback if AI fails
            terms = [base, f"best {base}", f"top {base}", f"new {base}", f"{base} app",
                     f"{base} free", f"{base} pro", f"{base} lite", f"{base} 2025",
                     f"popular {base}"]
        send(f"✅ Generated {len(terms)} keywords.")
        return terms
    except Exception as e:
        send(f"❌ Keyword generation failed: {e}")
        # Fallback to just the base
        return [base]

# ════════════════════════════════════════════════════════════
#  FILTER A SINGLE APP (returns True if qualified)
# ════════════════════════════════════════════════════════════
def is_qualified(app_dict, max_rating, max_installs, seen_emails):
    dev = str(app_dict.get('dev_name','') or '').lower()
    rating = float(app_dict.get('rating') or 0.0)
    installs = int(app_dict.get('installs') or 0)
    email = str(app_dict.get('email','') or '').strip().lower()

    # Government skip
    if any(g in dev for g in GOV):
        return False, "gov"
    # Zero rating skip
    if rating == 0.0:
        return False, "zero_rating"
    # Rating filter
    if rating > max_rating:
        return False, "rating"
    # Install filter
    if installs > max_installs:
        return False, "installs"
    # Email check
    if not email or '@' not in email:
        return False, "no_email"
    # Duplicate check
    if email in seen_emails:
        return False, "dup"
    return True, "passed"

# ════════════════════════════════════════════════════════════
#  SAVE A SINGLE QUALIFIED LEAD TO SHEET
# ════════════════════════════════════════════════════════════
def save_qualified_lead(row):
    """Save one qualified lead to the Qualified Leads sheet."""
    try:
        requests.post(SHEET_URL,
            json={"action":"save_qualified_batch","rows":[row]},
            timeout=15)
        return True
    except Exception as e:
        print(f"Error saving qualified lead: {e}")
        return False

# ════════════════════════════════════════════════════════════
#  PHASE 1 — SCRAPE (with per-keyword filtering)
# ════════════════════════════════════════════════════════════
def phase1_scrape():
    cid = state["chat_id"]
    if not cid:
        print("Cannot start phase1: no chat_id")
        return
    state["status"] = "SCRAPING"

    try:
        # Load filter settings
        res = requests.post(SHEET_URL, json={"action":"get_settings"}, timeout=20).json()
        max_installs = int(str(res.get('max_installs','100000')).replace(',','').strip())
        max_rating   = float(str(res.get('max_rating','4.5')).strip())

        # Load already scraped IDs
        try:
            existing = requests.post(SHEET_URL, json={"action":"get_scraped_ids"}, timeout=20).json()
            state["scraped_ids"] = set(existing) if isinstance(existing, list) else set()
        except:
            state["scraped_ids"] = set()

        # Load already qualified emails to avoid duplicates
        try:
            existing_emails = requests.post(SHEET_URL, json={"action":"get_qualified_emails"}, timeout=20).json()
            state["seen_emails"] = set(existing_emails) if isinstance(existing_emails, list) else set()
        except:
            state["seen_emails"] = set()

        # Get next keyword set
        set_id, base_kw = get_next_keyword_set()
        if not set_id:
            send("❌ No pending keyword sets. Add some first.")
            state["status"] = "IDLE"
            bot.send_message(cid, ".", reply_markup=kb())
            return

        state["current_set_id"] = set_id
        state["qualified_count"] = 0

        # Generate 200 keywords from the base
        generated = generate_keywords_from_base(base_kw)
        if not generated:
            send("❌ No keywords generated. Aborting.")
            state["status"] = "IDLE"
            return

        state["generated_kws"] = generated
        state["kw_index"] = 0
        state["total_scraped"] = 0

        send(f"✅ Already in DB: *{len(state['scraped_ids'])}* apps\n"
             f"Already qualified emails: *{len(state['seen_emails'])}*\n"
             f"Starting scrape with {len(generated)} keywords.")

        # Iterate over generated keywords
        while state["kw_index"] < len(state["generated_kws"]):
            while state["status"] == "PAUSED": time.sleep(1)
            if state["status"] == "IDLE":  # Stop pressed
                return

            kw = state["generated_kws"][state["kw_index"]]
            send(f"🔍 *KW {state['kw_index']+1}/{len(state['generated_kws'])}:* `{kw}`")

            # Search 8 variations
            raw_ids = []
            for q in [kw, f"{kw} app", f"{kw} free", f"best {kw}",
                      f"new {kw}", f"{kw} simple", f"{kw} lite", f"{kw} basic"]:
                try:
                    results = search(q, lang='en', country='us', n_hits=100)
                    for r in results: raw_ids.append(r['appId'])
                    time.sleep(0.2)
                except: continue

            # Deduplicate against global scraped_ids
            seen_kw, ids = set(), []
            for i in raw_ids:
                if i not in seen_kw and i not in state["scraped_ids"]:
                    seen_kw.add(i)
                    ids.append(i)

            send(f"📦 *{len(ids)}* new apps to fetch for `{kw}`")

            kw_count = 0
            batch_raw = []  # raw batch to save (all apps, regardless of qualification)
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

                # Check qualification
                qual, reason = is_qualified(app_dict, max_rating, max_installs, state["seen_emails"])
                if qual:
                    # Save to qualified leads immediately
                    if save_qualified_lead(app_dict):
                        state["seen_emails"].add(email)
                        state["qualified_count"] += 1
                        qualified_from_kw += 1

                kw_count += 1
                state["total_scraped"] += 1

                # Save raw batch periodically
                if len(batch_raw) >= 50:
                    try:
                        requests.post(SHEET_URL,
                            json={"action":"save_raw_batch","rows":batch_raw},
                            timeout=30)
                        send(f"💾 Saved raw batch | Total scraped: *{state['total_scraped']}*")
                        batch_raw = []
                    except Exception as e:
                        print(f"Batch save error: {e}")

                time.sleep(0.05)

            # Save remaining raw apps
            if batch_raw:
                try:
                    requests.post(SHEET_URL,
                        json={"action":"save_raw_batch","rows":batch_raw},
                        timeout=30)
                except: pass

            send(f"✅ KW `{kw}` done — {kw_count} apps scraped, {qualified_from_kw} qualified\n"
                 f"Total scraped: *{state['total_scraped']}*, Total qualified so far: *{state['qualified_count']}*")

            state["kw_index"] += 1

        # All keywords processed – mark set as used
        if state["status"] != "IDLE" and state["current_set_id"]:
            mark_keyword_set_used(state["current_set_id"])
            state["current_set_id"] = None
            send(f"🎉 *Phase 1 Complete!* Total scraped: *{state['total_scraped']}*, Qualified leads: *{state['qualified_count']}*")

            if state["status"] == "SCRAPING" and state["qualified_count"] > 0:
                send("⏩ Automatically starting Phase 2 (Emailing qualified leads)...")
                state["status"] = "EMAILING"
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
#  PHASE 2 — EMAIL ONLY (sends to all qualified leads)
# ════════════════════════════════════════════════════════════
def phase2_email_only():
    """Send emails to all qualified leads (already in sheet)."""
    cid = state["chat_id"]
    if not cid:
        print("Cannot start phase2: no chat_id")
        return

    try:
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

            # Get available sender
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

            subject, body_html = build_clean_email(row, sender['email'])

            try:
                r2   = requests.post(sender['url'],
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
                except: pass

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
    """Fetch all pending qualified leads from sheet."""
    try:
        r = requests.post(SHEET_URL, json={"action":"get_pending_qualified_leads"}, timeout=30)
        if r.status_code == 200:
            return r.json() if isinstance(r.json(), list) else []
    except:
        pass
    return []

# ─── CLEAN EMAIL BUILDER (short, with unsubscribe) ──────────
def build_clean_email(row, sender_email):
    """
    Generates a short, professional cold email with unsubscribe link at the bottom.
    """
    app_name    = str(row.get('app_name','Unknown App'))
    dev_name    = str(row.get('dev_name','') or '').strip()
    if not dev_name or len(dev_name) < 2 or len(dev_name) > 35:
        dev_name = "Developer"
    rating      = float(row.get('rating') or 0.0)
    genre       = str(row.get('genre','') or '')
    website_url = str(row.get('website','') or '')
    description = str(row.get('description','') or '')[:300]

    # Determine urgency
    if rating < 3.5:
        urgency = "critical"
    elif rating < 4.0:
        urgency = "moderate"
    else:
        urgency = "noticing some recent reviews?"

    # Business impact based on genre
    genre_lower = genre.lower()
    if any(word in genre_lower for word in ['finance','bank','payment','fintech']):
        impact = "hurts user trust and new sign-ups"
    elif any(word in genre_lower for word in ['shopping','delivery','ecommerce']):
        impact = "can make users switch to competitors"
    elif any(word in genre_lower for word in ['game','gaming']):
        impact = "reduces daily active users and ranking"
    else:
        impact = "affects downloads and retention"

    # Try to fetch a genuine compliment
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
                # use first sentence of description
                sentences = description.split('.')
                if sentences and len(sentences[0]) > 10:
                    compliment = f"I like your focus on {sentences[0].lower()[:50]}."
        except:
            compliment = f"Your app in the {genre} space looks interesting."
    else:
        compliment = f"Your app in the {genre} space looks interesting."

    if not compliment:
        compliment = f"Your app in the {genre} space looks interesting."

    # Build a very concise prompt
    prompt = f"""Write a short cold email (max 120 words) to this developer:

App: {app_name}
Developer: {dev_name}
Current rating: {rating} ({urgency})
Genre: {genre}
Impact: {impact}
Compliment: {compliment}

Rules:
- Subject: include app name, under 50 chars, no spam words.
- Start with a one-sentence genuine compliment.
- Then mention their rating and the problem it causes.
- Briefly introduce Abu Raihan's service (helps recover ratings with genuine reviews).
- Soft CTA: "If you're open to a quick chat, reply or message on WhatsApp."
- End with sign-off:
Best regards,
Abu Raihan
Play Store Review Service Specialist
WhatsApp: +8801902911261
Telegram: https://t.me/abu_raihan69

Output format:
SUBJECT: ...
[blank line]
[email body]"""

    try:
        r = ai.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=400
        )
        content = r.choices[0].message.content.strip()

        if "SUBJECT:" in content:
            parts = content.split("SUBJECT:", 1)[1].strip()
            subject = parts.split("\n", 1)[0].strip()
            body = parts.split("\n", 1)[1].strip() if "\n" in parts else ""
        else:
            subject = f"Quick question about {app_name}"
            body = content

        # Convert to HTML with line breaks
        body_html = body.replace('\n\n','<br><br>').replace('\n','<br>')
        # Add unsubscribe link at the very bottom
        unsubscribe = f'<br><br><hr style="border:0;border-top:1px solid #eee;margin:16px 0;"><p style="text-align:center;font-size:11px;color:#bbb;"><a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me." style="color:#bbb;">Unsubscribe</a></p>'
        full_html = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#333;max-width:600px;margin:0 auto;">
{body_html}{unsubscribe}</div>"""
        return subject, full_html

    except Exception as e:
        print(f"Email build error: {e}")
        # Fallback very short email
        fallback = f"""Dear {dev_name},<br><br>
I came across {app_name} and noticed your rating of {rating}. For a {genre} app, this can {impact}.<br><br>
We help developers recover their rating with genuine reviews. If you're open to a quick chat, just reply or message me on WhatsApp.<br><br>
Best regards,<br>
Abu Raihan<br>
Play Store Review Service Specialist<br><br>
WhatsApp: +8801902911261<br>
Telegram: https://t.me/abu_raihan69"""
        subject = f"Quick question about {app_name}"
        unsubscribe = f'<br><br><hr style="border:0;border-top:1px solid #eee;margin:16px 0;"><p style="text-align:center;font-size:11px;color:#bbb;"><a href="mailto:{sender_email}?subject=Unsubscribe&body=Remove me." style="color:#bbb;">Unsubscribe</a></p>'
        full_html = f"<div>{fallback}{unsubscribe}</div>"
        return subject, full_html

# ─── SPAM TEST (uses new builder) ────────────────────────────
def run_spam_test(test_email):
    send("🔄 Running Spam Test...")
    try:
        senders = requests.post(SHEET_URL, json={"action":"get_senders"}, timeout=15).json()
        if not senders:
            send("❌ No senders! Add one first.")
            bot.send_message(state["chat_id"],".", reply_markup=kb())
            return
        sender = senders[0]
        fake_row = {
            "app_name":"Demo Budget Tracker",
            "dev_name":"Indie Studio",
            "rating":3.1,
            "genre":"Finance",
            "website":"",
            "description":"A simple app to track daily expenses."
        }
        subject, body = build_clean_email(fake_row, sender['email'])
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

# ─── SCHEDULER (FIXED with debug) ────────────────────────────
def run_scheduler():
    tz = pytz.timezone('Asia/Dhaka')
    # Send a startup message only if chat_id is known
    if state["chat_id"]:
        send("⏰ Scheduler started. Will check every 10 seconds.")
    else:
        print("Scheduler started, but no chat_id yet.")
    while True:
        try:
            if state["status"] == "IDLE" and state["chat_id"]:
                now = datetime.now(tz).strftime("%H:%M")
                times = get_schedule_times()
                if times:
                    # Debug: optionally send current time and times (commented out to avoid spam)
                    # send(f"🕒 Current: {now}, Schedules: {times}")
                    if now in times:
                        send(f"⏰ Scheduled time *{now}* detected – starting full automation...")
                        threading.Thread(target=phase1_scrape, daemon=True).start()
                        # Wait 61 seconds to avoid re-triggering in same minute
                        time.sleep(61)
            time.sleep(10)
        except Exception as e:
            error_msg = f"❌ Scheduler error: {e}"
            if state["chat_id"]:
                send(error_msg)
            else:
                print(error_msg)
            time.sleep(10)

# ─── BOT HANDLERS ────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def welcome(message):
    state["chat_id"] = message.chat.id
    state["status"]  = "IDLE"
    bot.reply_to(message,
        "👋 *Welcome Boss!*\n\n"
        "*🚀 Start Automation:* Runs full workflow – scrape + filter + email – using next pending keyword set.\n"
        "*📅 Schedules:* Set multiple daily run times (automation will start automatically).\n"
        "*🔑 Keywords:* Add keyword sets like `[crypto wallet] [travel app]` – they are used one by one.\n"
        "*📧 Senders:* Manage email sender accounts.\n\n"
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

    # State-based input handling
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

    elif state["status"] == "WAITING_TEST":
        if "@" in text:
            state["status"] = "IDLE"
            bot.reply_to(message, f"Sending test to *{text}*...", parse_mode="Markdown")
            threading.Thread(target=run_spam_test, args=(text,), daemon=True).start()
        else:
            bot.reply_to(message,"❌ Invalid email.", reply_markup=back_kb())
        return

    # Main buttons
    if text == "🚀 Start Automation":
        if state["status"] == "IDLE":
            threading.Thread(target=phase1_scrape, daemon=True).start()

    elif text == "🛑 Pause":
        if state["status"] in ["SCRAPING","FILTERING","EMAILING"]:
            state["status"] = "PAUSED"
            bot.reply_to(message,"🛑 *Paused.* Progress saved.",
                parse_mode="Markdown", reply_markup=kb())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            # Resume to the appropriate phase
            if state["generated_kws"] and state["kw_index"] < len(state["generated_kws"]):
                state["status"] = "SCRAPING"
            else:
                state["status"] = "EMAILING"
            bot.reply_to(message,"▶️ *Resuming...*",
                parse_mode="Markdown", reply_markup=kb())

    elif text == "⏹️ Stop":
        if state["status"] in ["SCRAPING","FILTERING","EMAILING","PAUSED"]:
            # Abort current run, clear local state but keep keyword set pending
            state["status"] = "IDLE"
            state["generated_kws"] = []
            state["kw_index"] = 0
            state["current_set_id"] = None
            bot.reply_to(message,"⏹️ *Stopped.* Current keyword set remains pending.",
                parse_mode="Markdown", reply_markup=kb())

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
            "seen_emails":set()
        })
        bot.reply_to(message,"⏹️ *Fully reset.*", parse_mode="Markdown", reply_markup=kb())

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
