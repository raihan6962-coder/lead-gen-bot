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
def home(): return "Bot is Alive and Running 24/7!"
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

def get_schedule_options():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("✅ Everyday at this time"), KeyboardButton("❌ Cancel Schedule"))
    return markup

def parse_time(time_str):
    time_str = time_str.strip().upper()
    try: return datetime.strptime(time_str, "%I:%M %p").strftime("%H:%M")
    except:
        try: return datetime.strptime(time_str, "%H:%M").strftime("%H:%M")
        except: return None

# --- AI EMAIL GENERATOR ---
def generate_email_content(app_name, dev_name, rating, installs, description, contact_info, email_prompt, sender_email):
    if not dev_name or len(dev_name) > 20: dev_name = "Developer"
    contact_html = contact_info.replace('\n', '<br>')
    
    prompt = f"""
    {email_prompt}
    App Details: App Name: {app_name}, Developer: {dev_name}, Rating: {rating}, Installs: {installs}
    RULES:
    1. Write in plain text with normal paragraphs.
    2. DO NOT use markdown like **bold** or *italics*.
    Format EXACTLY like this:
    SUBJECT: [Subject Line]
    BODY: [Email Body]
    """
    try:
        chat = groq_client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model="llama-3.1-8b-instant")
        content = chat.choices[0].message.content
        subject = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
        raw_body = content.split("BODY:")[1].strip()
        
        clean_body = raw_body.replace('**', '').replace('*', '').replace('\n\n', '<br><br>').replace('\n', '<br>')
        
        final_html_body = f"""
        <div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
            {clean_body}<br><br>{contact_html}<br><br><br>
            <hr style="border: 0; border-top: 1px solid #eee;">
            <div style="text-align: center; padding-top: 10px;">
                <a href="mailto:{sender_email}?subject=Unsubscribe%20Me&body=Please%20remove%20me%20from%20your%20mailing%20list." style="color: #999; font-size: 12px; text-decoration: underline;">Unsubscribe from future emails</a>
            </div>
        </div>
        """
        return subject, final_html_body
    except:
        return f"Collaboration for {app_name}", f"Hi {dev_name},<br><br>Let's collaborate.<br><br>{contact_html}"

