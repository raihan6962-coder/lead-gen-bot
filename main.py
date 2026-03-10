import requests, telebot, time, random, os, threading
from flask import Flask
from groq import Groq
from google_play_scraper import search, app

# --- FLASK WEB SERVER (Render er jonno baddhotamulok) ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot is Alive and Running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# --- CONFIG ---
SHEET_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzI5eCCU_Gci6M0jFr5I_Ph48CqUvvP4_nkpngWtjFafVSr_i75yqKX37ZMG4qwG0_V/exec"
EMAIL_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbwrwh2vi677K1KyI6XkDObTCflb2yqtJp93kIWYxOj3uoUUt0PsskH5fSkgGTq1-jHW9A/exec"
BOT_TOKEN = "8742208395:AAHx834VKnPo2zV8j2uozOMxDr4LSVsGLPA"
GROQ_API_KEY = "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"

bot = telebot.TeleBot(BOT_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)
CHAT_ID = None 

# --- AI Email Generator ---
def generate_email_content(app_name, dev_name, rating, installs, description, contact_info, email_prompt):
    if not dev_name or len(dev_name) > 20: dev_name = "Developer"
    prompt = f"""
    {email_prompt}
    
    App Details:
    - App Name: {app_name}
    - Developer: {dev_name}
    - Rating: {rating}
    - Installs: {installs}
    - Description: {description[:200]}
    
    Contact Info to include: {contact_info}
    
    Format EXACTLY like this:
    SUBJECT: [Subject Line]
    BODY: [Email Body]
    """
    try:
        chat = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
        )
        content = chat.choices[0].message.content
        subject = content.split("SUBJECT:")[1].split("BODY:")[0].strip()
        body = content.split("BODY:")[1].strip()
        return subject, body
    except Exception as e:
        print(f"AI Error: {e}")
        return f"Collaboration Proposal for {app_name}", "Please contact us for collaboration."

# --- Scraper & Outreach Engine ---
def scrape_and_filter(keywords, max_installs, max_rating, contact_info, email_prompt):
    total_leads = 0
    scraped_apps = set()

    for kw in keywords:
        if total_leads >= 200: break
        try:
            results = search(kw, lang='en', country='us', n_hits=50)
            for r in results:
                if total_leads >= 200: break
                if r['appId'] in scraped_apps: continue
                scraped_apps.add(r['appId'])
                
                try:
                    d = app(r['appId'])
                except: continue
                
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
                        total_leads += 1
                        bot.send_message(CHAT_ID, f"✅ Lead #{total_leads} Sent: {d['title']}")
                        time.sleep(random.randint(60, 120))
            time.sleep(2)
        except Exception as e: 
            print(f"Error: {e}")
            continue
    bot.send_message(CHAT_ID, f"🎉 Task Finished! Total {total_leads} emails sent.")

@bot.message_handler(commands=['start'])
def start_process(message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    bot.reply_to(message, "🚀 Starting Lead Generation...")
    try:
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}).json()
        max_installs = int(str(res['max_installs']).replace(',', '').strip())
        max_rating = float(str(res['max_rating']).strip())
        
        chat = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": f"{res['keyword_prompt']} Niche: {res['niche']}. Give me 200 unique broad keywords."}],
            model="llama-3.1-8b-instant",
        )
        keywords = [k.strip() for k in chat.choices[0].message.content.split(',') if len(k.strip()) > 3]
        
        scrape_and_filter(keywords, max_installs, max_rating, res['contact_info'], res['email_prompt'])
    except Exception as e:
        bot.send_message(CHAT_ID, f"❌ System Error: {e}")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # Flask server ke alada thread e chalu kora
    threading.Thread(target=run_web).start()
    # Bot ke chalu kora
    print("🤖 Bot running...")
    bot.polling(none_stop=True)
