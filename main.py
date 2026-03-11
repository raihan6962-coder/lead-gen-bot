import requests, telebot, time, random, os, threading, re
from datetime import datetime
import pytz
from flask import Flask
from groq import Groq
from google_play_scraper import search, app
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# --- FLASK ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot is Alive!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# --- CONFIG ---
SHEET_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzI5eCCU_Gci6M0jFr5I_Ph48CqUvvP4_nkpngWtjFafVSr_i75yqKX37ZMG4qwG0_V/exec"
BOT_TOKEN = "8709829378:AAEJJQ8jm_oTyAcGenBrIfLi4KYHRVcSJbo"
GROQ_API_KEY = "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"

bot = telebot.TeleBot(BOT_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

state = {
    "status": "IDLE",
    "keywords": [],
    "current_kw_index": 0,
    "total_leads": 0,
    "scraped_apps": set(),
    "existing_emails": set(),
    "chat_id": None,
    "scheduled_time": None,
    "temp_sender_url": None,
    "temp_sender_email": None
}

# --- HELPER: Get best available email from app data ---
def get_best_email(d):
    """
    Priority:
    1. developerEmail
    2. supportEmail (from app details)
    3. Any email found in developer website or description
    Returns: (email, source_label)
    """
    # Priority 1: developerEmail
    dev_email = str(d.get('developerEmail', '') or '').strip().lower()
    if dev_email and '@' in dev_email and '.' in dev_email:
        return dev_email, "dev"

    # Priority 2: supportEmail
    support_email = str(d.get('supportEmail', '') or '').strip().lower()
    if support_email and '@' in support_email and '.' in support_email:
        return support_email, "support"

    # Priority 3: Try to extract email from developer website URL or privacyPolicy
    for field in ['developerWebsite', 'privacyPolicy', 'developerAddress']:
        val = str(d.get(field, '') or '')
        emails_found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', val)
        if emails_found:
            return emails_found[0].lower(), "extracted"

    return '', 'none'

# --- KEYBOARDS ---
def get_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    if state["status"] == "IDLE":
        markup.add(KeyboardButton("🚀 Start Automation"), KeyboardButton("📅 Schedule Automation"))
        markup.add(KeyboardButton("🧪 Spam Test"), KeyboardButton("📧 Manage Senders"))
    elif state["status"] == "RUNNING":
        markup.add(KeyboardButton("🛑 Stop Automation"))
    elif state["status"] == "PAUSED":
        markup.add(KeyboardButton("▶️ Resume"), KeyboardButton("⏹️ Permanent Stop"))
    elif state["status"] == "SCHEDULED":
        markup.add(KeyboardButton("❌ Cancel Schedule"))
    return markup

def get_back_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🔙 Back to Main Menu"))
    return markup

def parse_time(time_str):
    time_str = time_str.strip().upper()
    try:
        return datetime.strptime(time_str, "%I:%M %p").strftime("%H:%M")
    except:
        try:
            return datetime.strptime(time_str, "%H:%M").strftime("%H:%M")
        except:
            return None

def safe_send(chat_id, msg, parse_mode="Markdown"):
    try:
        bot.send_message(chat_id, msg, parse_mode=parse_mode)
    except:
        try:
            bot.send_message(chat_id, msg)
        except:
            pass

# --- EMAIL GENERATOR ---
def generate_email_content(app_name, dev_name, rating, installs, contact_info, email_prompt, sender_email):
    if not dev_name or len(str(dev_name).strip()) < 2 or len(str(dev_name)) > 30:
        dev_name = "Developer"

    contact_html = str(contact_info).replace('\n', '<br>')

    prompt = f"""{email_prompt}

App Details:
- App Name: {app_name}
- Developer: {dev_name}
- Rating: {rating}
- Installs: {installs}

STRICT RULES:
1. Start email body with exactly: "Dear {dev_name},"
2. Plain text only. No markdown bold/italic/headers.
3. Use <br> for line breaks.
4. Keep it short and professional (3-4 paragraphs max).

Format EXACTLY like this (nothing before or after):
SUBJECT: [subject here]
BODY: [body here starting with Dear {dev_name},]"""

    try:
        chat = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=600
        )
        content = chat.choices[0].message.content.strip()

        if "SUBJECT:" in content and "BODY:" in content:
            subject = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
            raw_body = content.split("BODY:")[1].strip()
        else:
            lines = content.split('\n')
            subject = lines[0].replace("Subject:", "").replace("SUBJECT:", "").strip()
            raw_body = '\n'.join(lines[1:]).strip()

        clean_body = raw_body.replace('**', '').replace('*', '')
        clean_body = clean_body.replace('\n\n', '<br><br>').replace('\n', '<br>')

        html_body = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#333;max-width:600px;margin:0 auto;">
{clean_body}<br><br>{contact_html}<br><br>
<hr style="border:0;border-top:1px solid #eee;">
<div style="text-align:center;padding-top:10px;">
<a href="mailto:{sender_email}?subject=Unsubscribe%20Me&body=Please%20remove%20me." style="color:#999;font-size:12px;text-decoration:underline;">Unsubscribe</a>
</div></div>"""

        return subject, html_body

    except Exception as e:
        print(f"Email gen error: {e}")
        fallback = f"""<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#333;max-width:600px;margin:0 auto;">
