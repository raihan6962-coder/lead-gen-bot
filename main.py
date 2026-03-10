!pip install requests telebot groq google-play-scraper

import requests, telebot, time, random
from groq import Groq
from google_play_scraper import search, app

# --- CONFIG ---
SHEET_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzI5eCCU_Gci6M0jFr5I_Ph48CqUvvP4_nkpngWtjFafVSr_i75yqKX37ZMG4qwG0_V/exec"
EMAIL_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbwrwh2vi677K1KyI6XkDObTCflb2yqtJp93kIWYxOj3uoUUt0PsskH5fSkgGTq1-jHW9A/exec"
BOT_TOKEN = "8742208395:AAHx834VKnPo2zV8j2uozOMxDr4LSVsGLPA"
GROQ_API_KEY = "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"

bot = telebot.TeleBot(BOT_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)
CHAT_ID = None 

def generate_email_content(app_name, dev_name, rating, installs, description, contact_info, email_prompt):
    if not dev_name or len(dev_name) > 20: dev_name = "Developer"
    
    prompt = f"""
    {email_prompt}
    
    Here is the App Data you must use:
    - App Name: {app_name}
    - Developer Name: {dev_name}
    - Rating: {rating}
    - Installs: {installs}
    - Description: {description[:300]}
    
    Signature to use at the end:
    {contact_info}
    
    Format EXACTLY like this:
    SUBJECT: [Your Subject]
    BODY: [Your Body]
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
        return f"Collaboration for {app_name}", content

def scrape_and_filter(keywords, max_installs, max_rating, contact_info, email_prompt):
    total_leads = 0
    scraped_apps = set()

    for kw in keywords:
        if total_leads >= 200: break
        bot.send_message(CHAT_ID, f"🔍 Searching Keyword: {kw}")
        print(f"\n--- Searching: {kw} ---")
        
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
                
                # 1. Rating Check
                if rating > 0 and rating <= max_rating:
                    # 2. Install & Email Check
                    if installs <= max_installs and email:
                        print(f"✅ PASSED: {d['title']} (Rating: {rating}, Installs: {installs})")
                        bot.send_message(CHAT_ID, f"✨ Lead Found: {d['title']}. Generating Email...")
                        
                        # 3. AI Email Gen
                        subject, body = generate_email_content(d['title'], d['developer'], rating, installs, d.get('description', ''), contact_info, email_prompt)
                        
                        # 4. Save to Sheet
                        requests.post(SHEET_WEB_APP_URL, json={
                            "action": "save_lead", "app_name": d['title'], "dev_name": d['developer'],
                            "email": email, "subject": subject, "body": body, "installs": installs,
                            "rating": rating, "link": d['url'], "category": d['genre'], 
                            "website": d.get('developerWebsite', ''), "updated": d.get('updated', '')
                        })
                        
                        # 5. Send Email
                        mail_res = requests.post(EMAIL_WEB_APP_URL, json={"action": "send_email", "to": email, "subject": subject, "body": body})
                        
                        if mail_res.text == "Success":
                            total_leads += 1
                            bot.send_message(CHAT_ID, f"📧 Email #{total_leads} Sent to: {email}")
                            
                            # 6. Wait 1-2 Mins
                            delay = random.randint(60, 120)
                            print(f"⏳ Waiting {delay} seconds...")
                            time.sleep(delay)
                        else:
                            print(f"❌ Email Failed: {mail_res.text}")
                    else:
                        print(f"❌ Rejected (Installs/Email): {d['title']} (Installs: {installs}, Email: {bool(email)})")
                else:
                    print(f"❌ Rejected (Rating): {d['title']} (Rating: {rating})")
                    
        except Exception as e: 
            print(f"Search Error: {e}")
            continue

    bot.send_message(CHAT_ID, f"🎉 Task Finished! Total {total_leads} emails sent.")

@bot.message_handler(commands=['start'])
def start_process(message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    bot.reply_to(message, "🚀 Starting System...")
    
    try:
        res = requests.post(SHEET_WEB_APP_URL, json={"action": "get_settings"}).json()
        
        # Data clean kora jate crash na kore
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

print("🤖 Bot running...")
bot.polling(none_stop=True)
