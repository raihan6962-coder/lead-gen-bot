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
def generate_email_content(app_name, dev_name, rating, installs, contact_info, email_prompt, sender_email):
    if not dev_name or len(str(dev_name).strip()) == 0:
        dev_name = "Developer"
    # If dev name too long or weird, use generic
    if len(str(dev_name)) > 30:
        dev_name = "Developer"

    contact_html = contact_info.replace('\n', '<br>')

    prompt = f"""{email_prompt}

App Details:
- App Name: {app_name}
- Developer Name: {dev_name}
- Rating: {rating}
- Installs: {installs}

STRICT RULES:
1. You MUST start the email body with exactly: "Dear {dev_name},"
2. Write in plain text only. NO markdown like **bold** or *italics*.
3. Keep it professional and concise (3-4 paragraphs).
4. Use <br> for line breaks, NOT backslash-n.

Format your response EXACTLY like this (nothing before or after):
SUBJECT: [Your subject line here]
BODY: [Your email body here starting with Dear {dev_name},]"""

    try:
        chat = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=800
        )
        content = chat.choices[0].message.content.strip()

        # Parse subject and body
        if "SUBJECT:" in content and "BODY:" in content:
            subject = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
            raw_body = content.split("BODY:")[1].strip()
        else:
            # Fallback parsing
            lines = content.split('\n')
            subject = lines[0].replace("Subject:", "").replace("SUBJECT:", "").strip()
            raw_body = '\n'.join(lines[1:]).strip()

        # Clean the body
        clean_body = raw_body
        clean_body = clean_body.replace('**', '').replace('*', '')
        clean_body = clean_body.replace('\n\n', '<br><br>').replace('\n', '<br>')

        final_html_body = f"""<div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
{clean_body}<br><br>
{contact_html}<br><br>
<hr style="border: 0; border-top: 1px solid #eee; margin-top: 20px;">
<div style="text-align: center; padding-top: 10px;">
<a href="mailto:{sender_email}?subject=Unsubscribe%20Me&body=Please%20remove%20me%20from%20your%20mailing%20list." style="color: #999; font-size: 11px; text-decoration: underline;">Unsubscribe</a>
</div>
</div>"""

        return subject, final_html_body

    except Exception as e:
        print(f"Email generation error: {e}")
        fallback_body = f"""<div style="font-family: Arial, sans-serif; font-size: 14px; color: #333; max-width: 600px; margin: 0 auto;">
Dear {dev_name},<br><br>
I came across your app <b>{app_name}</b> on the Play Store and I'm impressed with what you've built.<br><br>
I'd love to discuss a potential collaboration that could help grow your app further. Please feel free to reach out.<br><br>
{contact_html}
</div>"""
        return f"Partnership Opportunity for {app_name}", fallback_body


