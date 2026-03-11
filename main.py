import requests, telebot, time, random, os, threading, schedule
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

bot = telebot.TeleBot(BOT_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# --- STATE MANAGEMENT ---
state = {
    "status": "IDLE", # IDLE, RUNNING, PAUSED, WAITING_TIME
    "keywords": [],
    "current_kw_index": 0,
    "total_leads": 0,
    "scraped_apps": set(),
    "chat_id": None
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
    elif state["status"] == "WAITING_TIME":
        markup.add(KeyboardButton("❌ Cancel Schedule"))
    return markup

def get_schedule_options():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("✅ Everyday at this time"), KeyboardButton("❌ Cancel Schedule"))
    return markup

# --- AI EMAIL GENERATOR (HTML FORMAT) ---
def generate_email_content(app_name, dev_name, rating, installs, description, contact_info, email_prompt):
    if not dev_name or len(dev_name) > 20: dev_name = "Developer"
    prompt = f"""
    {email_prompt}
    
    App Details: App Name: {app_name}, Developer: {dev_name}, Rating: {rating}, Installs: {installs}
    
    Format EXACTLY like this (Use HTML tags like <br> for line breaks, DO NOT use markdown **):
    SUBJECT: [Subject Line]
    BODY: [HTML Email Body]
    """
    try:
        chat = groq_client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model="llama-3.1-8b-instant")
        content = chat.choices[0].message.content
        subject = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
        body = content.split("BODY:")[1].strip()
        
        # Add Centered Unsubscribe Button
        unsubscribe_html = f"<br><br><hr><div style='text-align: center;'><a href='mailto:your-email@gmail.com?subject=Unsubscribe' style='color: #888; font-size: 12px; text-decoration: none;'>Unsubscribe from future emails</a></div>"
        body += unsubscribe_html
        return subject, body
    except:
        return f"Collaboration for {app_name}", f"Hi {dev_name},<br><br>Let's collaborate.<br><br>{contact_info}"

# --- CORE ENGINE (Scrape, Filter, Send, Pause/Resume) ---
def engine_thread(max_installs, max_rating, contact_info, email_prompt):
    global state
    gov_keywords = ['gov', 'government', 'ministry', 'department', 'state', 'council', 'national', 'authority']
    
    while state["current_kw_index"] < len(state["keywords"]):
        # Pause Logic
        while state["status"] == "PAUSED": time.sleep(1)
        if state["status"] == "IDLE": break # Permanent Stop
        if state["total_leads"] >= 200: break
        
        kw = state["keywords"][state["current_kw_index"]]
        bot.send_message(state["chat_id"], f"🔍 Deep Searching Keyword: {kw}")
        
        try:
            results = search(kw, lang='en', country='us', n_hits=150) # Max apps per keyword
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
                # GOV APP FILTER
                if any(g in dev_name for g in gov_keywords):
                    print(f"🚫 Skipped Gov App: {d['title']}")
                    continue
                
                rating = float(d.get('score', 0))
                installs = int(d.get('minInstalls', 0))
                email = d.get('developerEmail')
                
                if rating > 0 and rating <= max_rating and installs <= max_installs and email:
                    subject, body = generate_email_content(d['title'], d['developer'], rating, installs, d.get('description', ''), contact_info, email_prompt)
                    
                    requests.post(SHEET_WEB_APP_URL, json={
                        "action": "save_lead", "app_name": d['title'], "dev_name": d['developer'],
                        "email": email, "subject": subject, "body": body, "installs": installs,
                        "rating": rating, "link": d['url'], "category": d['genre'], 
                        "website": d.get('developerWebsite', ''), "updated": d.get('updated', '')
                    })
                    
                    mail_res = requests.post(EMAIL_WEB_APP_URL, json={"action": "send_email", "to": email, "subject": subject, "body": body})
                    
                    if mail_res.text == "Success":
                        state["total_leads"] += 1
                        leads_in_this_kw += 1
                        bot.send_message(state["chat_id"], f"✅ Lead #{state['total_leads']} Sent: {d['title']}")
                        
                        # Random Delay 1-2 Mins
                        delay = random.randint(60, 120)
                        bot.send_message(state["chat_id"], f"⏳ Waiting {delay}s...")
                        for _ in range(delay):
                            if state["status"] != "RUNNING": break
                            time.sleep(1)
            
            if leads_in_this_kw < 3:
                bot.send_message(state["chat_id"], f"⚠️ Only found {leads_in_this_kw} leads for '{kw}'. Moving to next keyword.")
            else:
                bot.send_message(state["chat_id"], f"✅ Finished '{kw}'. Found {leads_in_this_kw} leads.")
                
        except Exception as e: 
            print(f"Error: {e}")
        
        if state["status"] == "RUNNING":
            state["current_kw_index"] += 1

    if state["status"] == "RUNNING":
        bot.send_message(state["chat_id"], f"🎉 Automation Finished! Total {state['total_leads']} emails sent.", reply_markup=get_keyboard())
        state["status"] = "IDLE"

def start_engine():
    global state
    try:
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}).json()
        max_installs = int(str(res['max_installs']).replace(',', '').strip())
        max_rating = float(str(res['max_rating']).strip())
        
        if not state["keywords"]: # Jodi resume na hoy
            chat = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": f"{res['keyword_prompt']} Niche: {res['niche']}. Give me 200 unique broad keywords."}],
                model="llama-3.1-8b-instant",
            )
            state["keywords"] = [k.strip() for k in chat.choices[0].message.content.split(',') if len(k.strip()) > 3]
            state["current_kw_index"] = 0
            state["total_leads"] = 0
            state["scraped_apps"] = set()
            
        threading.Thread(target=engine_thread, args=(max_installs, max_rating, res['contact_info'], res['email_prompt'])).start()
    except Exception as e:
        state["status"] = "IDLE"
        bot.send_message(state["chat_id"], f"❌ System Error: {e}", reply_markup=get_keyboard())

# --- SCHEDULER THREAD ---
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

def scheduled_job():
    global state
    if state["status"] == "IDLE":
        state["status"] = "RUNNING"
        bot.send_message(state["chat_id"], "⏰ Scheduled Automation Started!", reply_markup=get_keyboard())
        start_engine()

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
        if state["status"] == "IDLE":
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
        state["keywords"] = [] # Reset everything
        bot.reply_to(message, "⏹️ Automation Permanently Stopped and Reset.", reply_markup=get_keyboard())
        
    elif text == "📅 Schedule Automation":
        state["status"] = "WAITING_TIME"
        bot.reply_to(message, "⏰ Please send the time in HH:MM format (24-hour). Example: 14:30 for 2:30 PM.", reply_markup=get_keyboard())
        
    elif text == "❌ Cancel Schedule":
        state["status"] = "IDLE"
        schedule.clear()
        bot.reply_to(message, "❌ Schedule Cancelled.", reply_markup=get_keyboard())
        
    elif text == "✅ Everyday at this time":
        bot.reply_to(message, f"✅ Scheduled successfully! It will run everyday at the set time.", reply_markup=get_keyboard())
        
    elif state["status"] == "WAITING_TIME":
        try:
            # Validate time format
            time.strptime(text, '%H:%M')
            schedule.every().day.at(text).do(scheduled_job)
            state["status"] = "IDLE"
            bot.reply_to(message, f"Time set to {text}. Choose frequency:", reply_markup=get_schedule_options())
        except ValueError:
            bot.reply_to(message, "❌ Invalid time format. Please use HH:MM (e.g., 14:30).")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    threading.Thread(target=run_scheduler).start()
    print("🤖 Bot running...")
    bot.polling(none_stop=True)