Dear {dev_name},<br><br>I came across your app "{app_name}" on the Play Store and would love to discuss a collaboration opportunity.<br><br>{contact_html}<br><br>
<hr style="border:0;border-top:1px solid #eee;">
<div style="text-align:center;padding-top:10px;">
<a href="mailto:{sender_email}?subject=Unsubscribe%20Me&body=Please%20remove%20me." style="color:#999;font-size:12px;text-decoration:underline;">Unsubscribe</a>
</div></div>"""
        return f"Collaboration for {app_name}", fallback


# --- CORE ENGINE ---
def engine_thread(max_installs, max_rating, contact_info, email_prompt):
    global state
    gov_kw = ['gov', 'government', 'ministry', 'department', 'council', 'national', 'authority', 'federal', 'municipal']

    stats = {"checked": 0, "no_email": 0, "duplicate": 0, "rating_fail": 0, "install_fail": 0}

    while state["current_kw_index"] < len(state["keywords"]):
        while state["status"] == "PAUSED":
            time.sleep(1)
        if state["status"] == "IDLE" or state["total_leads"] >= 200:
            break

        kw = state["keywords"][state["current_kw_index"]]
        safe_send(state["chat_id"], f"🔍 Searching: *{kw}*")

        try:
            # 5 search variations for maximum app coverage
            raw_results = []
            for query in [kw, f"{kw} app", f"{kw} free", f"best {kw}", f"new {kw}"]:
                try:
                    batch = search(query, lang='en', country='us', n_hits=100)
                    raw_results.extend(batch)
                    time.sleep(0.3)
                except:
                    continue

            # Deduplicate
            results, seen_ids = [], set()
            for r in raw_results:
                if r['appId'] not in seen_ids:
                    seen_ids.add(r['appId'])
                    results.append(r)

            leads_in_kw = 0
            safe_send(state["chat_id"], f"📊 *{len(results)}* unique apps found. Filtering...")

            for r in results:
                while state["status"] == "PAUSED":
                    time.sleep(1)
                if state["status"] == "IDLE" or state["total_leads"] >= 200:
                    break

                app_id = r['appId']
                if app_id in state["scraped_apps"]:
                    continue
                state["scraped_apps"].add(app_id)

                try:
                    d = app(app_id)
                except:
                    continue

                stats["checked"] += 1

                # --- SAFE PARSING ---
                raw_score = d.get('score')
                rating = float(raw_score) if raw_score is not None else 0.0

                raw_installs = d.get('minInstalls') or d.get('realInstalls', 0)
                installs = int(raw_installs) if raw_installs is not None else 0

                dev_lower = str(d.get('developer', '') or '').lower()

                # Filter: Government apps skip
                if any(g in dev_lower for g in gov_kw):
                    continue

                # Filter: Rating check (want LOW rating apps, so rating must be <= max_rating)
                # Also allow apps with 0 rating (brand new apps — great leads!)
                if rating > max_rating:
                    stats["rating_fail"] += 1
                    continue

                # Filter: Install check
                if installs > max_installs:
                    stats["install_fail"] += 1
                    continue

                # --- GET BEST EMAIL (dev email first, then support email) ---
                email, email_source = get_best_email(d)

                if not email:
                    stats["no_email"] += 1
                    continue

                if email in state["existing_emails"]:
                    stats["duplicate"] += 1
                    continue

                # ✅ ALL FILTERS PASSED

                # Check sender availability
                try:
                    senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=15).json()
                    available = [s for s in senders if int(s.get('sent', 0)) < int(s.get('limit', 1))]
                except:
                    continue

                if not available:
                    safe_send(state["chat_id"], "⚠️ All senders hit daily limit! Pausing.")
                    state["status"] = "PAUSED"
                    break

                sender = available[0]
                app_title = str(d.get('title', 'Unknown'))
                dev_name = str(d.get('developer', 'Developer'))

                email_tag = "📧 Dev email" if email_source == "dev" else ("📩 Support email" if email_source == "support" else "📬 Extracted email")

                safe_send(state["chat_id"],
                    f"✨ *Lead Found!*\n"
                    f"App: {app_title}\n"
                    f"Rating: `{rating}` | Installs: `{installs:,}`\n"
                    f"{email_tag}: `{email}`\n"
                    f"Generating email...")

                subject, body = generate_email_content(
                    app_title, dev_name, rating, installs,
                    contact_info, email_prompt, sender['email']
                )

                # Save to sheet
                try:
                    requests.post(SHEET_WEB_APP_URL, json={
                        "action": "save_lead",
                        "app_name": app_title,
                        "dev_name": dev_name,
                        "email": email,
                        "email_source": email_source,
                        "subject": subject,
                        "body": body,
                        "installs": installs,
                        "rating": rating,
                        "link": d.get('url', ''),
                        "category": d.get('genre', ''),
                        "website": d.get('developerWebsite', ''),
                        "updated": str(d.get('updated', ''))
                    }, timeout=15)
                except:
                    pass

                state["existing_emails"].add(email)

                # Send email
                try:
                    mail_res = requests.post(
                        sender['url'],
                        json={"action": "send_email", "to": email, "subject": subject, "body": body},
                        timeout=30
                    )
                    mail_text = mail_res.text.strip()
                except Exception as me:
                    mail_text = f"Error: {me}"

                if mail_text == "Success":
                    try:
                        requests.post(SHEET_WEB_APP_URL, json={"action": "increment_sender", "email": sender['email']}, timeout=15)
                    except:
                        pass

                    state["total_leads"] += 1
                    leads_in_kw += 1

                    safe_send(state["chat_id"],
                        f"✅ *Lead #{state['total_leads']} Sent!*\n"
                        f"To: `{email}` ({email_source})\n"
                        f"Via: {sender['email']}")

                    delay = random.randint(60, 120)
                    safe_send(state["chat_id"], f"⏳ Waiting {delay}s before next...")
                    for _ in range(delay):
                        if state["status"] != "RUNNING":
                            break
                        time.sleep(1)
                else:
                    safe_send(state["chat_id"], f"❌ Send failed to `{email}`: {mail_text}")

            # Keyword summary
            safe_send(state["chat_id"],
                f"📌 *Summary for* `{kw}`:\n"
                f"Leads: {leads_in_kw} | Checked: {stats['checked']}\n"
                f"No email: {stats['no_email']} | Duplicate: {stats['duplicate']}\n"
                f"Rating fail: {stats['rating_fail']} | Install fail: {stats['install_fail']}")

        except Exception as e:
            print(f"Error kw '{kw}': {e}")
            safe_send(state["chat_id"], f"⚠️ Error on `{kw}`: {str(e)[:100]}")

        if state["status"] == "RUNNING":
            state["current_kw_index"] += 1

    if state["status"] == "RUNNING":
        safe_send(state["chat_id"],
            f"🎉 *Automation Done!*\n"
            f"Total leads: *{state['total_leads']}*\n"
            f"Apps checked: {stats['checked']}\n"
            f"No email: {stats['no_email']} | Duplicate: {stats['duplicate']}\n"
            f"Rating fail: {stats['rating_fail']} | Install fail: {stats['install_fail']}")
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], "✅ Done!", reply_markup=get_keyboard())


# --- START ENGINE ---
def start_engine():
    global state
    try:
        safe_send(state["chat_id"], "🔄 Fetching settings...")

        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}, timeout=20).json()

        # Parse with safe fallbacks
        try:
            max_installs = int(str(res.get('max_installs', '100000')).replace(',', '').strip())
        except:
            max_installs = 100000

        try:
            max_rating = float(str(res.get('max_rating', '4.5')).strip())
        except:
            max_rating = 4.5

        contact_info = str(res.get('contact_info', ''))
        email_prompt = str(res.get('email_prompt', 'Write a professional collaboration email.'))
        niche = str(res.get('niche', 'mobile apps'))
        keyword_prompt = str(res.get('keyword_prompt', 'Generate Play Store search terms for'))

        # Load existing emails
        try:
            db_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_existing_emails"}, timeout=20).json()
            state["existing_emails"] = set(db_res) if isinstance(db_res, list) else set()
        except:
            state["existing_emails"] = set()

        safe_send(state["chat_id"],
            f"✅ *Settings loaded:*\n"
            f"Target: Rating ≤ `{max_rating}` | Installs ≤ `{max_installs:,}`\n"
            f"DB emails loaded: `{len(state['existing_emails'])}`")

        # Generate keywords if needed
        if not state["keywords"]:
            safe_send(state["chat_id"], "🧠 AI generating keywords...")

            kw_prompt = f"""{keyword_prompt}