# --- CORE ENGINE ---
def engine_thread(max_installs, max_rating, contact_info, email_prompt):
    global state
    gov_keywords = ['gov', 'government', 'ministry', 'department', 'council', 'national', 'authority', 'official', 'public']

    while state["current_kw_index"] < len(state["keywords"]):
        # Pause check
        while state["status"] == "PAUSED":
            time.sleep(1)

        # Stop check
        if state["status"] == "IDLE":
            break
        if state["total_leads"] >= 200:
            break

        kw = state["keywords"][state["current_kw_index"]]
        bot.send_message(state["chat_id"], f"🔍 Searching: *{kw}*", parse_mode="Markdown")

        try:
            # --- MULTI-QUERY EXPANSION: 5 variations to get 300-500 apps ---
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
                    time.sleep(0.5)  # Small delay between searches
                except Exception as e:
                    print(f"Search error for '{query}': {e}")
                    continue

            # Deduplicate by appId
            results = []
            seen_ids = set()
            for r in raw_results:
                if r['appId'] not in seen_ids:
                    seen_ids.add(r['appId'])
                    results.append(r)

            leads_in_this_kw = 0
            bot.send_message(state["chat_id"], f"📊 Found *{len(results)}* unique apps. Filtering now...", parse_mode="Markdown")

            for r in results:
                # Pause check inside loop
                while state["status"] == "PAUSED":
                    time.sleep(1)

                if state["status"] == "IDLE":
                    break
                if state["total_leads"] >= 200:
                    break

                app_id = r['appId']

                # Skip already scraped apps
                if app_id in state["scraped_apps"]:
                    continue
                state["scraped_apps"].add(app_id)

                # Fetch full app details
                try:
                    d = app(app_id)
                except Exception as e:
                    print(f"App fetch error {app_id}: {e}")
                    continue

                # --- SAFE VALUE PARSING (Fixes crash on None) ---
                raw_score = d.get('score')
                rating = float(raw_score) if raw_score is not None else 0.0

                raw_installs = d.get('minInstalls')
                installs = int(raw_installs) if raw_installs is not None else 0

                email = str(d.get('developerEmail', '') or '').strip().lower()

                # Skip if no email
                if not email:
                    continue

                # Skip if duplicate email
                if email in state["existing_emails"]:
                    print(f"♻️ Duplicate skip: {email}")
                    continue

                # Skip government apps
                dev_name_check = str(d.get('developer', '') or '').lower()
                if any(g in dev_name_check for g in gov_keywords):
                    continue

                # --- MAIN FILTER: rating <= max_rating AND installs <= max_installs ---
                # NOTE: rating=0.0 means new app with no ratings yet - we INCLUDE these
                if rating <= max_rating and installs <= max_installs:

                    # --- CHECK SENDER AVAILABILITY ---
                    try:
                        senders_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=10)
                        senders = senders_res.json()
                        available_senders = [s for s in senders if int(s.get('sent', 0)) < int(s.get('limit', 0))]
                    except Exception as e:
                        print(f"Sender check error: {e}")
                        continue

                    if not available_senders:
                        bot.send_message(state["chat_id"], "⚠️ All senders reached daily limit! Automation Paused.", reply_markup=get_keyboard())
                        state["status"] = "PAUSED"
                        break

                    current_sender = available_senders[0]

                    # Notify lead found
                    dev_display = str(d.get('developer', 'Unknown'))
                    bot.send_message(
                        state["chat_id"],
                        f"✨ *Lead Found!*\n📱 App: {d.get('title', 'Unknown')}\n⭐ Rating: {rating}\n📥 Installs: {installs:,}\n📧 Generating email...",
                        parse_mode="Markdown"
                    )

                    # Generate email
                    subject, body = generate_email_content(
                        d.get('title', 'App'),
                        dev_display,
                        rating,
                        installs,
                        contact_info,
                        email_prompt,
                        current_sender['email']
                    )

                    # Save lead to sheet
                    try:
                        requests.post(SHEET_WEB_APP_URL, json={
                            "action": "save_lead",
                            "app_name": d.get('title', ''),
                            "dev_name": dev_display,
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
                    except Exception as e:
                        print(f"Sheet save error: {e}")

                    # Add to local duplicate set immediately
                    state["existing_emails"].add(email)

                    # Send email
                    try:
                        mail_res = requests.post(
                            current_sender['url'],
                            json={"action": "send_email", "to": email, "subject": subject, "body": body},
                            timeout=30
                        )
                        mail_status = mail_res.text.strip()
                    except Exception as e:
                        mail_status = f"Error: {e}"

                    if mail_status == "Success":
                        # Increment sender count
                        try:
                            requests.post(SHEET_WEB_APP_URL, json={"action": "increment_sender", "email": current_sender['email']}, timeout=10)
                        except:
                            pass

                        state["total_leads"] += 1
                        leads_in_this_kw += 1

                        bot.send_message(
                            state["chat_id"],
                            f"✅ *Lead #{state['total_leads']} Sent!*\n📧 To: `{email}`\n📤 Via: {current_sender['email']}",
                            parse_mode="Markdown"
                        )

                        # Random delay between emails (60-120 seconds)
                        delay = random.randint(60, 120)
                        bot.send_message(state["chat_id"], f"⏳ Waiting {delay}s before next lead...")
                        for _ in range(delay):
                            if state["status"] != "RUNNING":
                                break
                            time.sleep(1)
                    else:
                        bot.send_message(state["chat_id"], f"❌ Email failed to {email}: {mail_status}")

            # Keyword summary
            if leads_in_this_kw == 0:
                bot.send_message(state["chat_id"], f"⚠️ No leads from '{kw}'. Moving to next keyword...")
            else:
                bot.send_message(state["chat_id"], f"📌 Keyword '{kw}' done. Leads found: {leads_in_this_kw}")

        except Exception as e:
            print(f"Engine error on keyword '{kw}': {e}")
            bot.send_message(state["chat_id"], f"⚠️ Error on keyword '{kw}'. Skipping...")

        if state["status"] == "RUNNING":
            state["current_kw_index"] += 1

    # All done
    if state["status"] == "RUNNING":
        bot.send_message(
            state["chat_id"],
            f"🎉 *Automation Finished!*\nTotal emails sent: *{state['total_leads']}*",
            parse_mode="Markdown",
            reply_markup=get_keyboard()
        )
        state["status"] = "IDLE"


def start_engine():
    global state
    try:
        bot.send_message(state["chat_id"], "🔄 Fetching settings from Sheet...")

        # Get settings
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}, timeout=15).json()

        max_installs = int(str(res.get('max_installs', '10000')).replace(',', '').strip())
        max_rating = float(str(res.get('max_rating', '4.0')).strip())
        contact_info = res.get('contact_info', '')
        email_prompt = res.get('email_prompt', '')
        niche = res.get('niche', 'mobile apps')
        keyword_prompt = res.get('keyword_prompt', 'Generate search terms for')

        bot.send_message(state["chat_id"], f"✅ Settings loaded!\n⭐ Max Rating: {max_rating}\n📥 Max Installs: {max_installs:,}")

        # Load existing emails to avoid duplicates
        try:
            db_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_existing_emails"}, timeout=15).json()
            state["existing_emails"] = set(db_res) if isinstance(db_res, list) else set()
            bot.send_message(state["chat_id"], f"🗄️ Database loaded. {len(state['existing_emails'])} existing emails found.")
        except:
            state["existing_emails"] = set()

        # Generate keywords if not already generated
        if not state["keywords"]:
            bot.send_message(state["chat_id"], "🧠 AI generating keywords...")

            kw_chat = groq_client.chat.completions.create(
                messages=[{
                    "role": "user",
                    "content": f"{keyword_prompt} Niche: {niche}. Give me 150 unique short Play Store search terms separated by commas only. Each term should be 1-4 words. DO NOT write the word 'keywords'. DO NOT use numbers, bullet points, or new lines. Just comma-separated terms."
                }],
                model="llama-3.1-8b-instant",
                max_tokens=2000
            )

            raw_text = kw_chat.choices[0].message.content

            # SUPER CLEANER: Handle all possible AI output formats
            raw_text = raw_text.replace('\n', ',')  # newlines to commas
            raw_text = raw_text.replace(';', ',')   # semicolons to commas
            raw_kws = raw_text.split(',')

            cleaned_kws = []
            for k in raw_kws:
                k = re.sub(r'^\d+[\.\)\-]?\s*', '', k)   # Remove "1." "1)" "1-"
                k = k.replace('**', '').replace('*', '')   # Remove markdown
                k = k.replace('keywords', '').replace('keyword', '')  # Remove "keywords" word
                k = k.replace('search terms', '').replace('search term', '')
                k = k.strip().strip('"').strip("'")
                if len(k) > 2 and len(k) < 50:  # Valid length
                    cleaned_kws.append(k)

            # Remove duplicates while preserving order
            seen = set()
            final_kws = []
            for k in cleaned_kws:
                if k.lower() not in seen:
                    seen.add(k.lower())
                    final_kws.append(k)

            state["keywords"] = final_kws
            state["current_kw_index"] = 0
            state["total_leads"] = 0
            state["scraped_apps"] = set()

            if not state["keywords"]:
                bot.send_message(state["chat_id"], "❌ Keyword generation failed. Please try again.", reply_markup=get_keyboard())
                state["status"] = "IDLE"
                return

            bot.send_message(state["chat_id"], f"✅ Generated *{len(state['keywords'])}* clean keywords!\n\nStarting engine now...", parse_mode="Markdown")

        else:
            bot.send_message(state["chat_id"], f"▶️ Resuming from keyword #{state['current_kw_index'] + 1} of {len(state['keywords'])}")

        # Start the engine in a separate thread
        threading.Thread(
            target=engine_thread,
            args=(max_installs, max_rating, contact_info, email_prompt),
            daemon=True
        ).start()

    except Exception as e:
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], f"❌ System Error: {e}", reply_markup=get_keyboard())
        print(f"start_engine error: {e}")


