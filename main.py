import requests, telebot, time, random, os, threading, re
from datetime import datetime
import pytz
from flask import Flask
from groq import Groq
from google_play_scraper import search, app
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# --- FLASK WEB SERVER ---
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Bot is Alive and Running 24/7!"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# --- CONFIG ---
SHEET_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzI5eCCU_Gci6M0jFr5I_Ph48CqUvvP4_nkpngWtjFafVSr_i75yqKX37ZMG4qwG0_V/exec"
EMAIL_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbwrwh2vi677K1KyI6XkDObTCflb2yqtJp93kIWYxOj3uoUUt0PsskH5fSkgGTq1-jHW9A/exec"
BOT_TOKEN = "8709829378:AAEJJQ8jm_oTyAcGenBrIfLi4KYHRVcSJbo"
GROQ_API_KEY = "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"

# Ekhane apnar email ta din (Unsubscribe link e eita use hobe)
SENDER_EMAIL = "aburaihan6963@gmail.com" 

bot = telebot.TeleBot(BOT_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# --- STATE MANAGEMENT ---
state = {
    "status": "IDLE", 
    "keywords": [],
    "current_kw_index": 0,
    "total_leads": 0,
    "scraped_apps": set(),
    "existing_emails": set(), # Duplicate check er jonno database
    "chat_id": None,
    "scheduled_time": None
}

# --- KEYBOARDS ---
def get_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    if state["status"] == "IDLE":
        markup.add(KeyboardButton("🚀 Start Automation"), KeyboardButton("📅 Schedule Automation"))
    elif state["status"] == "RUNNING":
        markup.add(KeyboardButton("🛑 Stop Automation"))
    elif state["status"] == "PAUSED":
        markup.add(KeyboardButton("▶️ Resume"), KeyboardButton("⏹️ Permanent Stop"))
    elif state["status"] == "SCHEDULED":
        markup.add(KeyboardButton("❌ Cancel Schedule"))
    return markup

def get_schedule_options():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("✅ Everyday at this time"), KeyboardButton("❌ Cancel Schedule"))
    return markup

# --- TIME PARSER ---
def parse_time(time_str):
    time_str = time_str.strip().upper()
    try:
        t = datetime.strptime(time_str, "%I:%M %p")
        return t.strftime("%H:%M")
    except ValueError:
        try:
            t = datetime.strptime(time_str, "%H:%M")
            return t.strftime("%H:%M")
        except ValueError:
            return None

# --- AI EMAIL GENERATOR (PERFECT HTML FORMAT) ---
def generate_email_content(app_name, dev_name, rating, installs, description, contact_info, email_prompt):
    if not dev_name or len(dev_name) > 20: dev_name = "Developer"
    
    prompt = f"""
    {email_prompt}
    
    App Details: App Name: {app_name}, Developer: {dev_name}, Rating: {rating}, Installs: {installs}
    
    RULES:
    1. Write in plain text with normal paragraphs.
    2. DO NOT use markdown like **bold** or *italics*.
    3. Keep it professional and clean.
    
    Format EXACTLY like this:
    SUBJECT: [Subject Line]
    BODY: [Email Body]
    """
    try:
        chat = groq_client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model="llama-3.1-8b-instant")
        content = chat.choices[0].message.content
        subject = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
        raw_body = content.split("BODY:")[1].strip()
        
        # --- HTML FORMATTER ---
        # Clean markdown
        clean_body = raw_body.replace('**', '').replace('*', '')
        # Convert newlines to HTML breaks
        clean_body = clean_body.replace('\n\n', '<br><br>').replace('\n', '<br>')
        # Format contact info
        contact_html = contact_info.replace('\n', '<br>')
        
        # Final HTML Assembly with Centered Unsubscribe
        final_html_body = f"""
        <div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
            {clean_body}
            <br><br>
            {contact_html}
            <br><br><br>
            <hr style="border: 0; border-top: 1px solid #eee;">
            <div style="text-align: center; padding-top: 10px;">
                <a href="mailto:{SENDER_EMAIL}?subject=Unsubscribe%20Me&body=Please%20remove%20me%20from%20your%20mailing%20list." style="color: #999; font-size: 12px; text-decoration: underline;">Unsubscribe from future emails</a>
            </div>
        </div>
        """
        return subject, final_html_body
    except Exception as e:
        print(f"Email Gen Error: {e}")
        return f"Collaboration for {app_name}", f"Hi {dev_name},<br><br>Let's collaborate.<br><br>{contact_info}"

