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
EMAIL_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbwrwh2vi677K1KyI6XkDObTCflb2yqtJp93kIWYxOj3uoUUt0PsskH5fSkgGTq1-jHW9A/exec"
BOT_TOKEN = "8709829378:AAEJJQ8jm_oTyAcGenBrIfLi4KYHRVcSJbo"
GROQ_API_KEY = "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"

# SENDER EMAIL (Only for Unsubscribe link)
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

# --- AI EMAIL GENERATOR ---
def generate_email_content(app_name, dev_name, rating, installs, description, contact_info, email_prompt, sender_email):
    if not dev_name or len(dev_name) > 25: dev_name = "Team"
    
    prompt = f"""
    {email_prompt}
    
    App Name: {app_name}
    Developer: {dev_name}
    Rating: {rating}
    Installs: {installs}
    Description: {description[:200]}
    
    RULES:
    1. SUBJECT: Mention app '{app_name}'.
    2. BODY: Professional paragraphs, NO markdown like **bold**.
    3. Use <br> for new lines.
    4. Signature: {contact_info}
    
    Format:
    SUBJECT: [Subject]
    BODY: [HTML Body]
    """
    try:
        chat = groq_client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model="llama-3.1-8b-instant")
        content = chat.choices[0].message.content
        subject = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
        body = content.split("BODY:")[1].strip().replace('**', '').replace('\n', '<br>')
        
        # Centered Unsubscribe
        unsubscribe = f"<br><br><hr><div style='text-align: center;'><a href='mailto:{sender_email}?subject=Unsubscribe' style='color: #999; font-size: 12px;'>Unsubscribe from future emails</a></div>"
        return subject, body + unsubscribe
    except:
        return f"Collaboration for {app_name}", f"Hi {dev_name},<br><br>Let's collaborate."

# --- CORE ENGINE (IMPROVED SCRAPING) ---
def engine_thread(max_installs, max_rating, contact_info, email_prompt):
    global state
    gov_keywords = ['gov', 'government', 'ministry', 'department', 'state', 'national', 'authority', 'police', 'municipal']
    
    while state["current_kw_index"] < len(state["keywords"]):
        if state["status"] == "IDLE": break
        while state["status"] == "PAUSED": time.sleep(1)
        
        kw = state["keywords"][state["current_kw_index"]]
        bot.send_message(state["chat_id"], f"🔍 **Searching:** {kw}...", parse_mode="Markdown")
        
        try:
            # 1. Broad Search (Get up to 200 apps per keyword)
            results = search(kw, lang='en', country='us', n_hits=200)
            bot.send_message(state["chat_id"], f"📊 Found {len(results)} apps. Filtering now...")
            
            leads_in_kw = 0
            for r in results:
                if state["status"] == "IDLE" or state["total_leads"] >= 200: break
                while state["status"] == "PAUSED": time.sleep(1)
                
                app_id = r['appId']
                if app_id in state["scraped_apps"]: continue
                state["scraped_apps"].add(app_id)

                # Filter Rating & Install BEFORE calling full app details (FAST)
                if r.get('score', 0) > 0 and r.get('score', 0) <= max_rating and r.get('minInstalls', 0) <= max_installs:
                    
                    try:
                        d = app(app_id) # Call full details only if basic filter passes
                        time.sleep(0.5)
                    except: continue

                    # Gov Check
                    dev_name = str(d.get('developer', '')).lower()
                    if any(g in dev_name for g in gov_keywords): continue

                    email = str(d.get('developerEmail', '')).strip().lower()
                    if email and email not in state["existing_emails"]:
                        
                        # Get Sender Rotation
                        senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}).json()
                        available = [s for s in senders if int(s['sent']) < int(s['limit'])]
                        
                        if not available:
                            bot.send_message(state["chat_id"], "⚠️ All sender limits reached! Automation Paused.")
                            state["status"] = "PAUSED"
                            break
                        
                        current_sender = available[0]
                        bot.send_message(state["chat_id"], f"✨ **Lead Found:** {d['title']}\n📧 Generating Email via: {current_sender['email']}", parse_mode="Markdown")
                        
                        subject, body = generate_email_content(d['title'], d['developer'], d['score'], d['minInstalls'], d.get('description', ''), contact_info, email_prompt, current_sender['email'])
                        
                        # Save & Send
                        requests.post(SHEET_WEB_APP_URL, json={"action": "save_lead", "app_name": d['title'], "dev_name": d['developer'], "email": email, "subject": subject, "body": body, "installs": d['minInstalls'], "rating": d['score'], "link": d['url'], "category": d['genre'], "website": d.get('developerWebsite', ''), "updated": d.get('updated', '')})
                        
                        mail_res = requests.post(current_sender['url'], json={"action": "send_email", "to": email, "subject": subject, "body": body})
                        
                        if mail_res.text == "Success":
                            requests.post(SHEET_WEB_APP_URL, json={"action": "increment_sender", "email": current_sender['email']})
                            state["total_leads"] += 1
                            leads_in_kw += 1
                            state["existing_emails"].add(email)
                            bot.send_message(state["chat_id"], f"✅ Sent Lead #{state['total_leads']} to {email}")
                            
                            # Delay
                            delay = random.randint(60, 120)
                            bot.send_message(state["chat_id"], f"⏳ Waiting {delay}s...")
                            for _ in range(delay):
                                if state["status"] != "RUNNING": break
                                time.sleep(1)

            bot.send_message(state["chat_id"], f"🏁 Keyword '{kw}' done. Leads found: {leads_in_kw}")
            
        except Exception as e: print(f"Error: {e}")
        state["current_kw_index"] += 1

    bot.send_message(state["chat_id"], f"🎉 All Keywords Processed! Total: {state['total_leads']} Leads.", reply_markup=get_keyboard())
    state["status"] = "IDLE"

