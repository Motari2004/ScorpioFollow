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
# Use eventlet on production for better SocketIO performance
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet' if IS_PRODUCTION else 'threading')

# --- Logging Setup ---
# Simplified to avoid 'user' KeyError during system logs
logging.basicConfig(
    level=config.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
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

    def web_log(self, message):
        """Prints to terminal and sends to web UI."""
        print(f"[{self.username}] {message}")
        self.socketio.emit('bot_update', {'msg': message, 'count': self.followed_today_count})

    async def start(self, playwright):
        headless_mode = True if IS_PRODUCTION else config.HEADLESS_MODE
        self.web_log(f"🚀 STARTING: Browser (Headless={headless_mode})")
        
        # Args: Disable QUIC to prevent network timeout errors on Render
        self.browser = await playwright.chromium.launch(
            headless=headless_mode,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-quic"]
        )
        
        self.context = await self.browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        self.context.set_default_navigation_timeout(90000)
        self.context.set_default_timeout(90000)

        self.page = await self.context.new_page()
        
        # Block heavy media but keep structure (Save bandwidth)
        async def intercept(route):
            if route.request.resource_type in ["media", "font"]: await route.abort()
            else: await route.continue_()
        await self.page.route("**/*", intercept)

        # --- SESSION COOKIE MANAGEMENT ---
        env_cookies = os.environ.get('SESSION_COOKIES')
        if env_cookies:
            try:
                await self.context.add_cookies(json.loads(env_cookies.strip()))
                self.web_log("✅ Cookies loaded from SESSION_COOKIES Env.")
            except Exception as e:
                self.web_log(f"⚠️ Env Cookie Load Failed: {e}")
        elif os.path.exists(self.cookie_file):
            with open(self.cookie_file, 'r') as f:
                await self.context.add_cookies(json.load(f))
            self.web_log("✅ Cookies loaded from file.")

        return True

    async def check_if_logged_in(self):
        markers = ['svg[aria-label="Home"]', 'img[alt*="profile picture"]', 'span:has-text("Search")']
        for i in range(15):
            for selector in markers:
                try:
                    if await self.page.locator(selector).first.is_visible():
                        return True
                except: continue
            await asyncio.sleep(2)
        return False

    async def login(self):
        self.web_log("NAVIGATING: Opening Instagram...")
        try:
            await self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
            if await self.check_if_logged_in():
                self.web_log("✨ Session verified.")
                return True
            
            self.web_log("🔑 Manual login required...")
            await self.page.goto("https://www.instagram.com/accounts/login/")
            await asyncio.sleep(5)
            await self.page.fill('input[name="username"]', self.username)
            await self.page.fill('input[name="password"]', self.password)
            await self.page.click('button[type="submit"]')
            await asyncio.sleep(12)
            
            success = await self.check_if_logged_in()
            if success:
                cookies = await self.context.cookies()
                with open(self.cookie_file, 'w') as f:
                    json.dump(cookies, f)
            return success
        except Exception as e:
            self.web_log(f"❌ Login failed: {str(e)}")
        return False

    async def search_hashtag(self, hashtag):
        self.web_log(f"🔎 SEARCHING: #{hashtag}")
        try:
            await self.page.goto(f"https://www.instagram.com/explore/tags/{hashtag}/", wait_until="domcontentloaded")
            await self.page.wait_for_selector('div._aagu', timeout=40000)
            await asyncio.sleep(5)
            await self.page.mouse.wheel(0, 1000) # Scroll to load more
            await asyncio.sleep(3)
            links = await self.page.locator('a:has(div._aagu)').evaluate_all(
                "els => els.map(el => el.getAttribute('href'))"
            )
            return [f"https://www.instagram.com{l}" for l in links if "/p/" in l][:10]
        except Exception:
            return []

    async def process_post(self, post_url, target):
        try:
            self.web_log(f"📸 Opening Post: {post_url.split('/')[-2]}")
            await self.page.goto(post_url, wait_until="domcontentloaded", timeout=60000)
            
            # --- POLLING WAIT LOGIC (Checks every 10s for 60s total) ---
            header_clicked = False
            for attempt in range(1, 7):
                self.web_log(f"⏳ Settling content... Attempt {attempt}/6")
                try:
                    # Target the username link in the post header
                    user_trigger = self.page.locator('span._ap3a._aaco._aacw._aacx._aad7._aade').last
                    if await user_trigger.is_visible():
                        self.web_log("🎯 Element ready! Clicking profile...")
                        await user_trigger.click()
                        header_clicked = True
                        break
                except: pass
                
                if attempt < 6: await asyncio.sleep(10)

            if not header_clicked:
                self.web_log("⚠️ Polling timed out (60s). Header not found.")
                return

            # --- Profile Page Logic ---
            self.web_log("👤 Profile opened. Waiting for follower link...")
            await self.page.wait_for_selector('a[href*="/followers/"]', timeout=30000)
            
            # 5-second mandatory buffer as requested
            await asyncio.sleep(5)
            
            await self.page.locator('a[href*="/followers/"]').first.click()
            self.web_log("👥 Opening Followers Modal...")
            await self.page.wait_for_selector('div[role="dialog"]', timeout=30000)
            await asyncio.sleep(5)
            
            # --- Follow Loop ---
            while self.followed_today_count < target:
                if self.session_batch_count >= 10:
                    self.web_log("⏳ Batch limit reached. Resting 60s...")
                    await asyncio.sleep(60)
                    self.session_batch_count = 0

                modal = self.page.locator('div[role="dialog"]')
                follow_btn = modal.get_by_role("button", name="Follow", exact=True).first
                
                if await follow_btn.is_visible():
                    await follow_btn.click()
                    self.followed_today_count += 1
                    self.session_batch_count += 1
                    self.web_log(f"✅ Followed ({self.followed_today_count}/{target})")
                    await asyncio.sleep(random.uniform(4, 9))
                else:
                    # Scroll modal to load more
                    await self.page.mouse.wheel(0, 800)
                    await asyncio.sleep(5)
                    if await modal.get_by_role("button", name="Follow", exact=True).count() == 0: 
                        break
            
            await self.page.keyboard.press("Escape")
        except Exception as e:
            self.web_log(f"⚠️ Skip: {str(e)}")

    async def close(self):
        if self.browser: await self.browser.close()

# --- Worker Function ---
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
            socketio.emit('bot_update', {'msg': '🏁 Sequence Completed.', 'count': target_count})
    loop.run_until_complete(task())

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('start_request')
def handle_start(data):
    target = int(data.get('count', 5))
    threading.Thread(target=run_worker, args=(target,), daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)