# --- CORE ENGINE ---
def engine_thread(max_installs, max_rating, contact_info, email_prompt):
    global state
    gov_keywords = ['gov', 'government', 'ministry', 'department', 'state', 'council', 'national', 'authority']
    
    while state["current_kw_index"] < len(state["keywords"]):
        while state["status"] == "PAUSED": time.sleep(1)
        if state["status"] == "IDLE": break 
        if state["total_leads"] >= 200: break
        
        kw = state["keywords"][state["current_kw_index"]]
        bot.send_message(state["chat_id"], f"🔍 Deep Searching Keyword: *{kw}*", parse_mode="Markdown")
        
        try:
            raw_results = search(kw, lang='en', country='us', n_hits=150)
            if len(raw_results) < 100:
                raw_results += search(kw + " app", lang='en', country='us', n_hits=100)
                raw_results += search(kw + " free", lang='en', country='us', n_hits=100)
            
            results = []
            seen = set()
            for r in raw_results:
                if r['appId'] not in seen:
                    seen.add(r['appId'])
                    results.append(r)
            
            if not results:
                bot.send_message(state["chat_id"], f"⚠️ No apps found for '{kw}'. Moving to next...")
                state["current_kw_index"] += 1
                continue
                
            bot.send_message(state["chat_id"], f"📊 Found {len(results)} unique apps for '{kw}'. Filtering now...")
            leads_in_this_kw = 0
            
            for r in results:
                while state["status"] == "PAUSED": time.sleep(1)
                if state["status"] == "IDLE" or state["total_leads"] >= 200: break
                
                app_id = r['appId']
                if app_id in state["scraped_apps"]: continue
                state["scraped_apps"].add(app_id)
                
                try: d = app(app_id)
                except: continue
                
                dev_name = str(d.get('developer', '')).lower()
                if any(g in dev_name for g in gov_keywords): continue 
                
                rating = float(d.get('score', 0))
                installs = int(d.get('minInstalls', 0))
                email = str(d.get('developerEmail', '')).strip().lower()
                
                # --- DUPLICATE CHECK ---
                if email in state["existing_emails"]:
                    print(f"♻️ Duplicate Skipped: {email}")
                    continue
                
                if rating > 0 and rating <= max_rating and installs <= max_installs and email:
                    bot.send_message(state["chat_id"], f"✨ Qualified Lead Found: *{d['title']}*\nGenerating Email...", parse_mode="Markdown")
                    
                    subject, body = generate_email_content(d['title'], d['developer'], rating, installs, d.get('description', ''), contact_info, email_prompt)
                    
                    # Save to Sheet
                    requests.post(SHEET_WEB_APP_URL, json={
                        "action": "save_lead", "app_name": d['title'], "dev_name": d['developer'],
                        "email": email, "subject": subject, "body": body, "installs": installs,
                        "rating": rating, "link": d['url'], "category": d['genre'], 
                        "website": d.get('developerWebsite', ''), "updated": d.get('updated', '')
                    })
                    
                    # Add to local database to prevent duplicate in same run
                    state["existing_emails"].add(email)
                    
                    # Send Email
                    mail_res = requests.post(EMAIL_WEB_APP_URL, json={"action": "send_email", "to": email, "subject": subject, "body": body})
                    
                    if mail_res.text == "Success":
                        state["total_leads"] += 1
                        leads_in_this_kw += 1
                        bot.send_message(state["chat_id"], f"✅ Lead #{state['total_leads']} Saved & Email Sent to: {email}")
                        
                        delay = random.randint(60, 120)
                        bot.send_message(state["chat_id"], f"⏳ Waiting {delay}s before next lead...")
                        for _ in range(delay):
                            if state["status"] != "RUNNING": break
                            time.sleep(1)
            
            if leads_in_this_kw < 3:
                bot.send_message(state["chat_id"], f"⚠️ Only got {leads_in_this_kw} leads from '{kw}'. Moving to next keyword to find more.")
            else:
                bot.send_message(state["chat_id"], f"✅ Finished '{kw}'. Got {leads_in_this_kw} solid leads.")
                
        except Exception as e: 
            bot.send_message(state["chat_id"], f"❌ Error on keyword '{kw}': {e}")
        
        if state["status"] == "RUNNING":
            state["current_kw_index"] += 1

    if state["status"] == "RUNNING":
        bot.send_message(state["chat_id"], f"🎉 Automation Finished! Total {state['total_leads']} emails sent.", reply_markup=get_keyboard())
        state["status"] = "IDLE"

