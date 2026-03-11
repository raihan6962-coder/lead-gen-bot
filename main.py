import requests, telebot, time, random, os, threading, re
from datetime import datetime
import pytz
from flask import Flask
from groq import Groq
from google_play_scraper import search, app
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# --- FLASK WEB SERVER ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot is Alive and Running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# --- CONFIG ---
SHEET_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzI5eCCU_Gci6M0jFr5I_Ph48CqUvvP4_nkpngWtjFafVSr_i75yqKX37ZMG4qwG0_V/exec"
BOT_TOKEN = "8709829378:AAEJJQ8jm_oTyAcGenBrIfLi4KYHRVcSJbo"
GROQ_API_KEY = "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"

bot = telebot.TeleBot(BOT_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# --- STATE MANAGEMENT ---
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

# --- AI EMAIL GENERATOR ---
def generate_email_content(app_name, dev_name, rating, installs, description, contact_info, email_prompt, sender_email):
    if not dev_name or len(str(dev_name).strip()) < 2:
        dev_name = "Developer"
    if len(str(dev_name)) > 30:
        dev_name = "Developer"

    contact_html = str(contact_info).replace('\n', '<br>')

    prompt = f"""{email_prompt}

App Details:
- App Name: {app_name}
- Developer: {dev_name}
- Rating: {rating}
- Installs: {installs}

STRICT RULES:
1. You MUST start the email body with exactly: "Dear {dev_name},"
2. Write in plain text with normal paragraphs only.
3. DO NOT use markdown like **bold** or *italics* or # headers.
4. Use <br> tag for line breaks.

Format your response EXACTLY like this (no extra text before or after):
SUBJECT: [Your subject line here]
BODY: [Your email body here starting with Dear {dev_name},]"""

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

        final_html_body = f"""<div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
{clean_body}<br><br>{contact_html}<br><br>
<hr style="border: 0; border-top: 1px solid #eee;">
<div style="text-align: center; padding-top: 10px;">
<a href="mailto:{sender_email}?subject=Unsubscribe%20Me&body=Please%20remove%20me%20from%20your%20mailing%20list." style="color: #999; font-size: 12px; text-decoration: underline;">Unsubscribe from future emails</a>
</div>
</div>"""

        return subject, final_html_body

    except Exception as e:
        print(f"Email generation error: {e}")
        fallback_body = f"""<div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
Dear {dev_name},<br><br>
I came across your app "{app_name}" on the Play Store and was impressed by your work.<br><br>
I would love to explore a potential collaboration opportunity with you.<br><br>
{contact_html}<br><br>
<hr style="border: 0; border-top: 1px solid #eee;">
<div style="text-align: center; padding-top: 10px;">
<a href="mailto:{sender_email}?subject=Unsubscribe%20Me&body=Please%20remove%20me%20from%20your%20mailing%20list." style="color: #999; font-size: 12px; text-decoration: underline;">Unsubscribe from future emails</a>
</div>
</div>"""
        return f"Collaboration Opportunity for {app_name}", fallback_body


# --- CORE ENGINE THREAD ---
def engine_thread(max_installs, max_rating, contact_info, email_prompt):
    global state
    gov_keywords = ['gov', 'government', 'ministry', 'department', 'council', 'national', 'authority', 'federal', 'municipal']

    while state["current_kw_index"] < len(state["keywords"]):
        while state["status"] == "PAUSED":
            time.sleep(1)

        if state["status"] == "IDLE":
            break
        if state["total_leads"] >= 200:
            break

        kw = state["keywords"][state["current_kw_index"]]
        bot.send_message(state["chat_id"], f"🔍 Searching: *{kw}*", parse_mode="Markdown")

        try:
            # MULTI-QUERY EXPANSION: 5 variations = 400-500 apps per keyword
            raw_results = []
            search_queries = [
                kw,
                f"{kw} app",
                f"{kw} free",
                f"best {kw}",
                f"new {kw}"
            ]

            for query in search_queries:
                try:
                    results_batch = search(query, lang='en', country='us', n_hits=100)
                    raw_results.extend(results_batch)
                    time.sleep(0.5)
                except Exception as se:
                    print(f"Search error '{query}': {se}")
                    continue

            # Deduplicate
            results = []
            seen_ids = set()
            for r in raw_results:
                if r['appId'] not in seen_ids:
                    seen_ids.add(r['appId'])
                    results.append(r)

            leads_in_this_kw = 0
            bot.send_message(state["chat_id"], f"📊 Found {len(results)} unique apps. Filtering...", parse_mode="Markdown")

            for r in results:
                while state["status"] == "PAUSED":
                    time.sleep(1)
                if state["status"] == "IDLE":
                    break
                if state["total_leads"] >= 200:
                    break

                # Check sender availability
                try:
                    senders_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=15)
                    senders = senders_res.json()
                    available_senders = [s for s in senders if int(s.get('sent', 0)) < int(s.get('limit', 0))]
                except Exception as se:
                    print(f"Sender check error: {se}")
                    continue

                if not available_senders:
                    bot.send_message(state["chat_id"], "⚠️ All senders reached daily limit! Pausing automation.", reply_markup=get_keyboard())
                    state["status"] = "PAUSED"
                    break

                current_sender = available_senders[0]

                app_id = r['appId']
                if app_id in state["scraped_apps"]:
                    continue
                state["scraped_apps"].add(app_id)

                try:
                    d = app(app_id)
                except Exception as ae:
                    print(f"App fetch error {app_id}: {ae}")
                    continue

                # SAFE PARSING — No None crashes
                raw_score = d.get('score')
                rating = float(raw_score) if raw_score is not None else 0.0

                raw_installs = d.get('minInstalls')
                installs = int(raw_installs) if raw_installs is not None else 0

                raw_email = d.get('developerEmail', '')
                email = str(raw_email).strip().lower() if raw_email else ''

                # Fast filters
                if not email:
                    continue
                if email in state["existing_emails"]:
                    continue

                raw_dev = d.get('developer', '')
                dev_name_lower = str(raw_dev).lower()
                if any(g in dev_name_lower for g in gov_keywords):
                    continue

                # MAIN FILTER: Only sheet limits, no extra conditions
                # rating <= max_rating AND installs <= max_installs
                if rating <= max_rating and installs <= max_installs:

                    dev_name_display = str(d.get('developer', 'Developer'))
                    app_title = str(d.get('title', 'Unknown App'))

                    bot.send_message(
                        state["chat_id"],
                        f"✨ *Lead Found!*\nApp: {app_title}\nRating: {rating} | Installs: {installs:,}\n📧 Generating email...",
                        parse_mode="Markdown"
                    )

                    subject, body = generate_email_content(
                        app_title, dev_name_display, rating, installs,
                        str(d.get('description', '')), contact_info, email_prompt, current_sender['email']
                    )

                    # Save to sheet
                    try:
                        requests.post(SHEET_WEB_APP_URL, json={
                            "action": "save_lead",
                            "app_name": app_title,
                            "dev_name": dev_name_display,
                            "email": email,
                            "subject": subject,
                            "body": body,
                            "installs": installs,
                            "rating": rating,
                            "link": d.get('url', ''),
                            "category": d.get('genre', ''),
                            "website": d.get('developerWebsite', ''),
                            "updated": str(d.get('updated', ''))
                        }, timeout=15)
                    except Exception as save_err:
                        print(f"Save lead error: {save_err}")

                    state["existing_emails"].add(email)

                    # Send email
                    try:
                        mail_res = requests.post(
                            current_sender['url'],
                            json={"action": "send_email", "to": email, "subject": subject, "body": body},
                            timeout=30
                        )
                        mail_text = mail_res.text.strip()
                    except Exception as mail_err:
                        print(f"Mail send error: {mail_err}")
                        mail_text = "Error"

                    if mail_text == "Success":
                        try:
                            requests.post(SHEET_WEB_APP_URL, json={
                                "action": "increment_sender",
                                "email": current_sender['email']
                            }, timeout=15)
                        except:
                            pass

                        state["total_leads"] += 1
                        leads_in_this_kw += 1

                        bot.send_message(
                            state["chat_id"],
                            f"✅ *Lead #{state['total_leads']} Sent!*\nTo: `{email}`\nVia: {current_sender['email']}",
                            parse_mode="Markdown"
                        )

                        # Delay between emails
                        delay = random.randint(60, 120)
                        bot.send_message(state["chat_id"], f"⏳ Waiting {delay}s before next lead...")
                        for _ in range(delay):
                            if state["status"] != "RUNNING":
                                break
                            time.sleep(1)
                    else:
                        bot.send_message(state["chat_id"], f"❌ Email failed: `{email}` | {mail_text}", parse_mode="Markdown")

            if leads_in_this_kw == 0:
                bot.send_message(state["chat_id"], f"⚠️ No leads from *{kw}*. Moving to next...", parse_mode="Markdown")
            else:
                bot.send_message(state["chat_id"], f"📌 *{leads_in_this_kw} leads* from *{kw}*.", parse_mode="Markdown")

        except Exception as e:
            print(f"Engine error on keyword '{kw}': {e}")
            bot.send_message(state["chat_id"], f"⚠️ Error on *{kw}*: {str(e)[:100]}", parse_mode="Markdown")

        if state["status"] == "RUNNING":
            state["current_kw_index"] += 1

    if state["status"] == "RUNNING":
        bot.send_message(
            state["chat_id"],
            f"🎉 *Automation Finished!*\nTotal emails sent: *{state['total_leads']}*",
            parse_mode="Markdown",
            reply_markup=get_keyboard()
        )
        state["status"] = "IDLE"