def start_engine():
    global state
    try:
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}).json()
        db_res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_existing_emails"}).json()
        state["existing_emails"] = set(db_res)

        if not state["keywords"]:
            bot.send_message(state["chat_id"], "🧠 Generating Keywords...")
            chat = groq_client.chat.completions.create(messages=[{"role": "user", "content": f"{res['keyword_prompt']} Niche: {res['niche']}. Give me 200 keywords separated by commas."}], model="llama-3.1-8b-instant")
            kws = chat.choices[0].message.content.split(',')
            state["keywords"] = [re.sub(r'^\d+[\.\)]?\s*', '', k).strip() for k in kws if len(k.strip()) > 3]
            state["current_kw_index"] = 0
            state["total_leads"] = 0

        threading.Thread(target=engine_thread, args=(int(str(res['max_installs']).replace(',','')), float(res['max_rating']), res['contact_info'], res['email_prompt'])).start()
    except Exception as e:
        bot.send_message(state["chat_id"], f"❌ Start Error: {e}", reply_markup=get_keyboard())

# --- BOT COMMANDS ---
@bot.message_handler(commands=['start'])
def start(message):
    state["chat_id"] = message.chat.id
    bot.reply_to(message, "👋 System Ready. Use buttons to control.", reply_markup=get_keyboard())

@bot.message_handler(func=lambda m: True)
def handle_all(message):
    global state
    state["chat_id"] = message.chat.id
    text = message.text

    if text == "🚀 Start Automation":
        state["status"] = "RUNNING"
        bot.reply_to(message, "🚀 Starting Engine...", reply_markup=get_keyboard())
        start_engine()
    elif text == "🛑 Stop Automation":
        state["status"] = "PAUSED"
        bot.reply_to(message, "🛑 Paused.", reply_markup=get_keyboard())
    elif text == "▶️ Resume":
        state["status"] = "RUNNING"
        bot.reply_to(message, "▶️ Resuming...", reply_markup=get_keyboard())
    elif text == "⏹️ Permanent Stop":
        state["status"] = "IDLE"
        state["keywords"] = []
        bot.reply_to(message, "⏹️ Reset.", reply_markup=get_keyboard())
    elif text == "📧 Manage Senders":
        # Senders list callback query handler part... (already robust)
        pass 
    elif "script.google.com" in text and state["status"] == "WAITING_SENDER_URL":
        # Handle sender addition...
        pass
    # ... Other handlers (Schedule, Spam Test) remain same but with 'get_back_keyboard'

if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    bot.polling(none_stop=True)