def run_spam_test(test_email):
    bot.send_message(state["chat_id"], "🔄 Preparing spam test...")
    try:
        senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=10).json()
        if not senders:
            bot.send_message(state["chat_id"], "❌ No senders found! Add a sender first.", reply_markup=get_keyboard())
            return

        sender = senders[0]
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}, timeout=10).json()

        # Try to get a real lead for test
        try:
            lead_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_one_lead"}, timeout=10).json()
            if lead_res.get("found"):
                app_name = lead_res["app_name"]
                dev_name = lead_res["dev_name"]
                rating = lead_res["rating"]
                installs = lead_res["installs"]
            else:
                raise Exception("No lead found")
        except:
            app_name = "FinTrack - Budget Manager"
            dev_name = "DevStudio Labs"
            rating = 3.8
            installs = 5000

        subject, body = generate_email_content(
            app_name, dev_name, rating, installs,
            res.get('contact_info', ''),
            res.get('email_prompt', 'Write a professional outreach email.'),
            sender['email']
        )

        mail_res = requests.post(
            sender['url'],
            json={"action": "send_email", "to": test_email, "subject": subject, "body": body},
            timeout=30
        )

        if mail_res.text.strip() == "Success":
            bot.send_message(
                state["chat_id"],
                f"✅ *Test email sent!*\n📧 To: `{test_email}`\n📤 Via: {sender['email']}\n\nCheck your inbox (and spam folder)!",
                parse_mode="Markdown",
                reply_markup=get_keyboard()
            )
        else:
            bot.send_message(state["chat_id"], f"❌ Send failed: {mail_res.text}", reply_markup=get_keyboard())

    except Exception as e:
        bot.send_message(state["chat_id"], f"❌ Spam test error: {e}", reply_markup=get_keyboard())


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
                    bot.send_message(
                        state["chat_id"],
                        "⏰ Scheduled time reached! Starting automation...",
                        reply_markup=get_keyboard()
                    )
                    start_engine()
                    time.sleep(61)  # Prevent double-trigger
        except Exception as e:
            print(f"Scheduler error: {e}")
        time.sleep(10)


# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    global state
    state["chat_id"] = message.chat.id
    state["status"] = "IDLE"
    bot.reply_to(
        message,
        "👋 *Welcome Boss!*\n\nYour B2B Lead Generation Bot is ready.\nWhat would you like to do?",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )


@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    global state

    if call.data == "back_to_main":
        state["status"] = "IDLE"
        state["temp_sender_url"] = None
        state["temp_sender_email"] = None
        bot.send_message(call.message.chat.id, "🔙 Returned to Main Menu.", reply_markup=get_keyboard())

    elif call.data == "add_new_sender":
        script_code = """function doPost(e) {
  var data = JSON.parse(e.postData.contents);
  if (data.action == "send_email") {
    try {
      GmailApp.sendEmail(data.to, data.subject, "", {htmlBody: data.body});
      return ContentService.createTextOutput("Success");
    } catch (err) {
      return ContentService.createTextOutput("Error: " + err.toString());
    }
  }
}"""
        bot.send_message(
            call.message.chat.id,
            f"📝 *Step 1:* Go to your Gmail → Extensions → Apps Script\n\n*Step 2:* Paste this code and deploy as Web App:\n\n`{script_code}`\n\n*Step 3:* Send me the Web App URL.",
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
        bot.send_message(call.message.chat.id, f"⚠️ Delete sender *{email_to_del}*?", parse_mode="Markdown", reply_markup=markup)

    elif call.data.startswith("confirm_del_"):
        email_to_del = call.data.split("confirm_del_")[1]
        requests.post(SHEET_WEB_APP_URL, json={"action": "delete_sender", "email": email_to_del})
        bot.send_message(call.message.chat.id, f"🗑️ Deleted *{email_to_del}* successfully!", parse_mode="Markdown")

    elif call.data == "cancel_del":
        bot.send_message(call.message.chat.id, "❌ Deletion cancelled.")


@bot.message_handler(func=lambda msg: True)
def handle_messages(message):
    global state
    text = message.text.strip() if message.text else ""
    state["chat_id"] = message.chat.id

    # --- BACK BUTTON (Works from any state) ---
    if text == "🔙 Back to Main Menu":
        state["status"] = "IDLE"
        state["temp_sender_url"] = None
        state["temp_sender_email"] = None
        bot.reply_to(message, "🔙 Returned to Main Menu.", reply_markup=get_keyboard())
        return

    # --- WAITING STATES ---
    if state["status"] == "WAITING_SENDER_URL":
        if "script.google.com" in text:
            state["temp_sender_url"] = text
            state["status"] = "WAITING_SENDER_EMAIL"
            bot.reply_to(message, "✅ URL saved!\n\nNow send the *Gmail address* for this sender account:", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid URL. Must be a Google Apps Script URL (script.google.com).", reply_markup=get_back_keyboard())
        return

    if state["status"] == "WAITING_SENDER_EMAIL":
        if "@" in text and "." in text:
            state["temp_sender_email"] = text.lower().strip()
            state["status"] = "WAITING_SENDER_LIMIT"
            bot.reply_to(message, "✅ Email saved!\n\nNow send the *daily sending limit* for this email (e.g., 20):", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid email address. Try again.", reply_markup=get_back_keyboard())
        return

    if state["status"] == "WAITING_SENDER_LIMIT":
        if text.isdigit() and int(text) > 0:
            requests.post(SHEET_WEB_APP_URL, json={
                "action": "add_sender",
                "email": state["temp_sender_email"],
                "url": state["temp_sender_url"],
                "limit": int(text)
            })
            bot.reply_to(
                message,
                f"🎉 *Sender added successfully!*\n📧 Email: {state['temp_sender_email']}\n📊 Daily Limit: {text}",
                parse_mode="Markdown",
                reply_markup=get_keyboard()
            )
            state["temp_sender_url"] = None
            state["temp_sender_email"] = None
            state["status"] = "IDLE"
        else:
            bot.reply_to(message, "❌ Please send a valid number (e.g., 20).", reply_markup=get_back_keyboard())
        return

    if state["status"] == "WAITING_TIME":
        parsed = parse_time(text)
        if parsed:
            state["scheduled_time"] = parsed
            state["status"] = "SCHEDULED"
            bot.reply_to(
                message,
                f"✅ *Automation scheduled at {parsed} (Dhaka Time)!*\nBot will auto-start every day at this time.\n\nPress ❌ Cancel Schedule to stop.",
                parse_mode="Markdown",
                reply_markup=get_keyboard()
            )
        else:
            bot.reply_to(message, "❌ Invalid time format. Please send like: *02:30 PM* or *14:30*", parse_mode="Markdown", reply_markup=get_back_keyboard())
        return

    if state["status"] == "WAITING_TEST_EMAIL":
        if "@" in text and "." in text:
            state["status"] = "IDLE"
            bot.reply_to(message, f"🚀 Sending test email to {text}...", reply_markup=get_keyboard())
            threading.Thread(target=run_spam_test, args=(text,), daemon=True).start()
        else:
            bot.reply_to(message, "❌ Invalid email. Try again.", reply_markup=get_back_keyboard())
        return

    # --- MAIN MENU BUTTONS ---
    if text == "🚀 Start Automation":
        if state["status"] in ["IDLE", "SCHEDULED"]:
            state["status"] = "RUNNING"
            bot.reply_to(message, "🚀 *Automation Starting...*", parse_mode="Markdown", reply_markup=get_keyboard())
            threading.Thread(target=start_engine, daemon=True).start()
        else:
            bot.reply_to(message, f"⚠️ Bot is currently: {state['status']}", reply_markup=get_keyboard())

    elif text == "🛑 Stop Automation":
        if state["status"] == "RUNNING":
            state["status"] = "PAUSED"
            bot.reply_to(message, "🛑 Automation Paused. Press ▶️ Resume to continue or ⏹️ Permanent Stop to reset.", reply_markup=get_keyboard())

    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            state["status"] = "RUNNING"
            bot.reply_to(message, "▶️ *Resuming automation...*", parse_mode="Markdown", reply_markup=get_keyboard())

    elif text == "⏹️ Permanent Stop":
        state["status"] = "IDLE"
        state["keywords"] = []
        state["current_kw_index"] = 0
        state["total_leads"] = 0
        state["scraped_apps"] = set()
        bot.reply_to(message, "⏹️ Automation fully reset. All progress cleared.", reply_markup=get_keyboard())

    elif text == "📅 Schedule Automation":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TIME"
            bot.reply_to(message, "⏰ Send the time to schedule daily automation.\nFormat: *02:30 PM* or *14:30* (Dhaka time)", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, f"⚠️ Cannot schedule while bot is: {state['status']}", reply_markup=get_keyboard())

    elif text == "❌ Cancel Schedule":
        state["status"] = "IDLE"
        state["scheduled_time"] = None
        bot.reply_to(message, "❌ Schedule cancelled.", reply_markup=get_keyboard())

    elif text == "🧪 Spam Test":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TEST_EMAIL"
            bot.reply_to(message, "📧 Send the email address where you want to receive the test email:", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, f"⚠️ Cannot run spam test while bot is: {state['status']}", reply_markup=get_keyboard())

    elif text == "📧 Manage Senders":
        try:
            senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}, timeout=10).json()
        except Exception as e:
            bot.reply_to(message, f"❌ Error connecting to Sheet: {e}", reply_markup=get_keyboard())
            return

        markup = InlineKeyboardMarkup()
        msg_text = "📋 *Your Sender Accounts:*\n\n"

        if not senders:
            msg_text += "_No senders added yet._\n"
        else:
            for i, s in enumerate(senders):
                sent = int(s.get('sent', 0))
                limit = int(s.get('limit', 0))
                status_icon = "🟢" if sent < limit else "🔴"
                msg_text += f"{i+1}. {status_icon} {s['email']}\n    Sent: {sent}/{limit}\n\n"
                markup.add(InlineKeyboardButton(f"🗑️ Delete {s['email']}", callback_data=f"del_{s['email']}"))

        markup.add(InlineKeyboardButton("➕ Add New Sender", callback_data="add_new_sender"))
        markup.add(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main"))

        bot.reply_to(message, msg_text, parse_mode="Markdown", reply_markup=markup)

    else:
        # Unknown message
        bot.reply_to(message, "Please use the keyboard buttons below.", reply_markup=get_keyboard())


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("🚀 Starting B2B Lead Generation Bot...")
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    print("✅ Web server and scheduler started.")
    while True:
        try:
            print("🤖 Bot polling started...")
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)