# --- START ENGINE ---
def start_engine():
    global state
    try:
        bot.send_message(state["chat_id"], "🔄 Fetching settings and database...")

        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}, timeout=20).json()

        max_installs = int(str(res.get('max_installs', '100000')).replace(',', '').strip())
        max_rating = float(str(res.get('max_rating', '4.5')).strip())
        contact_info = str(res.get('contact_info', ''))
        email_prompt = str(res.get('email_prompt', 'Write a professional collaboration email.'))
        niche = str(res.get('niche', 'mobile apps'))
        keyword_prompt = str(res.get('keyword_prompt', 'Generate search terms for'))

        try:
            db_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_existing_emails"}, timeout=20).json()
            state["existing_emails"] = set(db_res) if isinstance(db_res, list) else set()
        except:
            state["existing_emails"] = set()

        bot.send_message(state["chat_id"], f"✅ Settings loaded!\nMax Installs: {max_installs:,}\nMax Rating: {max_rating}")

        if not state["keywords"]:
            bot.send_message(state["chat_id"], "🧠 AI is generating keywords...")

            kw_prompt = f"""{keyword_prompt}
Niche: {niche}

Give me exactly 200 unique, short, simple search terms (2-4 words each) that someone would type into Google Play Store to find apps in this niche.
Separate each term with a comma.
DO NOT use the word 'keywords' anywhere.
DO NOT use numbers, bullet points, or dashes.
DO NOT add any explanation or extra text.
Just give the comma-separated list of search terms only."""

            chat = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": kw_prompt}],
                model="llama-3.1-8b-instant",
                max_tokens=2000
            )

            raw_text = chat.choices[0].message.content

            # SUPER CLEANER — handles all AI output formats
            raw_text = raw_text.replace('\n', ',').replace('\r', ',')
            raw_kws = raw_text.split(',')

            cleaned_kws = []
            for k in raw_kws:
                k = re.sub(r'^\d+[\.\)\-\s]+', '', k)   # Remove "1." "2)" "3-"
                k = k.replace('keyword', '').replace('Keyword', '')
                k = k.replace('**', '').replace('*', '').replace('#', '')
                k = k.replace('"', '').replace("'", '').strip()
                if 2 < len(k) < 60:
                    cleaned_kws.append(k)

            if not cleaned_kws:
                bot.send_message(state["chat_id"], "❌ AI failed to generate keywords. Please try again.", reply_markup=get_keyboard())
                state["status"] = "IDLE"
                return

            state["keywords"] = cleaned_kws
            state["current_kw_index"] = 0
            state["total_leads"] = 0
            state["scraped_apps"] = set()

            bot.send_message(state["chat_id"], f"✅ Generated {len(state['keywords'])} keywords! Starting search now...")

        threading.Thread(
            target=engine_thread,
            args=(max_installs, max_rating, contact_info, email_prompt),
            daemon=True
        ).start()

    except Exception as e:
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], f"❌ System Error: {str(e)}", reply_markup=get_keyboard())