Niche: {niche}

Give me 200 unique short search terms (2-4 words) that someone types in Google Play Store to find apps in this niche.
Rules:
- Comma separated only
- NO word 'keywords' anywhere
- NO numbers or bullet points
- NO explanation, just the list"""

            chat = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": kw_prompt}],
                model="llama-3.1-8b-instant",
                max_tokens=2000
            )

            raw = chat.choices[0].message.content.replace('\n', ',').replace('\r', ',')
            cleaned = []
            for k in raw.split(','):
                k = re.sub(r'^\d+[\.\)\-\s]+', '', k)
                k = k.replace('keyword', '').replace('**', '').replace('*', '').replace('#', '')
                k = k.replace('"', '').replace("'", '').strip()
                if 2 < len(k) < 60:
                    cleaned.append(k)

            if not cleaned:
                safe_send(state["chat_id"], "❌ Keyword generation failed. Try again.")
                state["status"] = "IDLE"
                bot.send_message(state["chat_id"], ".", reply_markup=get_keyboard())
                return

            state["keywords"] = cleaned
            state["current_kw_index"] = 0
            state["total_leads"] = 0
            state["scraped_apps"] = set()
            safe_send(state["chat_id"], f"✅ *{len(cleaned)} keywords* ready! Starting search...")

        threading.Thread(
            target=engine_thread,
            args=(max_installs, max_rating, contact_info, email_prompt),
            daemon=True
        ).start()

    except Exception as e:
        state["status"] = "IDLE"
        safe_send(state["chat_id"], f"❌ Error: {e}")
        bot.send_message(state["chat_id"], ".", reply_markup=get_keyboard())


# --- SPAM TEST ---
def run_spam_test(test_email):
    safe_send(state["chat_id"], "🔄 Running Spam Test...")
    try:
        senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=15).json()
        if not senders:
            safe_send(state["chat_id"], "❌ No senders! Add one first.")
            bot.send_message(state["chat_id"], ".", reply_markup=get_keyboard())
            return

        sender = senders[0]
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}, timeout=15).json()

        try:
            lead_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_one_lead"}, timeout=15).json()
        except:
            lead_res = {"found": False}

        if lead_res.get("found"):
            app_name = lead_res.get("app_name", "Demo App")
            dev_name = lead_res.get("dev_name", "Developer")
            rating = lead_res.get("rating", 3.2)
            installs = lead_res.get("installs", 5000)
        else:
            app_name, dev_name, rating, installs = "Demo App", "Test Studio", 3.2, 5000

        subject, body = generate_email_content(
            app_name, dev_name, rating, installs,
            str(res.get('contact_info', '')),
            str(res.get('email_prompt', 'Write a professional collaboration email.')),
            sender['email']
        )

        mail_res = requests.post(
            sender['url'],
            json={"action": "send_email", "to": test_email, "subject": subject, "body": body},
            timeout=30
        )

        if mail_res.text.strip() == "Success":
            safe_send(state["chat_id"], f"✅ Test sent to `{test_email}` via {sender['email']}")
        else:
            safe_send(state["chat_id"], f"❌ Failed: {mail_res.text}")

        bot.send_message(state["chat_id"], ".", reply_markup=get_keyboard())

    except Exception as e:
        safe_send(state["chat_id"], f"❌ Error: {e}")
        bot.send_message(state["chat_id"], ".", reply_markup=get_keyboard())


# --- SCHEDULER ---
def run_scheduler():
    tz = pytz.timezone('Asia/Dhaka')
    while True:
        try:
            if state["status"] == "SCHEDULED" and state["scheduled_time"] and state["chat_id"]:
                if datetime.now(tz).strftime("%H:%M") == state["scheduled_time"]:
                    state["status"] = "RUNNING"
                    safe_send(state["chat_id"], "⏰ Scheduled time! Starting...")
                    bot.send_message(state["chat_id"], ".", reply_markup=get_keyboard())
                    start_engine()
                    time.sleep(61)
        except Exception as e:
            print(f"Scheduler: {e}")
        time.sleep(10)


# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    state["chat_id"] = message.chat.id
    state["status"] = "IDLE"
    bot.reply_to(message, "👋 *Welcome Boss!*", parse_mode="Markdown", reply_markup=get_keyboard())


@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if call.data == "back_to_main":
        state["status"] = "IDLE"
        bot.send_message(call.message.chat.id, "🔙 Main Menu.", reply_markup=get_keyboard())

    elif call.data == "add_new_sender":
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
        bot.send_message(call.message.chat.id,
            f"📝 Deploy this in Apps Script, then send me the URL:\n\n`{code}`",
            parse_mode="Markdown", reply_markup=get_back_keyboard())
        state["status"] = "WAITING_SENDER_URL"

    elif call.data.startswith("del_"):
        email_to_del = call.data.split("del_")[1]
        mk = InlineKeyboardMarkup()
        mk.add(
            InlineKeyboardButton("✅ Delete", callback_data=f"confirm_del_{email_to_del}"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_del")
        )
        bot.send_message(call.message.chat.id, f"Delete *{email_to_del}*?", parse_mode="Markdown", reply_markup=mk)

    elif call.data.startswith("confirm_del_"):
        email_to_del = call.data.split("confirm_del_")[1]
        requests.post(SHEET_WEB_APP_URL, json={"action": "delete_sender", "email": email_to_del}, timeout=15)
        bot.send_message(call.message.chat.id, f"🗑️ Deleted *{email_to_del}*", parse_mode="Markdown")

    elif call.data == "cancel_del":
        bot.send_message(call.message.chat.id, "Cancelled.")


@bot.message_handler(func=lambda msg: True)
def handle_messages(message):
    text = message.text.strip()
    state["chat_id"] = message.chat.id

    if text == "🔙 Back to Main Menu":
        state["status"] = "IDLE"
        state["temp_sender_url"] = None
        state["temp_sender_email"] = None
        bot.reply_to(message, "🔙 Main Menu.", reply_markup=get_keyboard())
        return

    if state["status"] == "WAITING_SENDER_URL":
        if "script.google.com" in text:
            state["temp_sender_url"] = text
            state["status"] = "WAITING_SENDER_EMAIL"
            bot.reply_to(message, "✅ URL saved! Now send the *email address*.", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid URL.", reply_markup=get_back_keyboard())
        return

    elif state["status"] == "WAITING_SENDER_EMAIL":
        if "@" in text:
            state["temp_sender_email"] = text
            state["status"] = "WAITING_SENDER_LIMIT"
            bot.reply_to(message, "✅ Email saved! Now send *daily limit* (e.g. 20).", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid email.", reply_markup=get_back_keyboard())
        return

    elif state["status"] == "WAITING_SENDER_LIMIT":
        if text.isdigit():
            requests.post(SHEET_WEB_APP_URL, json={
                "action": "add_sender",
                "email": state["temp_sender_email"],
                "url": state["temp_sender_url"],
                "limit": int(text)
            }, timeout=15)
            bot.reply_to(message, f"🎉 Sender *{state['temp_sender_email']}* added! Limit: {text}/day", parse_mode="Markdown", reply_markup=get_keyboard())
            state["status"] = "IDLE"
            state["temp_sender_url"] = None
            state["temp_sender_email"] = None
        else:
            bot.reply_to(message, "❌ Send a number.", reply_markup=get_back_keyboard())
        return

    elif state["status"] == "WAITING_TIME":
        parsed = parse_time(text)
        if parsed:
            state["scheduled_time"] = parsed
            state["status"] = "SCHEDULED"
            bot.reply_to(message, f"✅ Scheduled at *{parsed}* daily (Dhaka time)!", parse_mode="Markdown", reply_markup=get_keyboard())
        else:
            bot.reply_to(message, "❌ Format: 02:30 PM or 14:30", reply_markup=get_back_keyboard())
        return

    elif state["status"] == "WAITING_TEST_EMAIL":
        if "@" in text:
            state["status"] = "IDLE"
            bot.reply_to(message, f"Sending test to *{text}*...", parse_mode="Markdown")
            threading.Thread(target=run_spam_test, args=(text,), daemon=True).start()
        else:
            bot.reply_to(message, "❌ Invalid email.", reply_markup=get_back_keyboard())
        return

    # Main menu
    if text == "📧 Manage Senders":
        try:
            senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=15).json()
        except:
            bot.reply_to(message, "❌ Cannot connect to Sheet.", reply_markup=get_keyboard())
            return
        mk = InlineKeyboardMarkup()
        msg = "📋 *Senders:*\n\n"
        if not senders:
            msg += "_None yet._\n"
        else:
            for i, s in enumerate(senders):
                msg += f"{i+1}. `{s.get('email')}` — {s.get('sent',0)}/{s.get('limit',0)}\n"
                mk.add(InlineKeyboardButton(f"🗑️ {s.get('email')}", callback_data=f"del_{s.get('email')}"))
        mk.add(InlineKeyboardButton("➕ Add Sender", callback_data="add_new_sender"))
        mk.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
        bot.reply_to(message, msg, parse_mode="Markdown", reply_markup=mk)

    elif text == "🚀 Start Automation":
        if state["status"] in ["IDLE", "SCHEDULED"]:
            state["status"] = "RUNNING"
            bot.reply_to(message, "🚀 *Starting...*", parse_mode="Markdown", reply_markup=get_keyboard())
            threading.Thread(target=start_engine, daemon=True).start()

    elif text == "🛑 Stop Automation":
        if state["status"] == "RUNNING":
            state["status"] = "PAUSED"
            bot.reply_to(message, "🛑 *Paused.*", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            state["status"] = "RUNNING"
            bot.reply_to(message, "▶️ *Resuming...*", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "⏹️ Permanent Stop":
        state["status"] = "IDLE"
        state["keywords"] = []
        state["current_kw_index"] = 0
        state["total_leads"] = 0
        state["scraped_apps"] = set()
        bot.reply_to(message, "⏹️ *Fully reset.*", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "📅 Schedule Automation":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TIME"
            bot.reply_to(message, "⏰ Send time (e.g. *02:30 PM* or *14:30*)", parse_mode="Markdown", reply_markup=get_back_keyboard())

    elif text == "❌ Cancel Schedule":
        state["status"] = "IDLE"
        state["scheduled_time"] = None
        bot.reply_to(message, "❌ Cancelled.", reply_markup=get_keyboard())

    elif text == "🧪 Spam Test":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TEST_EMAIL"
            bot.reply_to(message, "📧 Send test email address.", reply_markup=get_back_keyboard())


# --- MAIN ---
if __name__ == "__main__":
    print("🚀 Bot starting...")
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    while True:
        try:
            print("🤖 Polling...")
            bot.polling(none_stop=True, interval=0, timeout=30)
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)