# --- CORE ENGINE ---
def engine_thread(max_installs, max_rating, contact_info, email_prompt):
    global state
    gov_keywords = ['gov', 'government', 'ministry', 'department', 'state', 'council', 'national', 'authority']
    
    while state["current_kw_index"] < len(state["keywords"]):
        while state["status"] == "PAUSED": time.sleep(1)
        if state["status"] == "IDLE" or state["total_leads"] >= 200: break
        
        kw = state["keywords"][state["current_kw_index"]]
        bot.send_message(state["chat_id"], f"🔍 Searching Keyword: *{kw}*", parse_mode="Markdown")
        
        try:
            # AGGRESSIVE SEARCH
            raw_results = search(kw, lang='en', country='us', n_hits=200)
            if len(raw_results) < 50:
                raw_results += search(kw + " app", lang='en', country='us', n_hits=100)
                raw_results += search(kw + " free", lang='en', country='us', n_hits=100)
            
            results = []
            seen = set()
            for r in raw_results:
                if r['appId'] not in seen:
                    seen.add(r['appId'])
                    results.append(r)
            
            leads_in_this_kw = 0
            bot.send_message(state["chat_id"], f"📊 Found {len(results)} unique apps. Filtering now...")
            
            for r in results:
                while state["status"] == "PAUSED": time.sleep(1)
                if state["status"] == "IDLE" or state["total_leads"] >= 200: break
                
                # SENDER CHECK
                senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}).json()
                available_senders = [s for s in senders if int(s['sent']) < int(s['limit'])]
                
                if not available_senders:
                    bot.send_message(state["chat_id"], "⚠️ All senders have reached their daily limit! Pausing automation.")
                    state["status"] = "PAUSED"
                    break
                
                current_sender = available_senders[0] 
                
                app_id = r['appId']
                if app_id in state["scraped_apps"]: continue
                state["scraped_apps"].add(app_id)
                
                try: d = app(app_id)
                except: continue
                
                # FAST FILTER: Email na thakle sathe sathe skip
                email = str(d.get('developerEmail', '')).strip().lower()
                if not email or email in state["existing_emails"]: continue
                
                dev_name = str(d.get('developer', '')).lower()
                if any(g in dev_name for g in gov_keywords): continue 
                
                rating = float(d.get('score', 0))
                installs = int(d.get('minInstalls', 0))
                
                if rating > 0 and rating <= max_rating and installs <= max_installs:
                    bot.send_message(state["chat_id"], f"✨ Lead Found: *{d['title']}*\nGenerating Email...", parse_mode="Markdown")
                    
                    subject, body = generate_email_content(d['title'], d['developer'], rating, installs, d.get('description', ''), contact_info, email_prompt, current_sender['email'])
                    
                    requests.post(SHEET_WEB_APP_URL, json={
                        "action": "save_lead", "app_name": d['title'], "dev_name": d['developer'],
                        "email": email, "subject": subject, "body": body, "installs": installs,
                        "rating": rating, "link": d['url'], "category": d['genre'], 
                        "website": d.get('developerWebsite', ''), "updated": d.get('updated', '')
                    })
                    state["existing_emails"].add(email)
                    
                    mail_res = requests.post(current_sender['url'], json={"action": "send_email", "to": email, "subject": subject, "body": body})
                    
                    if mail_res.text == "Success":
                        requests.post(SHEET_WEB_APP_URL, json={"action": "increment_sender", "email": current_sender['email']})
                        state["total_leads"] += 1
                        leads_in_this_kw += 1
                        bot.send_message(state["chat_id"], f"✅ Lead #{state['total_leads']} Sent to: {email}\n*(Sent via: {current_sender['email']})*", parse_mode="Markdown")
                        
                        delay = random.randint(60, 120)
                        for _ in range(delay):
                            if state["status"] != "RUNNING": break
                            time.sleep(1)
            
            if leads_in_this_kw < 3: bot.send_message(state["chat_id"], f"⚠️ Moving to next keyword...")
                
        except Exception as e: 
            print(f"Error: {e}")
        
        if state["status"] == "RUNNING": state["current_kw_index"] += 1

    if state["status"] == "RUNNING":
        bot.send_message(state["chat_id"], f"🎉 Automation Finished! Total {state['total_leads']} emails sent.", reply_markup=get_keyboard())
        state["status"] = "IDLE"

def start_engine():
    global state
    try:
        bot.send_message(state["chat_id"], "🔄 Fetching settings and checking database...")
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}).json()
        max_installs = int(str(res['max_installs']).replace(',', '').strip())
        max_rating = float(str(res['max_rating']).strip())
        
        db_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_existing_emails"}).json()
        state["existing_emails"] = set(db_res)
        
        if not state["keywords"]: 
            bot.send_message(state["chat_id"], "🧠 AI is generating keywords...")
            chat = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": f"{res['keyword_prompt']} Niche: {res['niche']}. Give me 200 unique broad keywords separated by commas."}],
                model="llama-3.1-8b-instant",
            )
            raw_kws = chat.choices[0].message.content.split(',')
            state["keywords"] = [re.sub(r'^\d+[\.\)]?\s*', '', k).strip() for k in raw_kws if len(k.strip()) > 3]
            state["current_kw_index"] = 0
            state["total_leads"] = 0
            state["scraped_apps"] = set()
            
        threading.Thread(target=engine_thread, args=(max_installs, max_rating, res['contact_info'], res['email_prompt'])).start()
    except Exception as e:
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], f"❌ System Error: {e}", reply_markup=get_keyboard())