def start_engine():
    global state
    try:
        bot.send_message(state["chat_id"], "🔄 Fetching settings and checking database for duplicates...")
        
        # Fetch Settings
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}).json()
        max_installs = int(str(res['max_installs']).replace(',', '').strip())
        max_rating = float(str(res['max_rating']).strip())
        
        # Fetch Existing Emails (Duplicate Database)
        db_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_existing_emails"}).json()
        state["existing_emails"] = set(db_res)
        bot.send_message(state["chat_id"], f"📚 Loaded {len(state['existing_emails'])} existing emails to prevent duplicates.")
        
        if not state["keywords"]: 
            bot.send_message(state["chat_id"], "🧠 AI is generating keywords...")
            chat = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": f"{res['keyword_prompt']} Niche: {res['niche']}. Give me 200 unique broad keywords separated by commas."}],
                model="llama-3.1-8b-instant",
            )
            raw_kws = chat.choices[0].message.content.split(',')
            cleaned_kws = []
            for k in raw_kws:
                k = re.sub(r'^\d+[\.\)]?\s*', '', k).strip() 
                if len(k) > 3: cleaned_kws.append(k)
                
            state["keywords"] = cleaned_kws
            state["current_kw_index"] = 0
            state["total_leads"] = 0
            state["scraped_apps"] = set()
            bot.send_message(state["chat_id"], f"✅ Generated {len(state['keywords'])} clean keywords!")
            
        threading.Thread(target=engine_thread, args=(max_installs, max_rating, res['contact_info'], res['email_prompt'])).start()
    except Exception as e:
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], f"❌ System Error: {e}", reply_markup=get_keyboard())

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

# --- BOT COMMANDS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    global state
    state["chat_id"] = message.chat.id
    bot.reply_to(message, "👋 Welcome Boss! What would you like to do?", reply_markup=get_keyboard())

@bot.message_handler(func=lambda msg: True)
def handle_messages(message):
    global state
    text = message.text
    state["chat_id"] = message.chat.id

    if text == "🚀 Start Automation":
        if state["status"] in ["IDLE", "SCHEDULED"]:
            state["status"] = "RUNNING"
            bot.reply_to(message, "🚀 Starting Automation...", reply_markup=get_keyboard())
            start_engine()
            
    elif text == "🛑 Stop Automation":
        if state["status"] == "RUNNING":
            state["status"] = "PAUSED"
            bot.reply_to(message, "🛑 Automation Paused. What next?", reply_markup=get_keyboard())
            
    elif text == "▶️ Resume":
        if state["status"] == "PAUSED":
            state["status"] = "RUNNING"
            bot.reply_to(message, "▶️ Resuming from where we left off...", reply_markup=get_keyboard())
            
    elif text == "⏹️ Permanent Stop":
        state["status"] = "IDLE"
        state["keywords"] = [] 
        bot.reply_to(message, "⏹️ Automation Permanently Stopped and Reset.", reply_markup=get_keyboard())
        
    elif text == "📅 Schedule Automation":
        bot.reply_to(message, "⏰ Please send the time. You can use AM/PM (e.g., 02:30 PM) or 24-hour format (e.g., 14:30).")
        
    elif text == "❌ Cancel Schedule":
        state["status"] = "IDLE"
        state["scheduled_time"] = None
        bot.reply_to(message, "❌ Schedule Cancelled.", reply_markup=get_keyboard())
        
    else:
        parsed_time = parse_time(text)
        if parsed_time:
            state["status"] = "SCHEDULED"
            state["scheduled_time"] = parsed_time
            bot.reply_to(message, f"✅ Scheduled successfully! It will run everyday at {text} (Bangladesh Time).", reply_markup=get_keyboard())
        elif state["status"] not in ["RUNNING", "PAUSED"]:
            bot.reply_to(message, "❌ Invalid command or time format. Please use '02:30 PM' or '14:30'.")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    threading.Thread(target=run_scheduler).start()
    
    while True:
        try:
            print("🤖 Bot connecting to Telegram...")
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"⚠️ Connection Error (Retrying in 5 seconds): {e}")
            time.sleep(5)
