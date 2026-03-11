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
    return markup

def get_back_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🔙 Back to Main Menu"))
    return markup

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
        if state["status"] == "IDLE": break 
        
        kw = state["keywords"][state["current_kw_index"]]
        bot.send_message(state["chat_id"], f"🔍 Searching Keyword: *{kw}*", parse_mode="Markdown")
        
        try:
            # Deep Search
            results = search(kw, lang='en', country='us', n_hits=200)
            
            for r in results:
                if state["status"] == "IDLE" or state["total_leads"] >= 200: break
                while state["status"] == "PAUSED": time.sleep(1)
                
                app_id = r['appId']
                if app_id in state["scraped_apps"]: continue
                state["scraped_apps"].add(app_id)
                
                # Filter Rating/Install BEFORE fetching full details to save time
                if r['score'] > 0 and r['score'] <= max_rating and r['minInstalls'] <= max_installs:
                    d = app(app_id)
                    email = str(d.get('developerEmail', '')).strip().lower()
                    
                    if email and email not in state["existing_emails"]:
                        # Get Sender
                        senders = requests.post(SHEET_WEB_APP_URL, json={"action": "get_senders"}).json()
                        available = [s for s in senders if int(s['sent']) < int(s['limit'])]
                        if not available: 
                            bot.send_message(state["chat_id"], "⚠️ All senders limit reached!")
                            state["status"] = "PAUSED"
                            break
                        
                        current_sender = available[0]
                        subject, body = generate_email_content(d['title'], d['developer'], d['score'], d['minInstalls'], d.get('description', ''), contact_info, email_prompt, current_sender['email'])
                        
                        # Save & Send
                        requests.post(SHEET_WEB_APP_URL, json={"action": "save_lead", "app_name": d['title'], "dev_name": d['developer'], "email": email, "subject": subject, "body": body, "installs": d['minInstalls'], "rating": d['score'], "link": d['url'], "category": d['genre'], "website": d.get('developerWebsite', ''), "updated": d.get('updated', '')})
                        requests.post(current_sender['url'], json={"action": "send_email", "to": email, "subject": subject, "body": body})
                        requests.post(SHEET_WEB_APP_URL, json={"action": "increment_sender", "email": current_sender['email']})
                        
                        state["existing_emails"].add(email)
                        state["total_leads"] += 1
                        bot.send_message(state["chat_id"], f"✅ Lead #{state['total_leads']} Sent: {d['title']}")
                        time.sleep(random.randint(60, 120))
            
            state["current_kw_index"] += 1
        except Exception as e: print(f"Error: {e}")

    bot.send_message(state["chat_id"], "🎉 Finished!", reply_markup=get_keyboard())
    state["status"] = "IDLE"

# --- MAIN ---
if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    threading.Thread(target=run_scheduler).start()
    bot.polling(none_stop=True)
