import asyncio
import random
import logging
import sys
import os
import json
import threading
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from playwright.async_api import async_playwright
import config

# --- Environment Detection ---
IS_PRODUCTION = os.environ.get('RENDER') is not None

app = Flask(__name__)
app.config['SECRET_KEY'] = 'insta-secret-2026'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet' if IS_PRODUCTION else 'threading')

# --- Logging Setup ---
logging.basicConfig(
    level=config.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - [%(user)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

class InstagramBot:
    def __init__(self, user_data, socketio_instance):
        self.username = user_data['username']
        self.password = user_data['password']
        self.cookie_file = f"cookies_{self.username}.json"
        self.followed_today_count = 0
        self.session_batch_count = 0 
        self.browser = None
        self.context = None
        self.page = None
        self.socketio = socketio_instance
        self.logger = logging.LoggerAdapter(logging.getLogger(), {'user': self.username})

    def web_log(self, message):
        self.logger.info(message)
        self.socketio.emit('bot_update', {'msg': message, 'count': self.followed_today_count})

    async def start(self, playwright):
        headless_mode = True if IS_PRODUCTION else config.HEADLESS_MODE
        self.web_log(f"🚀 STARTING: Browser (Headless={headless_mode})")
        
        self.browser = await playwright.chromium.launch(
            headless=headless_mode,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        
        self.context = await self.browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        self.context.set_default_navigation_timeout(90000)
        self.context.set_default_timeout(90000)

        # OPTIONAL: Block videos only to save bandwidth but keep page structure
        self.page = await self.context.new_page()
        async def intercept(route):
            if route.request.resource_type in ["media"]: await route.abort()
            else: await route.continue_()
        await self.page.route("**/*", intercept)

        # Cookie Management
        env_cookies = os.environ.get('SESSION_COOKIES')
        if env_cookies:
            try:
                await self.context.add_cookies(json.loads(env_cookies.strip()))
                self.web_log("✅ Cookies loaded from Env.")
            except: pass
        elif os.path.exists(self.cookie_file):
            with open(self.cookie_file, 'r') as f:
                await self.context.add_cookies(json.load(f))
            self.web_log("✅ Cookies loaded from file.")

        return True

    async def check_if_logged_in(self):
        markers = ['svg[aria-label="Home"]', 'img[alt*="profile picture"]', 'span:has-text("Search")']
        for i in range(30):
            self.web_log(f"🔍 Checking login markers ({i+1}/30)...")
            for selector in markers:
                try:
                    if await self.page.locator(selector).first.is_visible():
                        return True
                except: continue
            await asyncio.sleep(3)
        return False

    async def login(self):
        self.web_log("NAVIGATING: Opening Instagram...")
        try:
            await self.page.goto("https://www.instagram.com/", wait_until="commit", timeout=90000)
            if await self.check_if_logged_in():
                self.web_log("✨ Session verified.")
                return True
            
            self.web_log("🔑 Manual login required...")
            await self.page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
            await asyncio.sleep(5)
            await self.page.fill('input[name="username"]', self.username)
            await self.page.fill('input[name="password"]', self.password)
            await self.page.click('button[type="submit"]')
            await asyncio.sleep(15)
            return await self.check_if_logged_in()
        except Exception as e:
            self.web_log(f"❌ Login failed: {str(e)}")
        return False

    async def search_hashtag(self, hashtag):
        self.web_log(f"🔎 SEARCHING: #{hashtag}")
        try:
            await self.page.goto(f"https://www.instagram.com/explore/tags/{hashtag}/", wait_until="domcontentloaded")
            await self.page.wait_for_selector('div._aagu', timeout=70000)
            # Give the grid time to populate
            await asyncio.sleep(5)
            await self.page.mouse.wheel(0, 1500)
            await asyncio.sleep(4)
            links = await self.page.locator('a:has(div._aagu)').evaluate_all(
                "els => els.map(el => el.getAttribute('href'))"
            )
            return [f"https://www.instagram.com{l}" for l in links if "/p/" in l][:10]
        except Exception:
            return []

    async def process_post(self, post_url, target):
        try:
            self.web_log(f"📸 Opening Post: {post_url.split('/')[-2]}")
            await self.page.goto(post_url, wait_until="commit")
            
            # --- MANDATORY 1 MINUTE WAIT ---
            self.web_log("⏳ Waiting 60s for post content to fully settle...")
            await asyncio.sleep(60) 
            
            user_trigger = self.page.locator('span._ap3a._aaco._aacw._aacx._aad7._aade').last
            if await user_trigger.is_visible():
                await user_trigger.click()
                self.web_log("👤 Profile opened. Waiting for follower count...")
                await self.page.wait_for_selector('a[href*="/followers/"]', timeout=30000)
                await asyncio.sleep(5)
                
                await self.page.locator('a[href*="/followers/"]').first.click()
                self.web_log("👥 Opening Followers Modal...")
                await self.page.wait_for_selector('div[role="dialog"]', timeout=30000)
                await asyncio.sleep(5)
                
                while self.followed_today_count < target:
                    if self.session_batch_count >= 10:
                        self.web_log("⏳ Batch limit. Resting 60s...")
                        await asyncio.sleep(60)
                        self.session_batch_count = 0

                    modal = self.page.locator('div[role="dialog"]')
                    follow_btn = modal.get_by_role("button", name="Follow", exact=True).first
                    
                    if await follow_btn.is_visible():
                        await follow_btn.click()
                        self.followed_today_count += 1
                        self.session_batch_count += 1
                        self.web_log(f"✅ Followed ({self.followed_today_count}/{target})")
                        await asyncio.sleep(random.uniform(3, 6))
                    else:
                        await self.page.mouse.wheel(0, 800)
                        await asyncio.sleep(5)
                        if await modal.get_by_role("button", name="Follow", exact=True).count() == 0: break
                
                await self.page.keyboard.press("Escape")
        except Exception as e:
            self.web_log(f"⚠️ Skipping post: {str(e)}")

    async def close(self):
        if self.browser: await self.browser.close()

def run_worker(target_count):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def task():
        user_data = {'username': config.INSTAGRAM_USERNAME, 'password': config.INSTAGRAM_PASSWORD}
        async with async_playwright() as p:
            bot = InstagramBot(user_data, socketio)
            if await bot.start(p):
                if await bot.login():
                    tags = list(config.HASHTAGS_TO_SEARCH)
                    random.shuffle(tags)
                    for tag in tags:
                        if bot.followed_today_count >= target_count: break
                        urls = await bot.search_hashtag(tag)
                        for url in urls:
                            if bot.followed_today_count >= target_count: break
                            await bot.process_post(url, target_count)
                await bot.close()
            socketio.emit('bot_update', {'msg': '🏁 Task Finished.', 'count': target_count})
    loop.run_until_complete(task())

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('start_request')
def handle_start(data):
    target = int(data.get('count', 5))
    threading.Thread(target=run_worker, args=(target,), daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=not IS_PRODUCTION)