# --- SPAM TEST ---
def run_spam_test(test_email):
    bot.send_message(state["chat_id"], "🔄 Running Spam Test...")
    try:
        senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=15).json()
        if not senders:
            bot.send_message(state["chat_id"], "❌ No senders added! Please add a sender first.", reply_markup=get_keyboard())
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
            rating = lead_res.get("rating", 4.2)
            installs = lead_res.get("installs", 50000)
        else:
            app_name = "Demo Finance App"
            dev_name = "Demo Studio"
            rating = 4.2
            installs = 50000

        contact_info = str(res.get('contact_info', ''))
        email_prompt = str(res.get('email_prompt', 'Write a professional collaboration email.'))

        subject, body = generate_email_content(
            app_name, dev_name, rating, installs,
            "A great app for users.", contact_info, email_prompt, sender['email']
        )

        mail_res = requests.post(
            sender['url'],
            json={"action": "send_email", "to": test_email, "subject": subject, "body": body},
            timeout=30
        )

        if mail_res.text.strip() == "Success":
            bot.send_message(
                state["chat_id"],
                f"✅ *Spam Test Successful!*\nSent to: `{test_email}`\nVia: {sender['email']}",
                parse_mode="Markdown",
                reply_markup=get_keyboard()
            )
        else:
            bot.send_message(state["chat_id"], f"❌ Failed: {mail_res.text}", reply_markup=get_keyboard())

    except Exception as e:
        bot.send_message(state["chat_id"], f"❌ Spam test error: {str(e)}", reply_markup=get_keyboard())