def run_spam_test(test_email):
    bot.send_message(state["chat_id"], "🔄 Fetching data for Spam Test...")
    try:
        senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}).json()
        if not senders:
            bot.send_message(state["chat_id"], "❌ No senders added! Please add a sender first.", reply_markup=get_keyboard())
            return
        sender = senders[0] 
        
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}).json()
        lead_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_one_lead"}).json()
        
        if lead_res.get("found"):
            app_name, dev_name, rating, installs = lead_res["app_name"], lead_res["dev_name"], lead_res["rating"], lead_res["installs"]
            description = "This is a great application that provides excellent value to its users."
        else:
            app_name, dev_name, rating, installs = "Demo Finance App", "Demo Studio", 4.8, 50000
            description = "A revolutionary app designed to make daily transactions easier."
            
        subject, body = generate_email_content(app_name, dev_name, rating, installs, description, res['contact_info'], res['email_prompt'], sender['email'])
        mail_res = requests.post(sender['url'], json={"action": "send_email", "to": test_email, "subject": subject, "body": body})
        
        if mail_res.text == "Success":
            bot.send_message(state["chat_id"], f"✅ Spam Test Email sent to: {test_email}\n*(Sent via: {sender['email']})*", parse_mode="Markdown", reply_markup=get_keyboard())
        else:
            bot.send_message(state["chat_id"], f"❌ Failed: {mail_res.text}", reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(state["chat_id"], f"❌ Error: {e}", reply_markup=get_keyboard())

# --- SCHEDULER THREAD ---
def run_scheduler():
    global state
    tz = pytz.timezone('Asia/Dhaka')
    while True:
        if state["status"] == "SCHEDULED" and state["scheduled_time"]:
            now = datetime.now(tz).strftime("%H:%M")
            if now == state["scheduled_time"]:
                state["status"] = "RUNNING"
                bot.send_message(state["chat_id"], "⏰ Scheduled Time Reached! Starting Automation...", reply_markup=get_keyboard())
                start_engine()
                time.sleep(60) 
        time.sleep(10)

# --- BOT COMMANDS & CALLBACKS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    global state
    state["chat_id"] = message.chat.id
    state["status"] = "IDLE"
    bot.reply_to(message, "👋 Welcome Boss! What would you like to do?", reply_markup=get_keyboard())

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
    } catch (error) { return ContentService.createTextOutput("Error: " + error.toString()); }
  }
}"""
        bot.send_message(call.message.chat.id, f"📝 **Deploy this code in your new Email's Apps Script:**\n\n`{script_code}`\n\nAfter deploying, please send me the **Web App URL**.", parse_mode="Markdown", reply_markup=get_back_keyboard())
        state["status"] = "WAITING_SENDER_URL"
        
    elif call.data.startswith("del_"):
        email_to_del = call.data.split("del_")[1]
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Allow", callback_data=f"confirm_del_{email_to_del}"), InlineKeyboardButton("❌ Not Now", callback_data="cancel_del"))
        bot.send_message(call.message.chat.id, f"Are you sure you want to delete {email_to_del}?", reply_markup=markup)
        
    elif call.data.startswith("confirm_del_"):
        email_to_del = call.data.split("confirm_del_")[1]
        requests.post(SHEET_WEB_APP_URL, json={"action": "delete_sender", "email": email_to_del})
        bot.send_message(call.message.chat.id, f"🗑️ Deleted {email_to_del} successfully!")
        
    elif call.data == "cancel_del":
        bot.send_message(call.message.chat.id, "❌ Deletion cancelled.")

@bot.message_handler(func=lambda msg: True)
def handle_messages(message):
    global state
    text = message.text
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
            bot.reply_to(message, "✅ URL received. Now, what is the **Email Address** for this sender?", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid URL. Please send a valid Google Apps Script URL.", reply_markup=get_back_keyboard())
            
    elif state["status"] == "WAITING_SENDER_EMAIL":
        if "@" in text:
            state["temp_sender_email"] = text
            state["status"] = "WAITING_SENDER_LIMIT"
            bot.reply_to(message, "✅ Email received. What is the **Daily Sending Limit** for this email? (e.g., 20)", parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            bot.reply_to(message, "❌ Invalid Email. Try again.", reply_markup=get_back_keyboard())
            
    elif state["status"] == "WAITING_SENDER_LIMIT":
        if text.isdigit():
            requests.post(SHEET_WEB_APP_URL, json={
                "action": "add_sender", "email": state["temp_sender_email"], 
                "url": state["temp_sender_url"], "limit": int(text)
            })
            bot.reply_to(message, f"🎉 Sender {state['temp_sender_email']} added successfully with limit {text}!", reply_markup=get_keyboard())
            state["status"] = "IDLE"
        else:
            bot.reply_to(message, "❌ Please send a valid number.", reply_markup=get_back_keyboard())

    elif text == "📧 Manage Senders":
        try:
            senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}).json()
        except:
            bot.reply_to(message, "❌ Error connecting to Sheet. Make sure you have created the 'Senders' tab.", reply_markup=get_keyboard())
            return

        markup = InlineKeyboardMarkup()
        msg_text = "📋 *Your Senders:*\n\n"
        if not senders: 
            msg_text += "No senders added yet.\n"
        else:
            for i, s in enumerate(senders):
                msg_text += f"{i+1}. {s['email']} (Sent: {s['sent']}/{s['limit']})\n"
                markup.add(InlineKeyboardButton(f"🗑️ Delete {s['email']}", callback_data=f"del_{s['email']}"))
            
        markup.add(InlineKeyboardButton("➕ Add New Sender", callback_data="add_new_sender"))
        markup.add(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")) 
        
        bot.reply_to(message, msg_text, parse_mode="Markdown", reply_markup=markup)

    elif text == "🚀 Start Automation":
        if state["status"] in ["IDLE", "SCHEDULED"]:
            state["status"] = "RUNNING"
            bot.reply_to(message, "🚀 Starting Automation...", reply_markup=get_keyboard())
            start_engine()
            
    elif text == "🛑 Stop Automation":
        if state["status"] == "RUNNING":
            state["status"] = "PAUSED"
            bot.reply_to(message, "🛑 Automation Paused.", reply_markup=get_keyboard())
            
    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            state["status"] = "RUNNING"
            bot.reply_to(message, "▶️ Resuming...", reply_markup=get_keyboard())
            
    elif text == "⏹️ Permanent Stop":
        state["status"] = "IDLE"
        state["keywords"] = [] 
        bot.reply_to(message, "⏹️ Automation Reset.", reply_markup=get_keyboard())
        
    elif text == "📅 Schedule Automation":
        state["status"] = "WAITING_TIME"
        bot.reply_to(message, "⏰ Send time (e.g., 02:30 PM).", reply_markup=get_back_keyboard())
        
    elif text == "❌ Cancel Schedule":
        state["status"] = "IDLE"
        state["scheduled_time"] = None
        bot.reply_to(message, "❌ Schedule Cancelled.", reply_markup=get_keyboard())
        
    elif text == "🧪 Spam Test":
        if state["status"] == "IDLE":
            state["status"] = "WAITING_TEST_EMAIL"
            bot.reply_to(message, "📧 Send the email address to receive the test.", reply_markup=get_back_keyboard())
            
    elif state["status"] == "WAITING_TEST_EMAIL":
        if "@" in text:
            state["status"] = "IDLE"
            threading.Thread(target=run_spam_test, args=(text,)).start()
        else:
            bot.reply_to(message, "❌ Invalid email.", reply_markup=get_back_keyboard())

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    threading.Thread(target=run_scheduler).start()
    while True:
        try:
            print("🤖 Bot connecting to Telegram...")
            bot.polling(none_stop=True)
        except Exception as e:
            time.sleep(5)
