import asyncio
import logging
import sqlite3
import sys
import traceback
from datetime import datetime
from typing import Dict, List, Optional
import requests
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ChatMember
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ChatMemberHandler
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError, Conflict, NetworkError
import hashlib
import signal
import os

# إعدادات البوت
BOT_TOKEN = ""
ADMIN_USER_ID = 7139916921
DB_NAME = "news_bot.db"

# إعداد التسجيل المحسن
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class RobustNewsBot:
    def __init__(self):
        self.application = None
        self.bot = None
        self.published_news = set()
        self.is_running = False
        self.news_task = None
        
    def init_database(self):
        """إنشاء قاعدة البيانات والجداول مع حل مشكلات الأعمدة المفقودة"""
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()

            # جدول القنوات والجروبات
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY,
                    chat_id INTEGER UNIQUE,
                    chat_title TEXT,
                    chat_type TEXT,
                    added_by INTEGER,
                    date_added TEXT DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1
                )
            ''')

            # إضافة العمود المفقود إن لم يكن موجوداً
            cursor.execute("PRAGMA table_info(channels)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'date_added' not in columns:
                cursor.execute("ALTER TABLE channels ADD COLUMN date_added TEXT DEFAULT CURRENT_TIMESTAMP")

            # جدول المستخدمين المحظورين
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id INTEGER PRIMARY KEY,
                    banned_by INTEGER,
                    ban_date TEXT DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            ''')

            # إضافة العمود المفقود إن لم يكن موجوداً
            cursor.execute("PRAGMA table_info(banned_users)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'ban_date' not in columns:
                cursor.execute("ALTER TABLE banned_users ADD COLUMN ban_date TEXT DEFAULT CURRENT_TIMESTAMP")

            # باقي الجداول كما هي
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS published_news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    news_hash TEXT UNIQUE,
                    news_text TEXT,
                    publish_date TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS error_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_type TEXT,
                    error_message TEXT,
                    traceback_info TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            conn.commit()
            logger.info("✅ تم إنشاء/تحديث قاعدة البيانات بنجاح")
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء قاعدة البيانات: {e}")
            raise
        finally:
            conn.close()
            
    async def send_error_to_admin(self, error_type: str, error_message: str, traceback_info: str = ""):
        """إرسال الأخطاء للمشرف عبر التليجرام"""
        try:
            if not self.bot:
                return
                
            error_text = (
                f"🚨 **خطأ في البوت** 🚨\n\n"
                f"📝 **نوع الخطأ:** `{error_type}`\n"
                f"💬 **رسالة الخطأ:** `{error_message}`\n"
                f"🕐 **الوقت:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n"
            )
            
            if traceback_info and len(traceback_info) < 2000:
                error_text += f"📋 **التفاصيل:**\n```\n{traceback_info}\n```"
            
            # تقسيم الرسالة إذا كانت طويلة
            if len(error_text) > 4000:
                error_text = error_text[:3900] + "\n\n... (تم اقتطاع الرسالة)"
            
            await self.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=error_text,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"فشل في إرسال الخطأ للمشرف: {e}")
    
    def log_error_to_db(self, error_type: str, error_message: str, traceback_info: str = ""):
        """حفظ الأخطاء في قاعدة البيانات"""
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO error_logs (error_type, error_message, traceback_info, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (error_type, error_message, traceback_info, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"فشل في حفظ الخطأ في قاعدة البيانات: {e}")
    
    async def safe_api_request(self, func, *args, **kwargs):
        """تنفيذ طلبات API بأمان مع إعادة المحاولة"""
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except NetworkError as e:
                if attempt < max_retries - 1:
                    logger.warning(f"خطأ شبكة، إعادة المحاولة {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(retry_delay * (attempt + 1))
                else:
                    raise e
            except TelegramError as e:
                if "flood control" in str(e).lower():
                    wait_time = 30  # انتظار 30 ثانية في حالة flood control
                    logger.warning(f"Flood control detected, waiting {wait_time} seconds")
                    await asyncio.sleep(wait_time)
                    if attempt < max_retries - 1:
                        continue
                raise e

    def get_news_from_api(self) -> List[str]:
        """جلب الأخبار من API مع معالجة محسنة للأخطاء"""
        try:
            # نقطة النهاية الصحيحة لأخبار الجزيرة المباشر
            url = "https://www.aljazeeramubasher.net/graphql"
            
            # بيانات الاستعلام (query) المطلوبة
            query = """
            query ArchipelagoTVBreakingTickerQuery {
              tvBreakingNews {
                text
                createdAt
              }
            }
            """
            
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
                "Referer": "https://www.aljazeeramubasher.net/breaking",
                "Origin": "https://www.aljazeeramubasher.net",
                "wp-site": "ajm"
            }
            
            # إرسال الطلب كـ POST مع الاستعلام
            response = requests.post(
                url,
                json={
                    "operationName": "ArchipelagoTVBreakingTickerQuery",
                    "query": query,
                    "variables": {}
                },
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if "data" not in data or "tvBreakingNews" not in data["data"]:
                    logger.warning("استجابة API غير صحيحة أو لا توجد أخبار حالياً")
                    return []
                
                news_list = []
                
                for item in data["data"]["tvBreakingNews"]:
                    if "text" not in item:
                        continue
                        
                    news_text = item["text"].strip()
                    if not news_text:
                        continue
                        
                    news_hash = hashlib.md5(news_text.encode()).hexdigest()
                    
                    # فحص إذا كان الخبر جديد
                    if news_hash not in self.published_news:
                        news_list.append(news_text)
                        self.published_news.add(news_hash)
                        
                        # حفظ في قاعدة البيانات
                        self.save_published_news(news_hash, news_text)
                
                logger.info(f"تم جلب {len(news_list)} خبر جديد من API")
                return news_list
            else:
                logger.error(f"فشل في جلب الأخبار - كود الاستجابة: {response.status_code}")
                return []
                
        except requests.exceptions.Timeout:
            logger.error("انتهت مهلة انتظار طلب API")
            return []
        except requests.exceptions.ConnectionError:
            logger.error("خطأ في الاتصال بـ API")
            return []
        except Exception as e:
            error_msg = f"خطأ في جلب الأخبار: {str(e)}"
            logger.error(error_msg)
            
            # إرسال الخطأ للمشرف
            asyncio.create_task(
                self.send_error_to_admin("API Error", error_msg, traceback.format_exc())
            )
            return []
    
    def save_published_news(self, news_hash: str, news_text: str):
        """حفظ الخبر المنشور في قاعدة البيانات"""
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO published_news (news_hash, news_text, publish_date)
                VALUES (?, ?, ?)
            ''', (news_hash, news_text, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"خطأ في حفظ الخبر: {e}")
    
    def load_published_news(self):
        """تحميل الأخبار المنشورة مسبقاً من قاعدة البيانات"""
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('SELECT news_hash FROM published_news ORDER BY id DESC LIMIT 100')
            hashes = cursor.fetchall()
            self.published_news = {hash_tuple[0] for hash_tuple in hashes}
            logger.info(f"تم تحميل {len(self.published_news)} خبر منشور مسبقاً")
            conn.close()
        except Exception as e:
            logger.error(f"خطأ في تحميل الأخبار المحفوظة: {e}")
    
    def get_active_channels(self) -> List[int]:
        """الحصول على قائمة القنوات والجروبات النشطة"""
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('SELECT chat_id FROM channels WHERE is_active = 1')
            channels = [row[0] for row in cursor.fetchall()]
            conn.close()
            return channels
        except Exception as e:
            logger.error(f"خطأ في جلب القنوات النشطة: {e}")
            return []
    
    def add_channel(self, chat_id: int, chat_title: str, chat_type: str, added_by: Optional[int]):
        """إضافة قناة أو جروب جديد"""
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO channels 
                (chat_id, chat_title, chat_type, added_by, date_added, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
            ''', (chat_id, chat_title, chat_type, added_by, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            logger.info(f"تم إضافة القناة {chat_title} ({chat_id})")
            return True
        except Exception as e:
            logger.error(f"خطأ في إضافة القناة: {e}")
            return False
    
    async def publish_news_to_channels(self, news_list: List[str]):
        """نشر الأخبار في جميع القنوات والجروبات"""
        if not news_list:
            return
        
        active_channels = self.get_active_channels()
        if not active_channels:
            logger.info("لا توجد قنوات نشطة لنشر الأخبار")
            return
        
        logger.info(f"نشر {len(news_list)} خبر في {len(active_channels)} قناة")
        
        for news_text in news_list:
            # تنسيق الخبر
            formatted_news = f"🚨 **خبر عاجل** 🚨\n\n{news_text}\n\n📺 الجزيرة مباشر"
            
            successful_sends = 0
            failed_channels = []
            
            for chat_id in active_channels:
                try:
                    await self.safe_api_request(
                        self.bot.send_message,
                        chat_id=chat_id,
                        text=formatted_news,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    successful_sends += 1
                    await asyncio.sleep(1)  # تأخير بين الرسائل
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    failed_channels.append(chat_id)
                    
                    # التحقق من أسباب الفشل الشائعة
                    if any(keyword in error_msg for keyword in ['bot was kicked', 'chat not found', 'forbidden']):
                        logger.warning(f"إزالة القناة {chat_id} - السبب: {e}")
                        self.deactivate_channel(chat_id)
                    elif 'flood control' in error_msg:
                        logger.warning(f"Flood control للقناة {chat_id}")
                        await asyncio.sleep(60)  # انتظار دقيقة
                    else:
                        logger.error(f"فشل في إرسال الخبر للقناة {chat_id}: {e}")
            
            if failed_channels:
                await self.send_error_to_admin(
                    "News Publishing Error",
                    f"فشل نشر الخبر في {len(failed_channels)} قناة من أصل {len(active_channels)}",
                    f"القنوات الفاشلة: {failed_channels}"
                )
            
            logger.info(f"تم نشر الخبر بنجاح في {successful_sends}/{len(active_channels)} قناة")
    
    def deactivate_channel(self, chat_id: int):
        """إلغاء تفعيل قناة"""
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('UPDATE channels SET is_active = 0 WHERE chat_id = ?', (chat_id,))
            conn.commit()
            conn.close()
            logger.info(f"تم إلغاء تفعيل القناة {chat_id}")
        except Exception as e:
            logger.error(f"خطأ في إلغاء تفعيل القناة: {e}")
    
    async def news_scheduler(self):
        """جدولة نشر الأخبار كل دقيقة"""
        consecutive_failures = 0
        max_failures = 5
        
        while self.is_running:
            try:
                logger.info("🔍 جاري البحث عن أخبار جديدة...")
                news_list = self.get_news_from_api()
                
                if news_list:
                    logger.info(f"📰 تم العثور على {len(news_list)} خبر جديد")
                    await self.publish_news_to_channels(news_list)
                    consecutive_failures = 0  # إعادة تعيين عداد الأخطاء
                else:
                    logger.info("ℹ️ لا توجد أخبار جديدة")
                
            except Exception as e:
                consecutive_failures += 1
                error_msg = f"خطأ في جدولة الأخبار (المحاولة {consecutive_failures}): {str(e)}"
                logger.error(error_msg)
                
                # إرسال تحذير للمشرف بعد عدة أخطاء متتالية
                if consecutive_failures >= max_failures:
                    await self.send_error_to_admin(
                        "Critical Scheduler Error",
                        f"فشل جدولة الأخبار {consecutive_failures} مرات متتالية",
                        traceback.format_exc()
                    )
                    consecutive_failures = 0  # إعادة تعيين العداد
                
                self.log_error_to_db("Scheduler Error", error_msg, traceback.format_exc())
            
            # انتظار دقيقة واحدة
            await asyncio.sleep(60)

    async def handle_new_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالج الرسائل العادية (للتأكد من أن البوت لا يُزعج المستخدمين)"""
        # لا نفعل شيئاً هنا، فقط لتجنب ظهور خطأ "Unhandled update"
        pass

    def is_user_banned(self, user_id: int) -> bool:
        """فحص ما إذا كان المستخدم محظوراً"""
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM banned_users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            conn.close()
            return result is not None
        except Exception as e:
            logger.error(f"خطأ في فحص حظر المستخدم: {e}")
            return False

    async def stop_bot(self):
        """إيقاف البوت بشكل آمن"""
        try:
            self.is_running = False
            if self.news_task and not self.news_task.done():
                self.news_task.cancel()
            if self.application:
                await self.application.stop()
            logger.info("✅ تم إيقاف البوت")
        except Exception as e:
            logger.error(f"خطأ أثناء إيقاف البوت: {e}")

# التطبيق الرئيسي
news_bot = RobustNewsBot()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البداية مع معالجة محسنة للأخطاء"""
    try:
        user = update.effective_user
        
        # فحص إذا كان المستخدم محظور
        if news_bot.is_user_banned(user.id):
            await update.message.reply_text("❌ أنت محظور من استخدام هذا البوت")
            return
        
        if user.id == ADMIN_USER_ID:
            # لوحة تحكم المالك
            keyboard = [
                [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="stats")],
                [InlineKeyboardButton("📢 القنوات المسجلة", callback_data="channels")],
                [InlineKeyboardButton("🚫 المستخدمين المحظورين", callback_data="banned_users")],
                [InlineKeyboardButton("🔧 اختبار البوت", callback_data="test_bot")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"مرحباً {user.first_name} 👋\n\n"
                "🤖 **لوحة تحكم بوت الأخبار العاجلة**\n\n"
                "✅ البوت يعمل بشكل طبيعي\n"
                "📺 مصدر الأخبار: الجزيرة مباشر\n"
                "🔄 تحديث كل: دقيقة واحدة\n\n"
                "اختر من القائمة أدناه:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        else:
            # واجهة المستخدمين العاديين
            await update.message.reply_text(
                f"مرحباً {user.first_name} 👋\n\n"
                "🤖 **بوت الأخبار العاجلة**\n\n"
                "📺 هذا البوت ينشر الأخبار العاجلة من الجزيرة مباشر تلقائياً\n\n"
                "📋 **لاستخدام البوت:**\n"
                "1️⃣ أضف البوت إلى قناتك أو جروبك\n"
                "2️⃣ اجعل البوت مشرف (أدمن) مع صلاحية إرسال الرسائل\n"
                "3️⃣ البوت سيبدأ بنشر الأخبار العاجلة تلقائياً كل دقيقة\n\n"
                "🔔 ستحصل على رسالة تأكيد عند تفعيل البوت بنجاح",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"خطأ في أمر /start: {e}")
        await news_bot.send_error_to_admin("Start Command Error", str(e), traceback.format_exc())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأزرار مع حماية أفضل"""
    try:
        query = update.callback_query
        await query.answer()
        
        if query.from_user.id != ADMIN_USER_ID:
            await query.edit_message_text("❌ غير مسموح لك بالوصول لهذه الوظيفة")
            return
        
        if query.data == "stats":
            # إحصائيات البوت
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM channels WHERE is_active = 1')
            active_channels = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM channels WHERE is_active = 0')
            inactive_channels = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM banned_users')
            banned_users = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM published_news')
            published_news = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM error_logs WHERE timestamp > datetime("now", "-24 hours")')
            errors_24h = cursor.fetchone()[0]
            
            conn.close()
            
            keyboard = [[InlineKeyboardButton("🔙 العودة", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            status = "🟢 يعمل" if news_bot.is_running else "🔴 متوقف"
            
            stats_text = (
                "📊 **إحصائيات البوت**\n\n"
                f"🤖 **حالة البوت:** {status}\n"
                f"📢 **القنوات النشطة:** {active_channels}\n"
                f"📴 **القنوات المتوقفة:** {inactive_channels}\n"
                f"🚫 **المستخدمين المحظورين:** {banned_users}\n"
                f"📰 **الأخبار المنشورة:** {published_news}\n"
                f"⚠️ **أخطاء آخر 24 ساعة:** {errors_24h}"
            )
            
            await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        
        elif query.data == "test_bot":
            # اختبار البوت
            try:
                test_message = "🧪 **اختبار البوت**\n\n✅ البوت يعمل بشكل طبيعي!"
                await context.bot.send_message(ADMIN_USER_ID, test_message, parse_mode=ParseMode.MARKDOWN)
                await query.edit_message_text("✅ تم اختبار البوت بنجاح! تحقق من رسائلك الخاصة.")
            except Exception as e:
                await query.edit_message_text(f"❌ فشل اختبار البوت: {str(e)}")
        
        elif query.data == "channels":
            # عرض القنوات المسجلة
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT chat_id, chat_title, chat_type, is_active, date_added 
                FROM channels 
                ORDER BY date_added DESC 
                LIMIT 10
            ''')
            channels = cursor.fetchall()
            conn.close()
            
            if not channels:
                text = "📢 لا توجد قنوات مسجلة حتى الآن"
            else:
                text = "📢 **القنوات المسجلة:** (آخر 10)\n\n"
                for chat_id, title, chat_type, is_active, date_added in channels:
                    status = "🟢" if is_active else "🔴"
                    date = datetime.fromisoformat(date_added).strftime('%Y-%m-%d')
                    text += f"{status} **{title}**\n"
                    text += f"   📱 النوع: {chat_type}\n"
                    text += f"   🆔 ID: `{chat_id}`\n"
                    text += f"   📅 تاريخ الإضافة: {date}\n\n"
            
            keyboard = [
                [InlineKeyboardButton("🔄 تحديث القائمة", callback_data="channels")],
                [InlineKeyboardButton("🔙 العودة", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        
        elif query.data == "banned_users":
            # عرض المستخدمين المحظورين
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, ban_date, reason FROM banned_users ORDER BY ban_date DESC')
            banned = cursor.fetchall()
            conn.close()
            
            if not banned:
                text = "🚫 لا يوجد مستخدمين محظورين"
            else:
                text = "🚫 **المستخدمين المحظورين:**\n\n"
                for user_id, ban_date, reason in banned:
                    date = datetime.fromisoformat(ban_date).strftime('%Y-%m-%d %H:%M')
                    text += f"• المستخدم: `{user_id}`\n"
                    text += f"  📅 تاريخ الحظر: {date}\n"
                    text += f"  📝 السبب: {reason}\n\n"
            
            keyboard = [
                [InlineKeyboardButton("🔄 تحديث القائمة", callback_data="banned_users")],
                [InlineKeyboardButton("🔙 العودة", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        
        elif query.data == "back_to_main":
            # العودة للقائمة الرئيسية
            keyboard = [
                [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="stats")],
                [InlineKeyboardButton("📢 القنوات المسجلة", callback_data="channels")],
                [InlineKeyboardButton("🚫 المستخدمين المحظورين", callback_data="banned_users")],
                [InlineKeyboardButton("🔧 اختبار البوت", callback_data="test_bot")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "🤖 **لوحة تحكم بوت الأخبار العاجلة**\n\n"
                "اختر من القائمة أدناه:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
    except Exception as e:
        logger.error(f"خطأ في معالج الأزرار: {e}")
        await news_bot.send_error_to_admin("Button Handler Error", str(e), traceback.format_exc())

async def handle_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إضافة البوت لقناة أو جروب مع تحسينات"""
    try:
        message = update.message
        
        # فحص إذا كان البوت قد أضيف كعضو جديد
        if message.new_chat_members:
            bot_user = await context.bot.get_me()
            
            for member in message.new_chat_members:
                if member.id == bot_user.id:
                    chat = message.chat
                    added_by = message.from_user.id if message.from_user else None
                    
                    logger.info(f"تم إضافة البوت للمحادثة: {chat.title} ({chat.id})")
                    
                    # انتظار قصير للتأكد من تحديث الصلاحيات
                    await asyncio.sleep(2)
                    
                    # التحقق من صلاحيات البوت
                    try:
                        bot_member = await context.bot.get_chat_member(chat.id, bot_user.id)
                        
                        if bot_member.status == ChatMember.ADMINISTRATOR and bot_member.can_post_messages:
                            # البوت أدمن ويمكنه الإرسال
                            success = news_bot.add_channel(
                                chat_id=chat.id,
                                chat_title=chat.title or "بدون عنوان",
                                chat_type=chat.type,
                                added_by=added_by
                            )
                            
                            if success:
                                await context.bot.send_message(
                                    chat_id=chat.id,
                                    text="✅ تم تفعيل البوت بنجاح! سيبدأ بنشر الأخبار العاجلة كل دقيقة."
                                )
                        else:
                            # البوت ليس لديه صلاحيات كافية
                            await context.bot.send_message(
                                chat_id=chat.id,
                                text="⚠️ البوت تمت إضافته لكن يحتاج إلى صلاحية الإرسال لكي يعمل."
                            )
                            
                    except Exception as e:
                        logger.warning(f"لا يمكن التحقق من صلاحيات البوت في {chat.id}: {e}")
                        
    except Exception as e:
        logger.error(f"خطأ في معالج إضافة البوت: {e}")
        await news_bot.send_error_to_admin("Bot Added Error", str(e), traceback.format_exc())

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /stop – يستخدمه المالك لإيقاف البوت"""
    try:
        user = update.effective_user
        if user.id != ADMIN_USER_ID:
            await update.message.reply_text("❌ هذا الأمر مخصص للمشرف فقط.")
            return

        await update.message.reply_text("🛑 جاري إيقاف البوت...")
        await news_bot.stop_bot()
        await update.message.reply_text("✅ تم إيقاف البوت بنجاح.")
        # إغلاق البرنامج بعد ثانيتين
        await asyncio.sleep(2)
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception as e:
        logger.error(f"خطأ في أمر /stop: {e}")
        await news_bot.send_error_to_admin("Stop Command Error", str(e), traceback.format_exc())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /stats – إحصائيات سريعة للجميع"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM channels WHERE is_active = 1')
        active_channels = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM published_news')
        published_news = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM error_logs WHERE timestamp > datetime("now", "-24 hours")')
        errors_24h = cursor.fetchone()[0]

        conn.close()

        status = "🟢 يعمل" if news_bot.is_running else "🔴 متوقف"

        text = (
            "📊 **إحصائيات البوت** (سريعة)\n\n"
            f"🤖 **حالة البوت:** {status}\n"
            f"📢 **القنوات النشطة:** {active_channels}\n"
            f"📰 **الأخبار المنشورة:** {published_news}\n"
            f"⚠️ **أخطاء آخر 24 ساعة:** {errors_24h}"
        )

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"خطأ في أمر /stats: {e}")
        await news_bot.send_error_to_admin("Stats Command Error", str(e), traceback.format_exc())

async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يتم استدعاؤه عند تغيير حالة البوت في أي دردشة (إضافته كأدمن أو إزالته)"""
    try:
        chat = update.effective_chat
        old = update.my_chat_member.old_chat_member
        new = update.my_chat_member.new_chat_member

        # إذا أصبح البوت أدمن
        if old.status != "administrator" and new.status == "administrator":
            logger.info(f"البوت أصبح أدمن في {chat.title} ({chat.id})")
            success = news_bot.add_channel(
                chat_id=chat.id,
                chat_title=chat.title or "بدون عنوان",
                chat_type=chat.type,
                added_by=update.effective_user.id if update.effective_user else None
            )
            if success:
                try:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text="✅ تم تفعيل البوت بنجاح! سيبدأ بنشر الأخبار العاجلة كل دقيقة."
                    )
                except Exception as e:
                    logger.warning(f"لم نستطع إرسال رسالة التأكيد للقناة: {e}")

        # إذا أُزيل البوت من الأدمنية
        elif old.status == "administrator" and new.status != "administrator":
            logger.info(f"البوت أُزيل من الأدمنية في {chat.title} ({chat.id})")
            news_bot.deactivate_channel(chat.id)

    except Exception as e:
        logger.error(f"خطأ في معالج تغيير حالة البوت: {e}")
        await news_bot.send_error_to_admin("Chat Member Update Error", str(e), traceback.format_exc())

async def main():
    """الدالة الرئيسية لتشغيل البوت"""
    try:
        logger.info("🚀 بدء تشغيل بوت الأخبار العاجلة...")

        # تهيئة قاعدة البيانات
        news_bot.init_database()
        news_bot.load_published_news()

        # إنشاء التطبيق
        news_bot.application = Application.builder().token(BOT_TOKEN).build()
        news_bot.bot = news_bot.application.bot

        # تسجيل المعالجات
        news_bot.application.add_handler(CommandHandler("start", start_command))
        news_bot.application.add_handler(CommandHandler("stop", stop_command))
        news_bot.application.add_handler(CommandHandler("stats", stats_command))
        news_bot.application.add_handler(CallbackQueryHandler(button_handler))
        news_bot.application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_bot_added))
        news_bot.application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, news_bot.handle_new_message))
        news_bot.application.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

        # بدء مهمة الجدولة
        news_bot.is_running = True
        news_bot.news_task = asyncio.create_task(news_bot.news_scheduler())

        # تشغيل البوت
        await news_bot.application.initialize()
        await news_bot.application.start()
        logger.info("✅ البوت يعمل الآن!")

        # الانتظار حتى يتم الإيقاف
        await news_bot.application.updater.start_polling()
        await asyncio.Event().wait()

    except Conflict:
        logger.error("❌ هناك نسخة أخرى من البوت تعمل حالياً!")
        await news_bot.send_error_to_admin("Conflict Error", "هناك نسخة أخرى من البوت تعمل حالياً!")
    except Exception as e:
        logger.error(f"❌ خطأ عام أثناء تشغيل البوت: {e}")
        await news_bot.send_error_to_admin("Startup Error", str(e), traceback.format_exc())
    finally:
        await news_bot.stop_bot()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 تم إيقاف البوت يدوياً (Ctrl+C)")
        asyncio.run(news_bot.stop_bot())