# --- SCHEDULER THREAD ---
def run_scheduler():
    global state
    tz = pytz.timezone('Asia/Dhaka')
    while True:
        try:
            if state["status"] == "SCHEDULED" and state["scheduled_time"] and state["chat_id"]:
                now = datetime.now(tz).strftime("%H:%M")
                if now == state["scheduled_time"]:
                    state["status"] = "RUNNING"
                    bot.send_message(state["chat_id"], "⏰ Scheduled time reached! Starting automation...", reply_markup=get_keyboard())
                    start_engine()
                    time.sleep(61)
        except Exception as e:
            print(f"Scheduler error: {e}")
        time.sleep(10)


# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    global state
    state["chat_id"] = message.chat.id
    state["status"] = "IDLE"
    bot.reply_to(message, "👋 *Welcome Boss!* What would you like to do?", parse_mode="Markdown", reply_markup=get_keyboard())


@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    global state

    if call.data == "back_to_main":
        state["status"] = "IDLE"
        bot.send_message(call.message.chat.id, "🔙 Returned to Main Menu.", reply_markup=get_keyboard())
        return

    if call.data == "add_new_sender":
        script_code = """function doPost(e) {
  var data = JSON.parse(e.postData.contents);
  if (data.action == "send_email") {
    try {
      GmailApp.sendEmail(data.to, data.subject, "", {htmlBody: data.body});
      return ContentService.createTextOutput("Success");
    } catch (error) {
      return ContentService.createTextOutput("Error: " + error.toString());
    }
  }
}"""
        bot.send_message(
            call.message.chat.id,
            f"📝 *Step 1:* Deploy this code in your new Email's Apps Script:\n\n`{script_code}`\n\n*Step 2:* Send me the Web App URL.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
        state["status"] = "WAITING_SENDER_URL"

    elif call.data.startswith("del_"):
        email_to_del = call.data.split("del_")[1]
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_del_{email_to_del}"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_del")
        )
        bot.send_message(call.message.chat.id, f"⚠️ Delete *{email_to_del}*?", parse_mode="Markdown", reply_markup=markup)

    elif call.data.startswith("confirm_del_"):
        email_to_del = call.data.split("confirm_del_")[1]
        requests.post(SHEET_WEB_APP_URL, json={"action": "delete_sender", "email": email_to_del}, timeout=15)
        bot.send_message(call.message.chat.id, f"🗑️ Deleted *{email_to_del}* successfully!", parse_mode="Markdown")

    elif call.data == "cancel_del":
        bot.send_message(call.message.chat.id, "❌ Deletion cancelled.")


@bot.message_handler(func=lambda msg: True)
def handle_messages(message):
    global state
    text = message.text.strip()
    state["chat_id"] = message.chat.id

    if text == "🔙 Back to Main Menu":
        state["status"] = "IDLE"
        state["temp_sender_url"] = None
        state["temp_sender_email"] = None
        bot.reply_to(message, "🔙 Returned to Main Menu.", reply_markup=get_keyboard())
        return

    if state["status"] == "WAITING_SENDER_URL":
        if "script.google.com" in text:
            state["temp_sender_url"] = text
            state["status"] = "WAITING_SENDER_EMAIL"
            bot.reply_to(message, "✅ URL saved! Now send the *Email Address* for this sender.", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid URL. Send a valid Google Apps Script Web App URL.", reply_markup=get_back_keyboard())
        return

    elif state["status"] == "WAITING_SENDER_EMAIL":
        if "@" in text and "." in text:
            state["temp_sender_email"] = text
            state["status"] = "WAITING_SENDER_LIMIT"
            bot.reply_to(message, "✅ Email saved! Now send the *Daily Sending Limit* (e.g., 20).", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid email address. Try again.", reply_markup=get_back_keyboard())
        return

    elif state["status"] == "WAITING_SENDER_LIMIT":
        if text.isdigit():
            requests.post(SHEET_WEB_APP_URL, json={
                "action": "add_sender",
                "email": state["temp_sender_email"],
                "url": state["temp_sender_url"],
                "limit": int(text)
            }, timeout=15)
            bot.reply_to(message, f"🎉 Sender *{state['temp_sender_email']}* added with limit {text}/day!", parse_mode="Markdown", reply_markup=get_keyboard())
            state["status"] = "IDLE"
            state["temp_sender_url"] = None
            state["temp_sender_email"] = None
        else:
            bot.reply_to(message, "❌ Please send a valid number (e.g., 20).", reply_markup=get_back_keyboard())
        return

    elif state["status"] == "WAITING_TIME":
        parsed = parse_time(text)
        if parsed:
            state["scheduled_time"] = parsed
            state["status"] = "SCHEDULED"
            bot.reply_to(message, f"✅ Automation scheduled at *{parsed}* (Dhaka time) daily!", parse_mode="Markdown", reply_markup=get_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid time. Use format: 02:30 PM or 14:30", reply_markup=get_back_keyboard())
        return

    elif state["status"] == "WAITING_TEST_EMAIL":
        if "@" in text and "." in text:
            state["status"] = "IDLE"
            bot.reply_to(message, f"🧪 Sending test email to *{text}*...", parse_mode="Markdown")
            threading.Thread(target=run_spam_test, args=(text,), daemon=True).start()
        else:
            bot.reply_to(message, "❌ Invalid email. Try again.", reply_markup=get_back_keyboard())
        return

    # Main menu buttons
    if text == "📧 Manage Senders":
        try:
            senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=15).json()
        except:
            bot.reply_to(message, "❌ Cannot connect to Sheet. Check your Sheet URL.", reply_markup=get_keyboard())
            return

        markup = InlineKeyboardMarkup()
        msg_text = "📋 *Your Senders:*\n\n"

        if not senders:
            msg_text += "_No senders added yet._\n"
        else:
            for i, s in enumerate(senders):
                sent = s.get('sent', 0)
                limit = s.get('limit', 0)
                email_s = s.get('email', 'unknown')
                msg_text += f"{i+1}. `{email_s}` — {sent}/{limit} sent\n"
                markup.add(InlineKeyboardButton(f"🗑️ Delete {email_s}", callback_data=f"del_{email_s}"))

        markup.add(InlineKeyboardButton("➕ Add New Sender", callback_data="add_new_sender"))
        markup.add(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main"))
        bot.reply_to(message, msg_text, parse_mode="Markdown", reply_markup=markup)

    elif text == "🚀 Start Automation":
        if state["status"] in ["IDLE", "SCHEDULED"]:
            state["status"] = "RUNNING"
            bot.reply_to(message, "🚀 *Starting Automation...*", parse_mode="Markdown", reply_markup=get_keyboard())
            threading.Thread(target=start_engine, daemon=True).start()

    elif text == "🛑 Stop Automation":
        if state["status"] == "RUNNING":
            state["status"] = "PAUSED"
            bot.reply_to(message, "🛑 *Automation Paused.*", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            state["status"] = "RUNNING"
            bot.reply_to(message, "▶️ *Resuming Automation...*", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "⏹️ Permanent Stop":
        state["status"] = "IDLE"
        state["keywords"] = []
        state["current_kw_index"] = 0
        state["total_leads"] = 0
        state["scraped_apps"] = set()
        bot.reply_to(message, "⏹️ *Automation fully reset.* All progress cleared.", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "📅 Schedule Automation":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TIME"
            bot.reply_to(message, "⏰ Send the time for daily automation.\nFormat: *02:30 PM* or *14:30* (Dhaka time)", parse_mode="Markdown", reply_markup=get_back_keyboard())

    elif text == "❌ Cancel Schedule":
        state["status"] = "IDLE"
        state["scheduled_time"] = None
        bot.reply_to(message, "❌ Schedule cancelled.", reply_markup=get_keyboard())

    elif text == "🧪 Spam Test":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TEST_EMAIL"
            bot.reply_to(message, "📧 Send the email address for the spam test.", reply_markup=get_back_keyboard())


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("🚀 Starting Bot...")
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()

    while True:
        try:
            print("🤖 Bot polling started...")
            bot.polling(none_stop=True, interval=0, timeout=30)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)
