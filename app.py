import os
import random
import re
import json
import hmac
import hashlib
import base64
import zlib
import secrets
from flask import Flask, render_template, request, jsonify, send_from_directory
from groq import Groq
from dotenv import load_dotenv
import db

load_dotenv()

app = Flask(__name__)

MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
try:
    # timeout สั้นพอให้ไม่โดน Vercel ตัด function กลางทาง
    client = Groq(timeout=25.0, max_retries=1)
except Exception as e:  # ไม่มี GROQ_API_KEY -> อย่าให้ทั้งแอปพัง
    print("Groq init failed:", e)
    client = None

def get_db():
    return db.get_sqlite_conn()

# ---------------------------------------------------------------------------
# Signed tokens (save game + customer session) เก็บไว้ฝั่ง Client (localStorage)
# Vercel มีหลาย instance และ /tmp ถูกล้างบ่อย -> ให้ Client ถือข้อมูลสำรองที่เซ็นลายเซ็นไว้
# แก้ไขค่าเองไม่ได้ (HMAC) และอ่านค่าลับ (ราคาต่ำสุด/ของปลอม) ไม่ออก (obfuscated)
# ---------------------------------------------------------------------------
_SECRET = (os.environ.get("SECRET_KEY") or os.environ.get("GROQ_API_KEY") or "dealer-ai-dev").encode()
_KEY = hashlib.sha256(b"dealer-ai-token|" + _SECRET).digest()

def _keystream(nonce, n):
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(_KEY + nonce + i.to_bytes(4, 'big')).digest()
        i += 1
    return bytes(out[:n])

def make_token(obj):
    raw = zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    nonce = secrets.token_bytes(8)
    body = nonce + bytes(a ^ b for a, b in zip(raw, _keystream(nonce, len(raw))))
    sig = hmac.new(_KEY, body, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(sig + body).decode('ascii')

def read_token(token):
    if not token or not isinstance(token, str):
        return None
    try:
        blob = base64.urlsafe_b64decode(token.encode('ascii'))
        sig, body = blob[:16], blob[16:]
        if not hmac.compare_digest(sig, hmac.new(_KEY, body, hashlib.sha256).digest()[:16]):
            return None
        nonce, data = body[:8], body[8:]
        raw = bytes(a ^ b for a, b in zip(data, _keystream(nonce, len(data))))
        return json.loads(zlib.decompress(raw).decode('utf-8'))
    except Exception:
        return None

SHOP_FIELDS = ["nickname", "money", "day", "reputation", "customers_left",
               "guard_level", "repair_level", "forger_level", "ad_level",
               "version", "active_customer",
               "loan_remaining", "loan_daily", "loan_days_left", "loan_principal", "staff_data"]
INV_FIELDS = ["name", "value", "true_value", "bought_price", "image", "condition"]

STAFF_ROLE_INFO = {
    "guard": {
        "title": "ยามเฝ้าร้าน",
        "icon": "fas fa-shield-alt",
        "wages": [0, 1000, 2000, 3000],
        "costs": [0, 5000, 12000, 25000],
        "desc": {
            1: "ป้องกันการถูกโจรปล้นร้าน 50%",
            2: "กันโจร 100% และไล่นักเลงเก็บส่วย",
            3: "กันโจร+ได้เงินรางวัลนำจับ ฿5,000, ลดบทลงโทษเบี้ยวหนี้"
        }
    },
    "repair": {
        "title": "ช่างซ่อม",
        "icon": "fas fa-tools",
        "wages": [0, 1500, 3000, 5000],
        "costs": [0, 8000, 18000, 35000],
        "desc": {
            1: "สุ่มซ่อมของพัง 50% คืนละ 1 ชิ้น",
            2: "ซ่อมของพัง 1 ชิ้นทุกคืนแน่นอน",
            3: "ซ่อมของพังทุกชิ้นในคลังทุกคืน!"
        }
    },
    "forger": {
        "title": "นักปลอมแปลง",
        "icon": "fas fa-mask",
        "wages": [0, 2000, 4000, 7000],
        "costs": [0, 15000, 30000, 50000],
        "desc": {
            1: "พรางของปลอม: ลูกค้าตรวจจับได้ 40%",
            2: "พรางของปลอมเนียนขึ้น: ลูกค้าตรวจจับได้เพียง 15%",
            3: "พรางของปลอมขั้นเทพ: ลูกค้าดูไม่ออก 100% (ไม่โดนจับแน่นอน)"
        }
    },
    "ad": {
        "title": "นักโฆษณา",
        "icon": "fas fa-bullhorn",
        "wages": [0, 1000, 2500, 5000],
        "costs": [0, 8000, 20000, 40000],
        "desc": {
            1: "เพิ่มชื่อเสียงร้าน +1 ทุกวัน",
            2: "เพิ่มชื่อเสียงร้าน +2 ทุกวัน, ดึงลูกค้ามาต่อคิวเยอะขึ้น",
            3: "เพิ่มชื่อเสียงร้าน +4 ทุกวัน, ลูกค้าแน่นร้าน!"
        }
    },
    "expert": {
        "title": "ผู้เชี่ยวชาญ",
        "icon": "fas fa-search-dollar",
        "wages": [0, 1000, 2000, 4000],
        "costs": [0, 6000, 15000, 30000],
        "desc": {
            1: "ลดค่าตรวจของเหลือเพียง ฿250",
            2: "ตรวจของฟรี ฿0! (ทั้งตอนรับซื้อและขาย)",
            3: "ตรวจของฟรี + สแกนสถานะของปลอม/แท้ในร้านทันทีไม่ต้องกดตรวจ!"
        }
    }
}

STAFF_NAMES_FIRST = ["สมชาย", "วิชัย", "ลุงสนั่น", "เจ๊ณี", "อาร์ตี้", "ช่างอู๊ด", "ป้าศรี", "โต้ง", "บอย", "หมอเก่ง", "เฉียบ", "เอก", "เด่น", "น้าหม่ำ", "ก้อง", "ชาญ"]
STAFF_NAMES_LAST = ["สายลุย", "ตาเหยี่ยว", "เนียนกริบ", "ช่างทอง", "ปากหวาน", "สกิมเมอร์", "ตัวตึง", "มือฉมัง", "เซียนพระ", "มือทอง", "สายบู๊", "ตาเพชร", "ไร้พ่าย"]
MAX_STAFF_SLOTS = 3

def generate_candidate():
    role = random.choice(list(STAFF_ROLE_INFO.keys()))
    r = random.random()
    stars = 1 if r < 0.50 else (2 if r < 0.85 else 3)
    info = STAFF_ROLE_INFO[role]
    name = f"{random.choice(STAFF_NAMES_FIRST)} {random.choice(STAFF_NAMES_LAST)}"
    cand_id = f"cand_{secrets.token_hex(4)}"
    return {
        "id": cand_id,
        "name": name,
        "role": role,
        "role_title": info["title"],
        "icon": info["icon"],
        "stars": stars,
        "wage": info["wages"][stars],
        "cost": info["costs"][stars],
        "desc": info["desc"][stars]
    }

def get_staff_data(shop):
    raw = shop.get("staff_data")
    data = None
    if raw:
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            try:
                data = json.loads(raw)
            except Exception:
                data = None
    if not data or not isinstance(data, dict):
        data = {"hired": [], "candidates": [], "max_slots": MAX_STAFF_SLOTS}

    # Auto-migrate legacy levels once if hired has never been initialized
    if "migrated" not in data and not data.get("hired"):
        legacy_roles = [("guard", "guard_level"), ("repair", "repair_level"), ("forger", "forger_level"), ("ad", "ad_level")]
        migrated = []
        for role, col in legacy_roles:
            lvl_val = lvl(shop, col)
            if lvl_val > 0 and len(migrated) < MAX_STAFF_SLOTS:
                info = STAFF_ROLE_INFO[role]
                migrated.append({
                    "id": f"staff_{role}_{lvl_val}",
                    "name": f"{info['title']}ประจำร้าน",
                    "role": role,
                    "role_title": info["title"],
                    "icon": info["icon"],
                    "stars": lvl_val,
                    "wage": info["wages"][lvl_val],
                    "cost": info["costs"][lvl_val],
                    "desc": info["desc"][lvl_val]
                })
        data["hired"] = migrated
        data["migrated"] = True

    candidates = data.get("candidates") or []
    if len(candidates) < 3:
        while len(candidates) < 3:
            candidates.append(generate_candidate())
        data["candidates"] = candidates

    data["max_slots"] = MAX_STAFF_SLOTS
    return data

def sync_staff_levels(hired_list):
    res = {}
    for role in ["guard", "repair", "forger", "ad", "expert"]:
        role_stars = [s["stars"] for s in (hired_list or []) if s.get("role") == role]
        res[f"{role}_level"] = max(role_stars) if role_stars else 0
    return res

def calculate_hired_wages(hired_list):
    return sum(int(s.get("wage") or 0) for s in (hired_list or []))

def lvl(shop, key):
    v = shop.get(key)
    try:
        return max(0, min(3, int(v or 0)))
    except (TypeError, ValueError):
        return 0

def build_save(conn_or_pin, pin=None):
    target_pin = pin if pin is not None else conn_or_pin
    if not target_pin or not isinstance(target_pin, str):
        return None
    shop = db.get_shop(target_pin)
    if not shop:
        return None
    inv = db.get_inventory(target_pin)
    inv_data = [{k: r.get(k) for k in ["id"] + INV_FIELDS} for r in inv]
    return make_token({"t": "save", "pin": target_pin, "shop": {k: shop.get(k) for k in SHOP_FIELDS}, "inv": inv_data})

def ensure_shop(conn_or_pin, pin_or_token=None, save_token=None):
    """คืนค่า shop (dict) หรือ None
    ถ้า instance นี้ไม่มีข้อมูล หรือข้อมูลเก่ากว่าเซฟของ Client -> กู้คืนจากเซฟอัตโนมัติ"""
    if isinstance(conn_or_pin, str):
        target_pin = conn_or_pin
        token = pin_or_token
    else:
        target_pin = pin_or_token
        token = save_token
    if not target_pin:
        return None

    shop = db.get_shop(target_pin)
    snap = read_token(token)
    if snap and (snap.get("t") != "save" or snap.get("pin") != target_pin):
        snap = None

    if snap and (shop is None or (shop.get("version") or 0) < (snap["shop"].get("version") or 0)):
        db.restore_shop_and_inventory(target_pin, snap["shop"], snap.get("inv", []))
        shop = db.get_shop(target_pin)

    if shop and snap and snap.get("shop"):
        for k in ["loan_remaining", "loan_daily", "loan_days_left", "loan_principal"]:
            if k in snap["shop"] and (shop.get(k) is None):
                shop[k] = snap["shop"][k]

    return shop

def bump_version(c_or_pin=None, pin=None):
    target_pin = pin if pin is not None else c_or_pin
    if target_pin and isinstance(target_pin, str):
        db.bump_shop_version(target_pin)

def req_json():
    return request.get_json(silent=True) or {}

def shop_not_found():
    return jsonify({"status": "error", "message": "Shop not found"})

def clean_llm(text):
    """ตัด <think>...</think> (โมเดลตระกูล qwen บางรุ่นจะคิดออกมาด้วย)"""
    text = text or ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S)
    text = re.sub(r'<think>.*', '', text, flags=re.S)
    return text.strip()

TAG_ACCEPT = re.compile(r'\[\s*(SELL|BUY)_ACCEPTED\s*:\s*([\d,\.]+)\s*\]', re.I)
TAG_REJECT = re.compile(r'\[\s*DEAL_REJECTED\s*\]', re.I)

def strip_tags(text):
    text = TAG_ACCEPT.sub('', text)
    text = TAG_REJECT.sub('', text)
    text = re.sub(r'\[\s*(SELL|BUY)_ACCEPTED[^\]]*\]', '', text, flags=re.I)
    return text.strip()

GROQ_MODELS = [
    os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b"),
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "allam-2-7b"
]

def get_groq_keys(custom_key=None):
    keys = []
    if custom_key and isinstance(custom_key, str) and custom_key.strip().startswith("gsk_"):
        keys.append(custom_key.strip())
    env_keys = os.environ.get("GROQ_API_KEYS", "")
    if env_keys:
        for k in env_keys.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
    single_key = os.environ.get("GROQ_API_KEY", "")
    if single_key and single_key.strip() and single_key.strip() not in keys:
        keys.append(single_key.strip())
    return keys

def call_llm(messages, custom_key=None):
    """เรียก Groq พร้อมระบบ Auto-Fallback สลับโมเดลและคีย์อัตโนมัติเมื่อติด Rate Limit"""
    keys = get_groq_keys(custom_key)
    if not keys:
        return None

    for key in keys:
        try:
            c = Groq(api_key=key, timeout=18.0, max_retries=0)
        except Exception:
            continue

        for model_name in GROQ_MODELS:
            try:
                completion = c.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=400,
                )
                text = clean_llm(completion.choices[0].message.content)
                if text:
                    return text
            except Exception as e:
                print(f"[Groq Fallback] Model {model_name} failed: {e}")
                continue

    return None

def llm(messages, custom_key=None):
    return call_llm(messages, custom_key=custom_key)

def extract_price(text):
    text = (text or '').strip().lower()
    m = re.search(r'([\d\.]+)\s*(แสน|หมื่น|พัน|k)', text)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit == 'แสน': return int(val * 100000)
        elif unit == 'หมื่น': return int(val * 10000)
        elif unit in ('พัน', 'k'): return int(val * 1000)
    matches = re.findall(r'(\d[\d,]*\d|\d+)', text)
    nums = [int(m.replace(',', '')) for m in matches if m.replace(',', '').isdigit()]
    return max(nums) if nums else None

def fmt_th(n):
    return f"{int(n):,}"

def generate_fallback_greeting(game):
    item = game.get("item") or {}
    item_name = item.get("name", "ของชิ้นนี้")
    value = game.get("value") or item.get("value", 10000)
    is_female = "หญิง" in str(game.get("gender", ""))
    pronoun = "ฉัน" if is_female else "ผม"
    ending = "ค่ะ" if is_female else "ครับ"
    ending_q = "คะ" if is_female else "ครับ"
    trans = game.get("transaction_type", "sell")
    persona = str(game.get("persona_desc", ""))

    if trans == "sell":
        if game.get("is_fake"):
            asking = int(value * 1.25)
            greetings = [
                f"สวัสดี{ending} มี{item_name}สภาพแท้ 100% สวยมากมาปล่อย ขอเปิดที่ {fmt_th(asking)} บาท{ending} สนใจรับไหม{ending_q}?",
                f"สวัสดี{ending} {pronoun}เอา{item_name}ของสะสมมาส่งต่อให้ ขอแค่ {fmt_th(asking)} บาทพอ{ending} สภาพใหม่กริบเลย!",
                f"สวัสดี{ending} พอดีได้{item_name}มา สภาพดีมาก ขอปล่อยที่ {fmt_th(asking)} บาท{ending}"
            ]
        elif "ร้อนเงิน" in persona:
            asking = int(value * random.uniform(0.75, 0.90))
            greetings = [
                f"สวัสดี{ending} พอดี{pronoun}มีเรื่องต้องรีบใช้เงินด่วน ขอปล่อย{item_name}ชิ้นนี้สัก {fmt_th(asking)} บาทได้ไหม{ending_q}?",
                f"สวัสดี{ending} ร้อนเงินมากๆ เลย{ending} ปล่อย{item_name}ให้ราคาพิเศษเลย ขอแค่ {fmt_th(asking)} บาทพอนะ{ending}?",
                f"สวัสดี{ending} ช่วย{pronoun}หน่อยนะคะ/ครับ กำลังรีบใช้เงิน ขอขาย{item_name}ที่ {fmt_th(asking)} บาทพอ{ending}"
            ]
        elif "เขี้ยวลากดิน" in persona:
            asking = int(value * random.uniform(1.3, 1.5))
            greetings = [
                f"สวัสดี{ending} {pronoun}เอา{item_name}ระดับแรร์มาปล่อย สภาพแบบนี้หาที่ไหนไม่ได้แล้ว ขอเปิดที่ {fmt_th(asking)} บาท{ending}",
                f"สวัสดี{ending} ของดีแบบ{item_name}ถ้าทางร้านตาถึงคงรู้นะครับ ขอเปิดราคาที่ {fmt_th(asking)} บาท{ending}",
                f"สวัสดี{ending} นำ{item_name}ของหวงมาส่งต่อ ราคานี้ {fmt_th(asking)} บาทถือว่าคุ้มสุดๆ{ending}"
            ]
        elif "ซื่อๆ" in persona:
            asking = int(value * random.uniform(0.85, 1.05))
            greetings = [
                f"สวัสดี{ending} พอดีเก็บห้องเจอ{item_name} ไม่ค่อยรู้ราคาตลาด ขอขายสัก {fmt_th(asking)} บาทได้ไหม{ending_q}?",
                f"สวัสดี{ending} เอา{item_name}มาขายครับ ไม่รู้เขาซื้อกันเท่าไหร่ ขอสัก {fmt_th(asking)} บาทละกัน{ending}",
                f"สวัสดี{ending} มี{item_name}เก่าเก็บมาปล่อย ขอราคา {fmt_th(asking)} บาทพอไหวไหม{ending_q}?"
            ]
        else:
            asking = int(value * random.uniform(1.05, 1.25))
            greetings = [
                f"สวัสดี{ending} เอา{item_name}มาเสนอขายให้ทางร้านดูครับ สภาพดีมาก ขอเปิดที่ {fmt_th(asking)} บาทนะ{ending}",
                f"สวัสดี{ending} วันนี้นำ{item_name}มาส่งต่อ ขอราคาเริ่มต้นที่ {fmt_th(asking)} บาทครับ",
                f"สวัสดี{ending} สนใจรับ{item_name}ไว้ไหมครับ ขอเปิดราคาที่ {fmt_th(asking)} บาท{ending}"
            ]
        game["last_bot_price"] = asking
        return random.choice(greetings)
    else:
        # Buy mode
        if "เศรษฐี" in persona:
            bid = int(value * random.uniform(1.05, 1.30))
            greetings = [
                f"สวัสดี{ending}! พอดีเห็น{item_name}ในตู้แล้วถูกใจมาก {pronoun}ให้ {fmt_th(bid)} บาทเลย ขายให้{pronoun}ได้ไหม{ending_q}?",
                f"สวัสดี{ending} ตามหา{item_name}มานาน ให้ราคาพิเศษเลย {fmt_th(bid)} บาท ปล่อยให้{pronoun}นะครับ",
                f"สวัสดี{ending} ชิ้นนี้สวยมาก! {pronoun}เสนอซื้อที่ {fmt_th(bid)} บาท พร้อมจ่ายสดเลย{ending}"
            ]
        elif "พ่อค้าคนกลาง" in persona:
            bid = int(value * random.uniform(0.50, 0.65))
            greetings = [
                f"สวัสดี{ending} สนใจ{item_name}ในร้านครับ ขอรับไปปล่อยต่อสัก {fmt_th(bid)} บาท พอจะไหวไหม{ending_q}?",
                f"สวัสดี{ending} {item_name}ชิ้นนั้นปล่อยให้{pronoun}สัก {fmt_th(bid)} บาทได้ไหมครับ รับเงินสดทันทีเลย",
                f"สวัสดี{ending} มาหาของไปขายต่อ สนใจ{item_name} ขอราคา {fmt_th(bid)} บาทได้ไหม{ending_q}?"
            ]
        else:
            bid = int(value * random.uniform(0.70, 0.85))
            greetings = [
                f"สวัสดี{ending} สนใจ{item_name}ชิ้นนี้มาก ถ้า{pronoun}ขอซื้อสัก {fmt_th(bid)} บาท พอจะปล่อยให้ได้ไหม{ending_q}?",
                f"สวัสดี{ending} ขอดู{item_name}หน่อยครับ ถ้าให้ราคา {fmt_th(bid)} บาท พอจะไหวไหม{ending_q}?",
                f"สวัสดี{ending} อยากได้{item_name}ไปใช้งาน เสนอราคาซื้อที่ {fmt_th(bid)} บาท ขายไหม{ending_q}?"
            ]
        game["last_bot_price"] = bid
        return random.choice(greetings)

def classify_intent(user_msg, game):
    text = (user_msg or "").strip().lower()
    price = extract_price(text)
    trans = game.get("transaction_type", "sell")
    value = game.get("value", 10000)

    if price is not None:
        if trans == "sell":
            min_price = int(game.get("min_price") or value * 0.6)
            if price >= min_price:
                return "ACCEPT_PRICE", price
            elif price < min_price * 0.45 or price <= 0:
                return "INSULTING_PRICE", price
            else:
                return "COUNTER_PRICE", price
        else:
            max_price = int(game.get("max_price") or value * 1.1)
            if price <= max_price:
                return "ACCEPT_PRICE", price
            elif price > max_price * 1.6:
                return "OUTRAGEOUS_PRICE", price
            else:
                return "COUNTER_PRICE", price

    # Text keyword intents
    if any(k in text for k in ["แท้", "จริง", "ปลอม", "เช็ค", "ตรวจ", "แท้ไหม", "ดูยังไง", "สภาพ"]):
        return "CHECK_AUTHENTICITY", None
    if any(k in text for k in ["ลด", "ลดหน่อย", "ลดได้ไหม", "แพง", "แพงไป", "ถูกกว่านี้", "ขอร้อง", "ช่วยหน่อย"]):
        return "ASK_DISCOUNT", None
    if any(k in text for k in ["สวย", "ดี", "ชอบ", "เจ๋ง", "น่าสนใจ", "หล่อ", "สวยจัง", "เท่", "ยินดี", "ขอบคุณ"]):
        return "COMPLIMENT", None
    if any(k in text for k in ["ขี้โกง", "หลอก", "โกง", "มิจฉาชีพ", "ห่วย", "ต้มตุ๋น", "ขโมย", "บ้า"]):
        return "ANGRY_INSULT", None
    if any(k in text for k in ["เท่าไหร่", "ราคา", "ขอราคา", "ขายเท่าไหร่", "ซื้อเท่าไหร่", "เปิดเท่าไหร่"]):
        return "ASK_PRICE", None
    if any(k in text for k in ["สวัสดี", "หวัดดี", "ว่าไง", "สบายดี", "มาจากไหน", "กินข้าวยัง"]):
        return "SMALLTALK", None

    return "GENERAL_CHAT", None

def generate_fallback_chat(game, user_msg):
    intent, price = classify_intent(user_msg, game)
    item = game.get("item") or {}
    value = game.get("value") or item.get("value", 10000)
    trans = game.get("transaction_type", "sell")
    is_female = "หญิง" in str(game.get("gender", ""))
    pronoun = "ฉัน" if is_female else "ผม"
    ending = "ค่ะ" if is_female else "ครับ"
    ending_q = "คะ" if is_female else "ครับ"
    persona = str(game.get("persona_desc", ""))

    if trans == "sell":
        min_price = int(game.get("min_price") or value * 0.6)
        last_price = int(game.get("last_bot_price") or value * 1.1)

        if intent == "ACCEPT_PRICE":
            if "ร้อนเงิน" in persona:
                replies = [
                    f"ราคานี้{pronoun}ตกลงเลย{ending}! ขอบคุณมากที่ช่วยรับซื้อ [SELL_ACCEPTED:{price}]",
                    f"ดีลครับพี่! เงินก้อนนี้ช่วยชีวิตผมได้ทันเวลาพอดี [SELL_ACCEPTED:{price}]",
                    f"โอเคเลย{ending} ถือว่าช่วยกัน ตกลงตามนี้เลยครับ [SELL_ACCEPTED:{price}]"
                ]
            elif "เขี้ยวลากดิน" in persona:
                replies = [
                    f"เฮ้อ... เจอลูกค้าต่อเก่งแบบนี้ยอมเลย{ending} ตกลงตามนี้ครับ [SELL_ACCEPTED:{price}]",
                    f"กัดฟันปล่อยให้เลยนะเนี่ย อย่าเอาไปขายต่อแพงกว่าผมล่ะ ตกลงครับ [SELL_ACCEPTED:{price}]",
                    f"คุณนี่ตาถึงจริงๆ ราคานี้ถือว่าแฟร์ทั้งคู่ ดีลครับ [SELL_ACCEPTED:{price}]"
                ]
            elif game.get("is_fake"):
                replies = [
                    f"ราคาสวยเลยพี่! ตกลงครับ รับเงินแล้วห้ามเปลี่ยนใจนะ ดีล! [SELL_ACCEPTED:{price}]",
                    f"โอเคเลยพี่! โอนเงินแล้วรับของไปได้เลย คุ้มแน่นอน! [SELL_ACCEPTED:{price}]"
                ]
            else:
                replies = [
                    f"ตกลง{ending} ราคานี้{pronoun}พอรับได้ ดีลตามนี้เลยครับ! [SELL_ACCEPTED:{price}]",
                    f"โอเคครับคุณพี่ ราคานี้ถือว่าลงตัว ตกลงขายครับ [SELL_ACCEPTED:{price}]",
                    f"ยินดีที่ได้ร่วมดีลครับ ราคานี้จัดไปเลย! [SELL_ACCEPTED:{price}]"
                ]
            return random.choice(replies)

        elif intent == "INSULTING_PRICE":
            replies = [
                f"โอ้โห กดราคาโหดร้ายเกินไปแล้ว{ending}! ราคานี้ไม่ขายเด็ดขาด ขอยกเลิกดีลเลย [DEAL_REJECTED]",
                f"ราคานี้เก็บไว้ทิ้งขยะยังดีกว่าขายให้คุณเลย! ขอยกเลิกดีล [DEAL_REJECTED]",
                f"ตั้งราคาแบบนี้ดูถูกกันชัดๆ ไม่คุยด้วยแล้ว{ending}! [DEAL_REJECTED]"
            ]
            return random.choice(replies)

        elif intent == "COUNTER_PRICE":
            counter = max(min_price, int((last_price + price) / 2))
            if counter <= price:
                counter = min_price
            game["last_bot_price"] = counter
            replies = [
                f"ราคา {fmt_th(price)} บาทถูกไปหน่อยครับพี่ {pronoun}ขอสัก {fmt_th(counter)} บาทได้ไหม{ending_q}? ลดให้สุดๆ แล้วนะ{ending}",
                f"โอ้โห ราคานั้น{pronoun}ขาดทุนยับเลย{ending} ขอขยับขึ้นมาอีกนิดเป็น {fmt_th(counter)} บาท พอไหวไหม{ending_q}?",
                f"ยังต่ำไปนิดนึง{ending} ถ้าคุณพี่ให้ได้สัก {fmt_th(counter)} บาท {pronoun}ปล่อยให้ทันทีเลย!",
                f"ถูกไปนิดครับพี่ สภาพของยังดีอยู่เลย ขอ {fmt_th(counter)} บาทเถอะครับ ขาดตัวแล้วจริงๆ"
            ]
            return random.choice(replies)

        elif intent == "CHECK_AUTHENTICITY":
            if game.get("is_fake"):
                replies = [
                    f"{pronoun}รับประกันด้วยเกียรติเลย{ending} ของแท้แน่นอน สภาพสวยขนาดนี้สนใจให้ราคาเท่าไหร่คะ/ครับ?",
                    f"แท้ล้านเปอร์เซ็นต์พี่! สภาพนางฟ้าเลย ไม่แท้เอามาปาใส่หน้าได้เลย ลองเสนอราคามาดูสิ{ending}",
                    f"ดูเนื้องานสิครับพี่ กริบขนาดนี้ ของปลอมที่ไหนจะทำได้เนียนขนาดนี้!"
                ]
            else:
                replies = [
                    f"ของแท้ 100% แน่นอน{ending} สภาพเดิมๆ ตรวจดูได้ทุกจุดเลย คุณพี่สนใจรับไว้ที่เท่าไหร่ดี{ending_q}?",
                    f"แท้แน่นอน{ending} มีที่มาที่ไปชัดเจน เช็คความแท้ได้เลยครับ ลองเสนอราคามาได้เลย",
                    f"ของแท้ชัวร์ครับพี่ สภาพกริบมาก คุณพี่กะว่าจะให้สักกี่บาทดีครับ?"
                ]
            return random.choice(replies)

        elif intent == "ASK_DISCOUNT":
            counter = max(min_price, int(last_price * 0.92))
            game["last_bot_price"] = counter
            replies = [
                f"ลดให้ได้นิดหน่อย{ending} ถ้างั้น{pronoun}ยอมลดให้เหลือ {fmt_th(counter)} บาท คุณพี่ไหวไหม{ending_q}?",
                f"โธ่พี่... ของดีขนาดนี้ลดเยอะไม่ได้จริงๆ ยอมถอยก้าวหนึ่งเหลือ {fmt_th(counter)} บาทนะ{ending}",
                f"ถ้าจบไว{pronoun}ให้ที่ {fmt_th(counter)} บาทครับ คุณพี่ตกลงเอาเลยไหมล่ะ{ending_q}?"
            ]
            return random.choice(replies)

        elif intent == "COMPLIMENT":
            replies = [
                f"ขอบคุณมากครับคุณพี่ ตาถึงจริงๆ! ถ้างั้นสนใจรับไปดูแลในราคาเท่าไหร่ดีครับ?",
                f"แหม ปากหวานแบบนี้ เดี๋ยวลดราคาพิเศษให้เลยครับ ลองเสนอราคามาดูสิครับ!",
                f"ดีใจที่ชอบครับ ชิ้นนี้สวยจริง คุณพี่ไหวที่เท่าไหร่ว่ามาได้เลย{ending}"
            ]
            return random.choice(replies)

        elif intent == "ANGRY_INSULT":
            replies = [
                f"อ้าว ทำไมพูดจาแบบนี้ล่ะ{ending} ถ้าไม่อยากซื้อขายดีๆ ก็บอกกันดีๆ สิ",
                f"ใจเย็นๆ สิครับพี่ มาคุยเรื่องราคากันดีกว่า อย่าเพิ่งโมโหเลย",
                f"พูดจาไม่สุภาพเลยนะครับ มีอะไรค่อยๆ คุยกันดีกว่า"
            ]
            return random.choice(replies)

        elif intent == "ASK_PRICE":
            replies = [
                f"ชิ้นนี้{pronoun}เปิดราคาไว้ที่ {fmt_th(last_price)} บาทครับ คุณพี่คิดว่ายังไง ลองเสนอมาได้เลย{ending}",
                f"ราคาตั้งต้นอยู่ที่ {fmt_th(last_price)} บาทครับพี่ แต่ถ้าคุณพี่ชอบจริง ต่อรองได้นิดหน่อยนะ"
            ]
            return random.choice(replies)

        elif intent == "SMALLTALK":
            replies = [
                f"สวัสดีครับคุณพี่ วันนี้แวะมาคุยเรื่องของชิ้นนี้กันก่อนดีกว่า สนใจให้ราคาเท่าไหร่ดีครับ?",
                f"ยินดีที่ได้รู้จักครับ ชิ้นนี้สภาพดีจริงๆ คุณพี่ลองเสนอราคาที่คิดว่าแฟร์มาได้เลย{ending}"
            ]
            return random.choice(replies)

        else:
            replies = [
                f"คุณพี่ช่วยระบุตัวเลขราคาที่ต้องการมาได้เลย{ending} จะได้รีบปิดดีลกัน{ending}",
                f"ลองเสนอตัวเลขราคามาได้เลยครับพี่ ถ้าถูกใจเดี๋ยวปิดดีลให้ทันทีเลย!",
                f"สนใจรับที่ราคาเท่าไหร่ครับ บอกตัวเลขมาได้เลย เดี๋ยวผมดูให้{ending}"
            ]
            return random.choice(replies)

    else:
        # Buy mode (customer buying from player)
        max_price = int(game.get("max_price") or value * 1.1)
        last_price = int(game.get("last_bot_price") or value * 0.8)

        if intent == "ACCEPT_PRICE":
            if "เศรษฐี" in persona:
                replies = [
                    f"ราคานี้สบายมาก{ending}! {pronoun}ขอรับชิ้นนี้เลย แพ็คของให้ด้วยนะ [BUY_ACCEPTED:{price}]",
                    f"ดีลครับ! เงินไม่ใช่ปัญหา ชิ้นนี้ผมถูกใจมาก เดี๋ยวจ่ายสดเลย [BUY_ACCEPTED:{price}]"
                ]
            elif "พ่อค้าคนกลาง" in persona:
                replies = [
                    f"ราคานี้พอมีกำไรไปปล่อยต่อได้ ตกลงผมรับไว้ครับ ขอบคุณครับ [BUY_ACCEPTED:{price}]",
                    f"สวยครับ ราคานี้จบไวดี ตกลงตามนี้เลย จ่ายสดทันที [BUY_ACCEPTED:{price}]"
                ]
            else:
                replies = [
                    f"ตกลง{ending} ราคานี้{pronoun}รับได้ ตกลงซื้อเลยครับ! [BUY_ACCEPTED:{price}]",
                    f"ราคานี้อยู่ในงบพอดีเลยค่ะ ตกลงหนูซื้อชิ้นนี้เลย ขอบคุณนะคะ [BUY_ACCEPTED:{price}]",
                    f"ดีลครับคุณพี่! ถูกใจชิ้นนี้มานาน ขอรับไปดูแลต่อนะครับ [BUY_ACCEPTED:{price}]"
                ]
            return random.choice(replies)

        elif intent == "OUTRAGEOUS_PRICE":
            replies = [
                f"แพงเว่อร์ขนาดนี้ไม่ไหวหรอก{ending} ตั้งราคาเอารวยเลยเหรอ ขอผ่านดีกว่า! [DEAL_REJECTED]",
                f"ราคานี้เกินงบไปไกลมากครับ ไม่สู้แล้ว ขอยกเลิกดีล [DEAL_REJECTED]",
                f"แพงเกินไปมากครับ ไปขายให้เทวดาเถอะ ไม่เอาแล้ว! [DEAL_REJECTED]"
            ]
            return random.choice(replies)

        elif intent == "COUNTER_PRICE":
            counter = min(max_price, int((last_price + max_price) / 2))
            if counter >= price:
                counter = max_price
            game["last_bot_price"] = counter
            replies = [
                f"ราคา {fmt_th(price)} บาทตึงไปหน่อยครับพี่ ถ้า{pronoun}ให้สุดๆ ที่ {fmt_th(counter)} บาท พอจะปล่อยให้ได้ไหม{ending_q}?",
                f"แพงไปนิดครับคุณพี่ ลดให้หน่อยนะ {pronoun}ให้สุดๆ ที่ {fmt_th(counter)} บาท ขาดตัวเลย!",
                f"ราคานั้นยังไม่ไหวครับพี่ ถ้าสัก {fmt_th(counter)} บาท {pronoun}จ่ายสดตอนนี้เลย ไหวไหม{ending_q}?"
            ]
            return random.choice(replies)

        elif intent == "ASK_DISCOUNT":
            counter = min(max_price, int(last_price * 1.05))
            game["last_bot_price"] = counter
            replies = [
                f"ถ้าคุณพี่ยอมลดให้หน่อย {pronoun}ขยับราคาให้เป็น {fmt_th(counter)} บาท ไหวไหม{ending_q}?",
                f"ถ้างั้น{pronoun}เพิ่มให้อีกนิดเป็น {fmt_th(counter)} บาท ปล่อยให้เลยได้ไหมครับ?"
            ]
            return random.choice(replies)

        elif intent == "COMPLIMENT":
            replies = [
                f"ชิ้นนี้สวยจริงๆ ครับ ถ้างั้นคุณพี่จะปล่อยให้ผมที่ราคาเท่าไหร่ดีครับ?",
                f"เห็นด้วยเลยครับ! ลดราคาให้คนชอบของเหมือนกันหน่อยนะ คุณพี่ตั้งราคาเท่าไหร่ดี?"
            ]
            return random.choice(replies)

        else:
            replies = [
                f"คุณพี่ลองบอกราคาตัวเลขที่อยากขายมาได้เลย{ending} ถ้าพอรับได้ผมจ่ายสดทันทีเลย!",
                f"อยากปล่อยชิ้นนี้ที่ราคาเท่าไหร่ครับ บอกตัวเลขมาได้เลย{ending}",
                f"ช่วยเสนอราคาขายมาหน่อยครับ เดี๋ยว{pronoun}ดูว่างบถึงไหม"
            ]
            return random.choice(replies)

def init_db():
    db.init_db()

init_db()

current_games = {} # Key: pin, Value: game_state dict (cache เท่านั้น ตัวจริงอยู่ใน session token)

ITEMS = [
    {"name": "นาฬิกา Rolex รุ่นคุณปู่", "value": 150000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/Rolex_Submariner.jpg/500px-Rolex_Submariner.jpg"},
    {"name": "การ์ด Charizard (Holo)", "value": 20000, "image": "/static/images/charizard.jpg"},
    {"name": "เครื่องเกม PS5 มือสอง", "value": 12000, "image": "https://upload.wikimedia.org/wikipedia/commons/7/77/Black_and_white_Playstation_5_base_edition_with_controller.png"},
    {"name": "กีตาร์ Fender ปี 1990", "value": 35000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/63/Fender_Stratocaster_004-2.jpg/500px-Fender_Stratocaster_004-2.jpg"},
    {"name": "สร้อยคอทองคำ 1 บาท", "value": 40000, "image": "/static/images/gold.jpg"},
    {"name": "ภาพวาดสีน้ำมันเก่าเก็บ", "value": 8000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ec/Mona_Lisa%2C_by_Leonardo_da_Vinci%2C_from_C2RMF_retouched.jpg/500px-Mona_Lisa%2C_by_Leonardo_da_Vinci%2C_from_C2RMF_retouched.jpg"},
    {"name": "กระเป๋า Hermes", "value": 250000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/db/Pink_Birkin_bag.jpg/500px-Pink_Birkin_bag.jpg"},
    {"name": "MacBook Pro M3 มือสอง", "value": 45000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/MacBook_Pro_16_%28M1_Pro%2C_2021%29_-_Wikipedia.jpg/500px-MacBook_Pro_16_%28M1_Pro%2C_2021%29_-_Wikipedia.jpg"},
    {"name": "แหวนเพชร 1 กะรัต น้ำ 99", "value": 120000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/08/Two_engagement_rings_1.jpg/500px-Two_engagement_rings_1.jpg"},
    {"name": "เหรียญกษาปณ์โบราณ ร.๕", "value": 15000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ee/Kiloware.JPG/500px-Kiloware.JPG"},
    {"name": "กล้องฟิล์ม Leica M6", "value": 90000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e6/Leica_M6_TTL_front.jpg/500px-Leica_M6_TTL_front.jpg"},
    {"name": "รองเท้า Nike Air Jordan 1", "value": 25000, "image": "/static/images/nike.jpg"},
    {"name": "แว่นตา Ray-Ban ยุค 80s", "value": 5000, "image": "/static/images/rayban.jpg"},
    {"name": "ดาบคาตานะญี่ปุ่นแท้", "value": 85000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9b/Katana_-_Motoshige.JPG/500px-Katana_-_Motoshige.JPG"},
    {"name": "จักรยานเสือหมอบคาร์บอน", "value": 60000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Look_795_30th_Anniversary_Dura-Ace_9100-Mavic_Custom_Build_%2830636542393%29.jpg/500px-Look_795_30th_Anniversary_Dura-Ace_9100-Mavic_Custom_Build_%2830636542393%29.jpg"},
    {"name": "เครื่องพิมพ์ดีดโบราณ", "value": 4500, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b7/MEK_II-371.jpg/500px-MEK_II-371.jpg"},
    {"name": "แจกันเครื่องลายครามจีน", "value": 30000, "image": "/static/images/vase.jpg"},
    {"name": "ฟิกเกอร์ Iron Man (Limited)", "value": 18000, "image": "/static/images/robot.jpg"},
    {"name": "กระเป๋าเดินทาง Rimowa", "value": 32000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c0/Suitcase1.jpg/500px-Suitcase1.jpg"},
    {"name": "iPhone 15 Pro Max มือสอง", "value": 28000, "image": "/static/images/iphone.jpg"},
    {"name": "พรมเปอร์เซียทอมือแท้", "value": 55000, "image": "/static/images/carpet.jpg"},
]

AVATARS = [
    {"url": "https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=200&q=80", "gender": "ชาย (ผู้ชาย)"},
    {"url": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=200&q=80", "gender": "หญิง (ผู้หญิง)"},
    {"url": "https://images.unsplash.com/photo-1599566150163-29194dcaad36?w=200&q=80", "gender": "ชาย (ผู้ชาย)"},
    {"url": "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=200&q=80", "gender": "หญิง (ผู้หญิง)"},
    {"url": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=200&q=80", "gender": "ชาย (ผู้ชาย)"},
    {"url": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=200&q=80", "gender": "ชาย (ผู้ชาย)"},
    {"url": "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=200&q=80", "gender": "หญิง (ผู้หญิง)"},
    {"url": "https://images.unsplash.com/photo-1580489944761-15a19d654956?w=200&q=80", "gender": "หญิง (ผู้หญิง)"}
]

def get_condition_info(pct):
    pct = max(10, min(100, int(pct if pct is not None else 100)))
    if pct >= 95:
        return {"pct": pct, "name": "ใหม่กริบ (Mint)", "color": "#10b981", "badge_class": "badge-mint", "multiplier": 1.0}
    elif pct >= 80:
        return {"pct": pct, "name": "ดีเยี่ยม (Excellent)", "color": "#06b6d4", "badge_class": "badge-excellent", "multiplier": 0.90}
    elif pct >= 65:
        return {"pct": pct, "name": "สภาพดี (Good)", "color": "#3b82f6", "badge_class": "badge-good", "multiplier": 0.80}
    elif pct >= 45:
        return {"pct": pct, "name": "พอใช้ (Fair)", "color": "#f59e0b", "badge_class": "badge-fair", "multiplier": 0.65}
    elif pct >= 25:
        return {"pct": pct, "name": "ชำรุด (Damaged)", "color": "#f97316", "badge_class": "badge-damaged", "multiplier": 0.45}
    else:
        return {"pct": pct, "name": "เสียหายหนัก (Broken)", "color": "#ef4444", "badge_class": "badge-broken", "multiplier": 0.30}

CUSTOMER_MOODS = [
    {
        "id": "joyful",
        "name": "ร่าเริงแจ่มใส",
        "icon": "fas fa-laugh-beam",
        "color": "#10b981",
        "desc": "อารมณ์ดีเป็นพิเศษ ยิ้มแย้ม เปิดใจรับข้อเสนอ",
        "effect": "ต่อรองง่าย พร้อมรับฟังราคาที่สมเหตุสมผล"
    },
    {
        "id": "calm",
        "name": "สุขุมใจเย็น",
        "icon": "fas fa-smile",
        "color": "#06b6d4",
        "desc": "อารมณ์คงที่ มีสติสุขุม พูดคุยด้วยเหตุผล",
        "effect": "ใช้ตัวเลขและราคาที่สมเหตุสมผลในการเจรจา"
    },
    {
        "id": "rushed",
        "name": "รีบร้อนกระวนกระวาย",
        "icon": "fas fa-stopwatch",
        "color": "#f59e0b",
        "desc": "มีธุระด่วน ไม่อยากเสียเวลาเจรจานาน",
        "effect": "รีบยื่นข้อเสนอที่กระชับ ระวังอย่าดึงเกมนานเกินไป"
    },
    {
        "id": "suspicious",
        "name": "ช่างสงสัย/ระแวง",
        "icon": "fas fa-eye",
        "color": "#8b5cf6",
        "desc": "ขี้ระแวง จับตาดูทุกคำพูด ตรวจตราสินค้าละเอียด",
        "effect": "อย่าโกหกหรือตั้งราคาเกินจริง มีโอกาสตรวจของสูง"
    },
    {
        "id": "irritated",
        "name": "หงุดหงิดพร้อมเหวี่ยง",
        "icon": "fas fa-angry",
        "color": "#ef4444",
        "desc": "อารมณ์บูด หงุดหงิดง่าย พร้อมยกเลิกดีลทันที",
        "effect": "อย่ากดราคาหรือตั้งราคาสูงเกินไป ไม่งั้นจะเดินหนีทันที"
    }
]

COLLECTOR_TYPES = [
    {
        "id": "diehard",
        "name": "นักสะสมพันธุ์แท้",
        "icon": "fas fa-gem",
        "color": "#ec4899",
        "desc": "หลงใหลในของสะสม หายากแค่ไหนก็ยอมทุ่มไม่อั้น!",
        "effect": "ยอมจ่ายราคาสูงพรีเมียม (120-150%) สำหรับของแท้สภาพดี"
    },
    {
        "id": "merchant",
        "name": "พ่อค้าคนกลาง",
        "icon": "fas fa-hand-holding-usd",
        "color": "#f59e0b",
        "desc": "ซื้อไปขายต่อเก็งกำไร กดราคาต่ำสุดๆ",
        "effect": "ยากที่จะฟันกำไรสูง ต้องตั้งราคาในเกณฑ์แข่งขันได้"
    },
    {
        "id": "casual",
        "name": "นักสะสมมือสมัครเล่น",
        "icon": "fas fa-heart",
        "color": "#3b82f6",
        "desc": "ซื้อเพราะความชอบส่วนตัว ดูของไม่ขาดมากนัก",
        "effect": "โน้มน้าวง่าย หากพูดชมหรือสินค้าสภาพสวยงาม"
    },
    {
        "id": "bargain_hunter",
        "name": "นักล่าของถูก",
        "icon": "fas fa-tags",
        "color": "#14b8a6",
        "desc": "มองหาเฉพาะของราคาต่ำกว่าท้องตลาด ไม่ชอบของแพง",
        "effect": "เน้นเสนอส่วนลดหรือขายของชำรุด/ราคาประหยัด"
    },
    {
        "id": "general_user",
        "name": "ผู้ใช้งานทั่วไป",
        "icon": "fas fa-user-check",
        "color": "#64748b",
        "desc": "ซื้อไปใช้งานจริง ไม่ได้สะสม เน้นคุ้มค่าสมราคา",
        "effect": "ซื้อตามราคาตลาดมาตรฐาน ไม่ยอมจ่ายเกินมูลค่าจริง"
    }
]

PATIENCE_LEVELS = [
    {
        "id": "very_patient",
        "name": "ใจเย็นดั่งสายน้ำ",
        "icon": "fas fa-shield-heart",
        "color": "#10b981",
        "desc": "ใจเย็นมาก ต่อรองได้หลายยก ไม่หัวร้อนง่าย",
        "effect": "สามารถดึงเกมต่อราคาแบบทีละนิดเพื่อบีบราคาเป้าหมายได้"
    },
    {
        "id": "patient",
        "name": "ใจเย็นปานกลาง",
        "icon": "fas fa-hourglass-half",
        "color": "#06b6d4",
        "desc": "พร้อมเจรจา 2-3 รอบ หากข้อเสนอค่อยๆ ขยับเข้าหากัน",
        "effect": "ขยับราคาเข้าหากันทีละขั้น ดีลจะลุล่วงอย่างราบรื่น"
    },
    {
        "id": "impatient",
        "name": "ค่อนข้างใจร้อน",
        "icon": "fas fa-fire-alt",
        "color": "#f97316",
        "desc": "ไม่ชอบการต่อรองยืดเยื้อ ถ้าไม่เข้าเป้าจะเริ่มบ่น",
        "effect": "อย่าเสนอลด/เพิ่มทีละน้อยจนน่ารำคาญ รีบเข้าสู่ราคาจริง"
    },
    {
        "id": "hot_tempered",
        "name": "ใจร้อนสุดขีด",
        "icon": "fas fa-bomb",
        "color": "#ef4444",
        "desc": "ความอดทนต่ำมาก ถ้าราคาต่างกันเกินไปจะล้มโต๊ะหนีทันที",
        "effect": "ระวัง! หากเสนอราคาห่างไกลจากเป้าหมาย ดีลจะล่มทันที"
    }
]

EXPERTISE_LEVELS = [
    {
        "id": "master",
        "name": "เซียนตัวยง (ตาเหยี่ยว)",
        "icon": "fas fa-crown",
        "color": "#8b5cf6",
        "desc": "รู้มูลค่าจริงเป๊ะๆ ตรวจของปลอมออกแทบ 100% หลอกไม่ได้",
        "effect": "อย่าพยายามขายของปลอมหรือตั้งราคาโก่งเวอร์ จะโดนจับได้"
    },
    {
        "id": "knowledgeable",
        "name": "ตาถึง / มีความรู้ดี",
        "icon": "fas fa-glasses",
        "color": "#3b82f6",
        "desc": "พอรู้ราคาตลาดและจุดสังเกตสำคัญ ต่อรองอย่างมีชั้นเชิง",
        "effect": "รู้แกวการตั้งราคา ให้ใช้กลยุทธ์ราคาสมดุล"
    },
    {
        "id": "average",
        "name": "ความรู้ระดับทั่วไป",
        "icon": "fas fa-search",
        "color": "#14b8a6",
        "desc": "รู้ราคาคร่าวๆ แต่อาจไม่ชำนาญการประเมินสภาพที่แท้จริง",
        "effect": "สามารถใช้สภาพสินค้าเป็นข้ออ้างในการต่อรองราคาได้"
    },
    {
        "id": "clueless",
        "name": "มือใหม่ไร้เดียงสา",
        "icon": "fas fa-question-circle",
        "color": "#10b981",
        "desc": "ไม่รู้ราคาตลาด โดนโน้มน้าวง่าย คล้อยตามคำพูดได้ดี",
        "effect": "โอกาสทอง! สามารถกดราคาซื้อหรือเสนอขายแพงได้ง่ายมาก"
    },
    {
        "id": "ruthless",
        "name": "เขี้ยวลากดิน",
        "icon": "fas fa-skull-crossbones",
        "color": "#dc2626",
        "desc": "เคี่ยวจัด ไม่ยอมเสียเปรียบแม้แต่สลึงเดียว สู้ราคายาก",
        "effect": "อย่าคาดหวังกำไรหนา พยายามยอมรับกำไรบางๆ เพื่อปิดดีล"
    }
]

CUSTOMER_NAMES_MALE = [
    "คุณสมศักดิ์", "คุณธีรเดช", "คุณธนินท์", "คุณกฤษฎา", "คุณภาคิน",
    "คุณอานนท์", "คุณวีรชัย", "คุณณัฐพล", "คุณชัชวาล", "คุณวรภพ"
]
CUSTOMER_NAMES_FEMALE = [
    "คุณวิภาดา", "คุณณดา", "คุณพิชญา", "คุณวรัญญา", "คุณเกสร",
    "คุณมนัสนันท์", "คุณชิดชนก", "คุณพิมมาดา", "คุณอรอนงค์", "คุณศิริพร"
]

def generate_customer_profile(gender):
    is_female = "หญิง" in str(gender)
    name = random.choice(CUSTOMER_NAMES_FEMALE if is_female else CUSTOMER_NAMES_MALE)
    mood = random.choice(CUSTOMER_MOODS)
    collector = random.choice(COLLECTOR_TYPES)
    patience = random.choice(PATIENCE_LEVELS)
    expertise = random.choice(EXPERTISE_LEVELS)

    tips = []
    if collector["id"] == "diehard":
        tips.append("ยอมจ่ายราคาสูงถ้าของสวยแท้ สามารถดันราคาขายขึ้นได้")
    elif collector["id"] == "merchant":
        tips.append("พ่อค้าคนกลางจะกดราคาหนัก ควรตั้งเป้ากำไรพอประมาณ")
    elif collector["id"] == "bargain_hunter":
        tips.append("เน้นราคาประหยัด อย่าเสนอราคาสูงเกินไป")

    if patience["id"] in ("hot_tempered", "impatient"):
        tips.append("ใจร้อนมาก! อย่าต่อรองหลายรอบหรือเสนอราคาห่างไกลเกินไป")
    elif patience["id"] == "very_patient":
        tips.append("ใจเย็นมาก ค่อยๆ ตะล่อมต่อรองทีละสเต็ปเพื่อทำกำไรสูงสุด")

    if expertise["id"] == "clueless":
        tips.append("ไม่ค่อยรู้ราคาตลาด คุณกุมความได้เปรียบในการต่อรอง!")
    elif expertise["id"] in ("master", "ruthless"):
        tips.append("เชี่ยวชาญ/เคี่ยวจัด อย่าพยายามเอาเปรียบหรือขายของปลอม")

    if not tips:
        tips.append("เจรจาตามกลไกราคาตลาด ค่อยๆ ขยับราคาเข้าหากัน")

    tactical_tip = " • ".join(tips)

    return {
        "name": name,
        "gender": "หญิง" if is_female else "ชาย",
        "mood": mood,
        "collector": collector,
        "patience": patience,
        "expertise": expertise,
        "tactical_tip": tactical_tip
    }

SELLER_PERSONAS = [
    "คุณเป็นคนร้อนเงินมากๆ รีบใช้เงินสุดๆ ยอมลดราคาให้เยอะขอแค่ได้เงินสดกลับไป",
    "คุณเป็นคนเขี้ยวลากดิน ต่อราคายากมาก รู้มูลค่าของจริงทุกบาททุกสตางค์",
    "คุณเป็นคนซื่อๆ ไม่ค่อยรู้ราคาตลาด โดนหลอกง่าย มักจะคล้อยตามถ้าเจ้าของร้านพูดดีๆ",
    "คุณเป็นนักสะสมที่หวงของสุดๆ จะยอมขายต่อเมื่อได้ราคาสูงเท่านั้น"
]

BUYER_PERSONAS = [
    "คุณเป็นเศรษฐีเงินเหลือ ชอบของชิ้นนี้มาก เสนอราคาสูงๆ ได้ไม่แคร์เงิน",
    "คุณเป็นพ่อค้าคนกลาง จะซื้อของไปเก็งกำไรต่อ กดราคาสุดๆ จะซื้อเมื่อได้ราคาถูกมากเท่านั้น",
    "คุณเป็นคนธรรมดาทั่วไป อยากได้ของชิ้นนี้ไปใช้งาน มีงบจำกัด ถ้าแพงไปก็ไม่ซื้อ"
]

EVENTS = [
    {"type": "bad", "name": "โจรปล้นร้าน!", "desc": "มีโจรแอบงัดร้านตอนกลางคืน กวาดเงินในลิ้นชักไปบางส่วน...", "money_mod": -5000, "rep_mod": -2},
    {"type": "bad", "name": "ค่าคุ้มครอง", "desc": "นักเลงคุมถิ่นมาขอเก็บค่าดูแลร้าน", "money_mod": -2000, "rep_mod": 0},
    {"type": "good", "name": "อินฟลูเอนเซอร์มารีวิว", "desc": "มีคนดังมาถ่ายคลิปลง TikTok คนรู้จักร้านมากขึ้น!", "money_mod": 0, "rep_mod": 15},
    {"type": "good", "name": "เจอเงินตก", "desc": "คุณเจอกระเป๋าตังค์ตกอยู่หน้าร้านและไม่มีใครมารับคืน", "money_mod": 3000, "rep_mod": 0},
    {"type": "neutral", "name": "คืนที่เงียบสงบ", "desc": "ไม่มีอะไรเกิดขึ้นเมื่อคืนนี้ ได้นอนหลับเต็มอิ่ม", "money_mod": 0, "rep_mod": 0},
    {"type": "good", "name": "ลูกค้าเก่าแนะนำ", "desc": "ลูกค้าเก่าประทับใจ เลยไปบอกต่อเพื่อนๆ", "money_mod": 0, "rep_mod": 5}
]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/favicon.ico")
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route("/sw.js")
def serve_sw():
    resp = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

@app.route("/api/register", methods=["POST"])
def register():
    data = req_json()
    nickname = str(data.get("nickname") or "").strip()[:30]
    pin = str(data.get("pin") or "").strip()

    if not nickname:
        return jsonify({"status": "error", "message": "กรุณาใส่ชื่อเล่น"})
    if len(pin) != 6 or not pin.isdigit():
        return jsonify({"status": "error", "message": "กรุณาตั้งรหัส PIN ให้ครบ 6 หลัก (ตัวเลขเท่านั้น)"})

    if db.get_shop(pin):
        return jsonify({"status": "error", "message": "รหัส PIN นี้ถูกใช้งานแล้ว กรุณาใช้รหัสอื่น"})

    db.create_shop(pin, nickname)
    return jsonify({"status": "ok", "pin": pin, "nickname": nickname, "save": build_save(pin)})

@app.route("/api/login", methods=["POST"])
def login():
    data = req_json()
    pin = str(data.get("pin") or "").strip()
    if not pin:
        return jsonify({"status": "error", "message": "PIN is required"})

    shop = ensure_shop(pin, data.get("save"))
    if shop:
        return jsonify({"status": "ok", "nickname": shop.get("nickname"), "pin": pin, "save": build_save(pin)})
    return jsonify({"status": "error", "message": "ไม่พบ PIN นี้ในระบบ"})

@app.route("/api/load_game", methods=["GET", "POST"])
def load_game():
    if request.method == "POST":
        data = req_json()
        pin = data.get("pin")
        save = data.get("save")
    else:
        pin = request.args.get("pin")
        save = None
    if not pin:
        return jsonify({"status": "error", "message": "PIN is required"})

    shop = ensure_shop(pin, save)
    if not shop:
        return shop_not_found()

    inv = db.get_inventory(pin)
    is_bankrupt = bool(int(shop.get("loan_remaining") or 0) > 0 and (shop.get("reputation") if shop.get("reputation") is not None else 10) <= 0)

    staff_data = get_staff_data(shop)
    levels = sync_staff_levels(staff_data.get("hired", []))
    total_wages = calculate_hired_wages(staff_data.get("hired", []))

    return jsonify({
        "status": "ok",
        "bankrupt": is_bankrupt,
        "money": shop["money"],
        "day": shop["day"],
        "reputation": shop.get("reputation") if shop.get("reputation") is not None else 10,
        "customers_left": shop.get("customers_left") if shop.get("customers_left") is not None else 5,
        "nickname": shop.get("nickname") or "Unknown",
        "loan": {
            "remaining": int(shop.get("loan_remaining") or 0),
            "daily": int(shop.get("loan_daily") or 0),
            "days_left": int(shop.get("loan_days_left") or 0),
            "principal": int(shop.get("loan_principal") or 0)
        },
        "staff": {
            "guard": levels["guard_level"],
            "repair": levels["repair_level"],
            "forger": levels["forger_level"],
            "ad": levels["ad_level"],
            "expert": levels["expert_level"],
            "hired": staff_data.get("hired", []),
            "candidates": staff_data.get("candidates", []),
            "max_slots": MAX_STAFF_SLOTS,
            "total_wages": total_wages
        },
        "inventory": inv,
        "save": build_save(pin)
    })

@app.route("/api/end_day", methods=["POST"])
def end_day():
    data = req_json()
    pin = data.get("pin")

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    # กันการกดซ้ำ/ส่งคำขอซ้ำ (เช่นเน็ต iPad กระตุก) ทำให้ข้ามวันสองรอบ
    if (shop.get("customers_left") or 0) > 0:
        return jsonify({"status": "error", "message": "ยังมีลูกค้ารอคิวอยู่", "save": build_save(pin)})

    rent = 1000

    staff_data = get_staff_data(shop)
    hired = staff_data.get("hired", [])
    levels = sync_staff_levels(hired)
    g_level = levels["guard_level"]
    r_level = levels["repair_level"]
    f_level = levels["forger_level"]
    ad_level = levels["ad_level"]
    expert_level = levels["expert_level"]

    wages = calculate_hired_wages(hired)
    total_expenses = rent + wages
    event = dict(random.choice(EVENTS))
    event_logs = []

    # Guard protection
    if event["name"] == "โจรปล้นร้าน!" and g_level > 0:
        reward = [0, 1000, 2000, 5000][g_level]
        event = {"type": "good", "name": "ยามจับโจรได้!", "desc": f"โจรพยายามงัดร้าน แต่ยามที่คุณจ้างไว้ (Lv.{g_level}) จับได้แถมได้รางวัลนำจับ!", "money_mod": reward, "rep_mod": 5}
    elif event["name"] == "ค่าคุ้มครอง" and g_level >= 2:
        event = {"type": "neutral", "name": "ยามไล่นักเลง", "desc": f"นักเลงมาเก็บค่าคุ้มครอง แต่เจอยามล่ำบึ้ก (Lv.{g_level}) ไล่ตะเพิดกลับไป", "money_mod": 0, "rep_mod": 2}

    new_money = shop["money"] - total_expenses + event["money_mod"]
    new_rep = (shop.get("reputation") or 0) + event["rep_mod"]

    # --- ระบบหักชำระค่างวดเงินกู้ (Loan Installment) ---
    loan_remaining = int(shop.get("loan_remaining") or 0)
    loan_daily = int(shop.get("loan_daily") or 0)
    loan_days_left = int(shop.get("loan_days_left") or 0)
    loan_paid = 0

    if loan_remaining > 0:
        installment = min(loan_daily if loan_daily > 0 else loan_remaining, loan_remaining)
        if new_money >= installment:
            new_money -= installment
            loan_paid = installment
            loan_remaining -= installment
            loan_days_left = max(0, loan_days_left - 1)
            total_expenses += installment
            if loan_remaining <= 0:
                loan_remaining = 0
                loan_daily = 0
                loan_days_left = 0
                event_logs.append(f"💳 ชำระค่างวดเงินกู้ ฿{installment:,} ปิดยอดหนี้สินทั้งหมดเรียบร้อยแล้ว! 🎉")
            else:
                event_logs.append(f"💳 ชำระค่างวดเงินกู้ประจำวัน ฿{installment:,} (หนี้คงเหลือ ฿{loan_remaining:,}, เหลือ {loan_days_left} งวด)")
        else:
            # เงินไม่พอจ่ายค่างวด -> ดอกเบี้ยปรับ 10%
            penalty = int(installment * 0.10)
            loan_remaining += penalty
            rep_loss = 5
            if g_level >= 2:
                rep_loss = 2
                event_logs.append(f"⚠️ เงินไม่พอจ่ายค่างวด ฿{installment:,}! ดอกเบี้ยปรับ +฿{penalty:,} แต่ยาม (Lv.{g_level}) ช่วยคุ้มกัน เสียชื่อเสียงเพียง -{rep_loss}")
            else:
                event_logs.append(f"⚠️ เบี้ยวหนี้! เงินไม่พอจ่ายค่างวด ฿{installment:,} ถูกคิดดอกเบี้ยปรับเพิ่ม +฿{penalty:,} และเสียชื่อเสียง -{rep_loss}!")
            new_rep = max(0, new_rep - rep_loss)

    # Advertiser passive buff
    if ad_level > 0:
        rep_buff = [0, 1, 2, 4][ad_level]
        new_rep += rep_buff
        event_logs.append(f"📢 นักโฆษณา (Lv.{ad_level}) ช่วยโปรโมทร้าน ได้ชื่อเสียงเพิ่ม +{rep_buff}")

    # Repairman passive buff (ซ่อมแซมสินค้าที่สภาพไม่สมบูรณ์ หรือพัง ในคลัง)
    if r_level > 0:
        all_inv = db.get_inventory(pin)
        repair_candidates = [
            it for it in all_inv 
            if (it.get("condition") is not None and it.get("condition", 100) < 100) or str(it.get("name", "")).startswith("[พัง] ")
        ]

        items_to_fix = 0
        if r_level == 1 and random.random() > 0.4: items_to_fix = 1
        elif r_level == 2: items_to_fix = 2
        elif r_level == 3: items_to_fix = 999

        fixed_count = 0
        for b_item in repair_candidates[:items_to_fix]:
            old_cond = int(b_item.get("condition") if b_item.get("condition") is not None else 50)
            old_name = b_item.get("name", "")
            new_name = old_name.replace("[พัง] ", "", 1)
            old_val = b_item.get("value", 1000)

            # ช่างซ่อมฟื้นฟูสภาพเป็น 100% ใหม่กริบ
            new_cond = 100
            cond_info = get_condition_info(old_cond)
            mult = cond_info["multiplier"] if cond_info["multiplier"] > 0 else 0.5
            new_val = int(old_val / mult)
            if str(old_name).startswith("[พัง] "):
                new_val = max(new_val, int(old_val / 0.3))

            db.update_inventory_item(b_item["id"], name=new_name, value=new_val, true_value=new_val, condition=new_cond)
            fixed_count += 1
            event_logs.append(f"🔧 ช่างซ่อม (Lv.{r_level}) ซ่อมแซม '{new_name}' จากสภาพ {old_cond}% สู่ 100% (ใหม่กริบ)! มูลค่าฟื้นฟูเป็น ฿{new_val:,}")

        if fixed_count == 0 and repair_candidates and r_level == 1:
            event_logs.append(f"🔧 ช่างซ่อม (Lv.1) กำลังจัดเตรียมอะไหล่ ยังซ่อมของไม่เสร็จในคืนนี้")

    # Expert Appraiser passive buff
    if expert_level >= 3:
        event_logs.append(f"🔍 ผู้เชี่ยวชาญ (Lv.{expert_level}) ตรวจตราสินค้าในร้านอย่างละเอียด")

    # Bankruptcy check: หากชื่อเสียงเหลือ 0 จากการเบี้ยวหนี้ หรือชื่อเสียงหมดร้าน
    is_bankrupt = False
    bankrupt_reason = ""
    if new_rep <= 0:
        is_bankrupt = True
        new_rep = 0
        if loan_remaining > 0:
            bankrupt_reason = "คุณเบี้ยวหนี้จนชื่อเสียงร้านลดลงเหลือ 0! เจ้าหนี้นอกระบบบุกมายึดร้านและกวาดทรัพย์สินทั้งหมด กิจการของคุณล้มละลาย!"
            event_logs.append("💀 ล้มละลาย! ชื่อเสียงร้านเหลือ 0 จากการผิดนัดชำระหนี้เงินกู้")
        else:
            bankrupt_reason = "ชื่อเสียงร้านของคุณตกต่ำจนเหลือ 0! ไม่มีลูกค้าคนไหนกล้าเข้าร้านอีกต่อไป กิจการของคุณล้มละลาย!"
            event_logs.append("💀 ล้มละลาย! ชื่อเสียงร้านเหลือ 0")

    new_day = shop["day"] + 1
    # Queue size based on reputation (min 3, max 20)
    new_queue = min(20, max(3, 3 + (new_rep // 10)))

    # Refresh candidates for next day
    staff_data["candidates"] = [generate_candidate() for _ in range(3)]

    db.update_shop(pin, money=new_money, day=new_day, reputation=new_rep, customers_left=new_queue, active_customer=None,
                   loan_remaining=loan_remaining, loan_daily=loan_daily, loan_days_left=loan_days_left,
                   guard_level=levels["guard_level"], repair_level=levels["repair_level"],
                   forger_level=levels["forger_level"], ad_level=levels["ad_level"],
                   staff_data=json.dumps(staff_data, ensure_ascii=False))
    bump_version(pin)

    return jsonify({
        "status": "ok",
        "bankrupt": is_bankrupt,
        "bankrupt_reason": bankrupt_reason,
        "expenses": total_expenses,
        "rent": rent,
        "wages": wages,
        "loan_paid": loan_paid,
        "loan_remaining": loan_remaining,
        "loan_days_left": loan_days_left,
        "event": event,
        "event_logs": event_logs,
        "new_day": new_day,
        "new_queue": new_queue,
        "new_money": new_money,
        "new_rep": new_rep,
        "save": build_save(pin)
    })

@app.route("/api/pay_loan", methods=["POST"])
def pay_loan():
    data = req_json()
    pin = data.get("pin")
    amount = int(data.get("amount") or 0)

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    loan_remaining = int(shop.get("loan_remaining") or 0)
    if loan_remaining <= 0:
        return jsonify({"status": "error", "message": "คุณไม่มีหนี้สินที่ต้องชำระ"})

    if amount <= 0:
        return jsonify({"status": "error", "message": "จำนวนเงินไม่ถูกต้อง"})

    pay_amount = min(amount, loan_remaining, shop["money"])
    if pay_amount <= 0:
        return jsonify({"status": "error", "message": "เงินในกระเป๋าของคุณไม่เพียงพอ"})

    new_money = shop["money"] - pay_amount
    new_remaining = loan_remaining - pay_amount
    new_daily = int(shop.get("loan_daily") or 0)
    new_days_left = int(shop.get("loan_days_left") or 0)

    if new_remaining <= 0:
        new_remaining = 0
        new_daily = 0
        new_days_left = 0
        msg = f"ชำระเงิน ฿{pay_amount:,} ปิดยอดหนี้สินทั้งหมดสำเร็จแล้ว! 🎉"
    else:
        new_daily = min(new_daily, new_remaining)
        msg = f"ชำระหนี้ ฿{pay_amount:,} สำเร็จ! ยอดหนี้คงเหลือ ฿{new_remaining:,}"

    db.update_shop(pin, money=new_money, loan_remaining=new_remaining, loan_daily=new_daily, loan_days_left=new_days_left)
    bump_version(pin)

    return jsonify({
        "status": "ok",
        "message": msg,
        "paid": pay_amount,
        "new_money": new_money,
        "loan": {
            "remaining": new_remaining,
            "daily": new_daily,
            "days_left": new_days_left
        },
        "save": build_save(pin)
    })

@app.route("/api/restart_game", methods=["POST"])
def restart_game():
    data = req_json()
    pin = data.get("pin")

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    fresh_staff = {
        "hired": [],
        "candidates": [generate_candidate() for _ in range(3)],
        "max_slots": MAX_STAFF_SLOTS
    }
    db.update_shop(pin, money=100000, day=1, reputation=10, customers_left=5,
                   guard_level=0, repair_level=0, forger_level=0, ad_level=0,
                   staff_data=json.dumps(fresh_staff, ensure_ascii=False),
                   active_customer=None,
                   loan_remaining=0, loan_daily=0, loan_days_left=0, loan_principal=0)
    db.clear_inventory(pin)
    bump_version(pin)
    current_games.pop(pin, None)

    return jsonify({
        "status": "ok",
        "message": "รีเซ็ตร้านค้าเพื่อเริ่มต้นใหม่เรียบร้อยแล้ว",
        "save": build_save(pin)
    })

@app.route("/api/hire_staff", methods=["POST"])
def hire_staff():
    data = req_json()
    pin = data.get("pin")
    cand_id = data.get("candidate_id")

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    staff_data = get_staff_data(shop)
    hired = staff_data.get("hired", [])
    candidates = staff_data.get("candidates", [])

    if len(hired) >= MAX_STAFF_SLOTS:
        return jsonify({"status": "error", "message": f"พนักงานเต็มโควตาแล้ว (สูงสุด {MAX_STAFF_SLOTS} คน) กรุณาเลิกจ้างพนักงานคนเดิมก่อน"})

    cand = next((c for c in candidates if c["id"] == cand_id), None)
    if not cand:
        return jsonify({"status": "error", "message": "ไม่พบผู้สมัครงานคนนี้"})

    cost = cand.get("cost", 0)
    if shop["money"] < cost:
        return jsonify({"status": "error", "message": f"เงินไม่พอค่าเซ็นสัญญา (ต้องการ ฿{cost:,})"})

    new_money = shop["money"] - cost
    candidates = [c for c in candidates if c["id"] != cand_id]
    hired_member = dict(cand)
    hired_member["hired_at_day"] = shop.get("day", 1)
    hired.append(hired_member)

    staff_data["hired"] = hired
    staff_data["candidates"] = candidates
    levels = sync_staff_levels(hired)

    db.update_shop(pin, money=new_money,
                   guard_level=levels["guard_level"], repair_level=levels["repair_level"],
                   forger_level=levels["forger_level"], ad_level=levels["ad_level"],
                   staff_data=json.dumps(staff_data, ensure_ascii=False))
    bump_version(pin)

    return jsonify({
        "status": "ok",
        "message": f"จ้าง {cand['name']} ({cand['role_title']} {cand['stars']}⭐) เข้าทำงานเรียบร้อยแล้ว!",
        "new_money": new_money,
        "staff": {
            "guard": levels["guard_level"],
            "repair": levels["repair_level"],
            "forger": levels["forger_level"],
            "ad": levels["ad_level"],
            "expert": levels["expert_level"],
            "hired": hired,
            "candidates": candidates,
            "max_slots": MAX_STAFF_SLOTS,
            "total_wages": calculate_hired_wages(hired)
        },
        "save": build_save(pin)
    })

@app.route("/api/fire_staff", methods=["POST"])
def fire_staff():
    data = req_json()
    pin = data.get("pin")
    staff_id = data.get("staff_id")

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    staff_data = get_staff_data(shop)
    hired = staff_data.get("hired", [])

    fired = next((s for s in hired if s["id"] == staff_id), None)
    if not fired:
        return jsonify({"status": "error", "message": "ไม่พบพนักงานคนนี้ในร้าน"})

    hired = [s for s in hired if s["id"] != staff_id]
    staff_data["hired"] = hired
    levels = sync_staff_levels(hired)

    db.update_shop(pin,
                   guard_level=levels["guard_level"], repair_level=levels["repair_level"],
                   forger_level=levels["forger_level"], ad_level=levels["ad_level"],
                   staff_data=json.dumps(staff_data, ensure_ascii=False))
    bump_version(pin)

    return jsonify({
        "status": "ok",
        "message": f"เลิกจ้าง {fired['name']} เรียบร้อยแล้ว (เหลือช่องว่าง {MAX_STAFF_SLOTS - len(hired)} ช่อง)",
        "staff": {
            "guard": levels["guard_level"],
            "repair": levels["repair_level"],
            "forger": levels["forger_level"],
            "ad": levels["ad_level"],
            "expert": levels["expert_level"],
            "hired": hired,
            "candidates": staff_data.get("candidates", []),
            "max_slots": MAX_STAFF_SLOTS,
            "total_wages": calculate_hired_wages(hired)
        },
        "save": build_save(pin)
    })

@app.route("/api/refresh_candidates", methods=["POST"])
def refresh_candidates():
    data = req_json()
    pin = data.get("pin")

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    REFRESH_COST = 500
    if shop["money"] < REFRESH_COST:
        return jsonify({"status": "error", "message": f"เงินไม่พอค่าสุ่มหาผู้สมัครใหม่ (ต้องการ ฿{REFRESH_COST:,})"})

    new_money = shop["money"] - REFRESH_COST
    staff_data = get_staff_data(shop)
    staff_data["candidates"] = [generate_candidate() for _ in range(3)]

    db.update_shop(pin, money=new_money, staff_data=json.dumps(staff_data, ensure_ascii=False))
    bump_version(pin)

    levels = sync_staff_levels(staff_data.get("hired", []))
    return jsonify({
        "status": "ok",
        "message": "สุ่มค้นหาผู้สมัครงานใหม่เรียบร้อยแล้ว!",
        "money": new_money,
        "new_money": new_money,
        "candidates": staff_data["candidates"],
        "staff": {
            "guard": levels["guard_level"],
            "repair": levels["repair_level"],
            "forger": levels["forger_level"],
            "ad": levels["ad_level"],
            "expert": levels["expert_level"],
            "hired": staff_data.get("hired", []),
            "candidates": staff_data["candidates"],
            "max_slots": MAX_STAFF_SLOTS,
            "total_wages": calculate_hired_wages(staff_data.get("hired", []))
        },
        "save": build_save(pin)
    })

@app.route("/api/upgrade_staff", methods=["POST"])
def upgrade_staff():
    data = req_json()
    pin = data.get("pin")
    role = data.get("role")

    valid_roles = ["guard", "repair", "forger", "ad", "expert"]
    if role not in valid_roles:
        return jsonify({"status": "error", "message": "Role not found"})

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    staff_data = get_staff_data(shop)
    hired = staff_data.get("hired", [])
    
    # Check if this role is currently hired
    matching = next((s for s in hired if s["role"] == role), None)
    current_lvl = matching["stars"] if matching else 0

    if current_lvl >= 3:
        return jsonify({"status": "error", "message": "ระดับสูงสุดแล้ว (3 ดาว)"})

    costs_map = {
        "guard": [5000, 12000, 25000],
        "repair": [8000, 18000, 35000],
        "forger": [15000, 30000, 50000],
        "ad": [8000, 20000, 40000],
        "expert": [6000, 15000, 30000]
    }
    cost = costs_map[role][current_lvl]

    if shop["money"] < cost:
        return jsonify({"status": "error", "message": f"เงินไม่พอ (ต้องการ {cost:,} บาท)"})

    if not matching:
        if len(hired) >= MAX_STAFF_SLOTS:
            return jsonify({"status": "error", "message": f"พนักงานเต็มโควตาแล้ว (สูงสุด {MAX_STAFF_SLOTS} คน) กรุณาเลิกจ้างพนักงานคนเดิมก่อน"})
        info = STAFF_ROLE_INFO[role]
        hired.append({
            "id": f"staff_{role}_1",
            "name": f"{info['title']}ประจำร้าน",
            "role": role,
            "role_title": info["title"],
            "icon": info["icon"],
            "stars": 1,
            "wage": info["wages"][1],
            "cost": info["costs"][1],
            "desc": info["desc"][1]
        })
    else:
        new_stars = current_lvl + 1
        info = STAFF_ROLE_INFO[role]
        matching["stars"] = new_stars
        matching["wage"] = info["wages"][new_stars]
        matching["cost"] = info["costs"][new_stars]
        matching["desc"] = info["desc"][new_stars]

    staff_data["hired"] = hired
    levels = sync_staff_levels(hired)

    db.update_shop(pin, money=shop["money"] - cost,
                   guard_level=levels["guard_level"], repair_level=levels["repair_level"],
                   forger_level=levels["forger_level"], ad_level=levels["ad_level"],
                   staff_data=json.dumps(staff_data, ensure_ascii=False))
    bump_version(pin)

    return jsonify({"status": "ok", "message": f"อัปเกรดสำเร็จเป็นระดับ {current_lvl + 1} ดาว!", "save": build_save(pin)})

@app.route("/api/repair_item", methods=["POST"])
def repair_item():
    data = req_json()
    pin = data.get("pin")
    item_id = data.get("item_id")

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    inventory = db.get_inventory(pin)
    item = next((it for it in inventory if it["id"] == item_id), None)
    if not item:
        return jsonify({"status": "error", "message": "ไม่พบสินค้าชิ้นนี้ในคลัง"})

    current_cond = int(item.get("condition") if item.get("condition") is not None else 100)
    is_broken = str(item.get("name", "")).startswith("[พัง] ")
    if current_cond >= 100 and not is_broken:
        return jsonify({"status": "error", "message": "สินค้านี้อยู่ในสภาพสมบูรณ์ 100% อยู่แล้ว ไม่ต้องซ่อมแซม"})

    staff_data = get_staff_data(shop)
    levels = sync_staff_levels(staff_data.get("hired", []))
    r_level = levels["repair_level"]

    # ค่าซ่อมแซม:
    # ช่าง Lv.3 = ฟรี ฿0
    # ช่าง Lv.2 = ฿300
    # ช่าง Lv.1 = ฿800
    # ไม่มีช่าง = ส่งร้านนอก ฿2,000
    costs = {3: 0, 2: 300, 1: 800, 0: 2000}
    cost = costs.get(r_level, 2000)

    if shop["money"] < cost:
        return jsonify({"status": "error", "message": f"เงินไม่พอสำหรับค่าซ่อม (ต้องการ ฿{cost:,})"})

    new_money = shop["money"] - cost
    old_name = item["name"]
    new_name = old_name.replace("[พัง] ", "", 1)
    old_val = item.get("value", 1000)
    cond_info = get_condition_info(current_cond)
    mult = cond_info["multiplier"] if cond_info["multiplier"] > 0 else 0.5
    new_val = int(old_val / mult)
    if is_broken:
        new_val = max(new_val, int(old_val / 0.3))

    db.update_shop(pin, money=new_money)
    db.update_inventory_item(item_id, name=new_name, value=new_val, true_value=new_val, condition=100)
    bump_version(pin)

    return jsonify({
        "status": "ok",
        "message": f"ซ่อมแซม '{new_name}' เรียบร้อยแล้ว! สภาพฟื้นฟูเป็น 100% ใหม่กริบ (มูลค่าเพิ่มเป็น ฿{new_val:,})",
        "new_money": new_money,
        "cost": cost,
        "inventory": db.get_inventory(pin),
        "save": build_save(pin)
    })

def load_session(pin, token):
    """ดึงสถานะลูกค้าคนปัจจุบัน: จาก token ของ Client ก่อน (ทนต่อการรีสตาร์ท/หลาย instance) แล้วค่อย cache"""
    game = read_token(token)
    if game and game.get("t") == "game" and game.get("pin") == pin:
        return game
    return current_games.get(pin)

def save_session(pin, game):
    current_games[pin] = game
    return make_token(game)

@app.route("/api/new_customer", methods=["POST"])
def new_customer():
    data = req_json()
    pin = data.get("pin")

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()

    if (shop.get("customers_left") or 0) <= 0:
        return jsonify({"status": "end_of_day", "save": build_save(pin)})

    inventory = db.get_inventory(pin)

    cid = secrets.token_hex(8)
    game = {
        "t": "game",
        "pin": pin,
        "cid": cid,
        "history": [],
        "item": None,
        "transaction_type": "sell",
        "value": 0,
        "true_value": 0,
        "min_price": 0,
        "max_price": 0,
        "is_fake": False,
        "appraised": False,
    }

    avatar_data = random.choice(AVATARS)
    avatar = avatar_data["url"]
    gender = avatar_data["gender"]
    customer = generate_customer_profile(gender)

    # สลับและรักษาสมดุลระหว่างคนมาขายของ (sell) กับคนมาซื้อของ (buy) ให้เท่าๆ กัน 50:50
    last_trans = data.get("last_type") or current_games.get(f"{pin}_last_trans")
    is_buyer = False
    if len(inventory) > 0:
        if last_trans == "buy":
            # คนก่อนหน้ามาขอซื้อของไปแล้ว -> สลับให้คนถัดไปเอาของมาขายให้ร้าน (โอกาส 80% เป็นคนมาขาย)
            is_buyer = (random.random() < 0.20)
        elif last_trans == "sell":
            # คนก่อนหน้ามาขายของให้ร้าน -> สลับให้คนถัดไปมาขอซื้อของในร้าน (โอกาส 80% เป็นคนมาซื้อ)
            is_buyer = (random.random() < 0.80)
        else:
            is_buyer = (random.random() < 0.50)

    current_games[f"{pin}_last_trans"] = "buy" if is_buyer else "sell"

    if is_buyer:
        game["transaction_type"] = "buy"
        item = random.choice(inventory)
        persona = random.choice(BUYER_PERSONAS)

        forger_level = lvl(shop, "forger_level")
        base_price_for_buyer = item.get("true_value") or item["value"]

        if base_price_for_buyer < item["value"]: # It's a fake item
            success_chance = [0, 0.3, 0.7, 1.0][forger_level]
            if random.random() < success_chance:
                # Forger fools buyer
                base_price_for_buyer = item["value"]

        # คำนวณราคาสูงสุดที่ลูกค้ายอมจ่าย
        if "เศรษฐี" in persona:
            max_price = int(base_price_for_buyer * 1.5)
        elif "พ่อค้าคนกลาง" in persona:
            max_price = int(base_price_for_buyer * 0.7)
        else:
            max_price = int(base_price_for_buyer * 1.1)

        item_cond = int(item.get("condition") if item.get("condition") is not None else 100)
        cond_info = get_condition_info(item_cond)
        item["condition"] = item_cond
        item["condition_name"] = cond_info["name"]
        item["condition_color"] = cond_info["color"]
        item["condition_pct"] = item_cond

        game["item"] = item
        game["value"] = item["value"]
        game["true_value"] = base_price_for_buyer
        game["max_price"] = max_price
        game["gender"] = gender
        game["customer"] = customer
        game["persona_desc"] = persona
        game["is_fake"] = bool(item.get("true_value", item["value"]) < item["value"])
        game["last_bot_price"] = 0

        system_prompt = f"""คุณกำลังเล่นเกม Roleplay: คุณคือลูกค้าที่เดินเข้ามาในร้านขายของมือสองเพื่อ 'ขอซื้อของ'
ชื่อของคุณ: {customer['name']} | เพศของคุณ: {gender}
อารมณ์: {customer['mood']['name']} ({customer['mood']['desc']})
ประเภทนักสะสม: {customer['collector']['name']} ({customer['collector']['desc']})
ความอดทน: {customer['patience']['name']} ({customer['patience']['desc']})
ความเชี่ยวชาญ: {customer['expertise']['name']} ({customer['expertise']['desc']})
ของที่คุณสนใจจะซื้อคือ: {item['name']} (สภาพ: {cond_info['name']})
ราคาสูงสุดที่คุณยอมจ่ายคือ: {max_price} บาท (ห้ามบอกตัวเลขนี้ให้เจ้าของร้านรู้เด็ดขาด!)

กฎการตอบ:
1. เล่นตามนิสัยอย่างเคร่งครัด พยายามต่อราคาให้ถูกที่สุดก่อน
2. ถ้าเจ้าของร้านเสนอราคามา และคุณพอใจ (ราคานั้น <= {max_price}) ให้คุณตอบตกลงซื้อ โดยพิมพ์คำว่า [BUY_ACCEPTED:ราคาที่เจ้าของร้านเสนอ] ท้ายประโยค (ตัวเลขล้วน ไม่ต้องมีลูกน้ำ)
3. คำเตือน: ห้ามพิมพ์ [BUY_ACCEPTED] เด็ดขาด ถ้าคุณกำลังขอต่อราคา หรือเป็นฝ่ายเสนอราคาใหม่ คุณจะพิมพ์แท็กนี้ได้ก็ต่อเมื่อคุณ 'ยอมรับ' ราคาที่เจ้าของร้านเสนอมาล่าสุดเท่านั้น!
4. ถ้าแพงเกินไปรับไม่ได้จริงๆ ให้ด่า/บ่น แล้วใส่ [DEAL_REJECTED] ท้ายประโยค
5. เริ่มทักทาย ถามราคาของชิ้นนี้ และเสนอราคาที่คุณอยากซื้อทันที
6. ***สำคัญมาก: ตอบให้สั้นกระชับที่สุด ไม่เกิน 1-2 ประโยค พูดเหมือนคนคุยกันจริงๆ (ห้ามพิมพ์ยาวเวิ่นเว้อ)***
"""
        initial_user_msg = "สวัสดีครับ สนใจรับของชิ้นไหนในร้านดีครับ?"
    else:
        game["transaction_type"] = "sell"
        item = dict(random.choice(ITEMS))

        is_fake = random.random() < 0.25 # โอกาส 25% เป็นของปลอม
        is_broken = random.random() < 0.20 # โอกาส 20% ของพัง

        if is_broken:
            condition = random.randint(15, 35)
        else:
            condition = random.choices(
                [random.randint(95, 100), random.randint(80, 94), random.randint(65, 79), random.randint(45, 64)],
                weights=[40, 30, 20, 10]
            )[0]

        cond_info = get_condition_info(condition)
        item["condition"] = condition
        item["condition_name"] = cond_info["name"]
        item["condition_color"] = cond_info["color"]
        item["condition_pct"] = condition
        item["original_value"] = item["value"]

        if condition < 95:
            item["value"] = int(item["value"] * cond_info["multiplier"])

        if is_broken:
            item["name"] = f"[พัง] {item['name']}"
            item["value"] = int(item["value"] * 0.4)
            true_value = item["value"]
            persona = random.choice(SELLER_PERSONAS)
            persona_desc = persona
            min_price = int(item["value"] * 0.5)
        elif is_fake:
            true_value = int(item["value"] * 0.1) # มูลค่าจริงเหลือแค่ 10%
            min_price = int(true_value * 0.5) # ยอมขายถูกมากเพราะรู้ว่าเป็นของปลอม
            persona_desc = "คุณเป็นมิจฉาชีพที่นำของ 'ปลอม' มาหลอกขายให้เนียนที่สุด ห้ามให้เจ้าของร้านรู้เด็ดขาด พยายามหลอกล่อให้เขาซื้อในราคาสูง"
        else:
            true_value = item["value"]
            persona = random.choice(SELLER_PERSONAS)
            persona_desc = persona

            if "ร้อนเงิน" in persona or "ซื่อๆ" in persona:
                min_price = int(item["value"] * 0.4)
            elif "เขี้ยวลากดิน" in persona:
                min_price = int(item["value"] * 0.8)
            else:
                min_price = int(item["value"] * 0.6)

        game["item"] = item
        game["value"] = item["value"] # ราคากลางที่โชว์ให้ผู้เล่นเห็น
        game["true_value"] = true_value # ราคาที่แท้จริง (อาจจะปลอม)
        game["min_price"] = min_price
        game["is_fake"] = is_fake
        game["appraised"] = False
        game["gender"] = gender
        game["customer"] = customer
        game["persona_desc"] = persona_desc
        game["last_bot_price"] = 0

        system_prompt = f"""คุณกำลังเล่นเกม Roleplay: คุณคือลูกค้าที่นำของมา 'ขายให้' โรงรับจำนำ
ชื่อของคุณ: {customer['name']} | เพศของคุณ: {gender}
อารมณ์: {customer['mood']['name']} ({customer['mood']['desc']})
ประเภทนักสะสม: {customer['collector']['name']} ({customer['collector']['desc']})
ความอดทน: {customer['patience']['name']} ({customer['patience']['desc']})
ความเชี่ยวชาญ: {customer['expertise']['name']} ({customer['expertise']['desc']})
ของที่คุณนำมาขายคือ: {item['name']} (สภาพ: {cond_info['name']})
ลักษณะนิสัย: {persona_desc}
ราคาต่ำสุดที่คุณยอมรับได้คือ: {min_price} บาท (ห้ามบอกให้เจ้าของร้านรู้!)

กฎการตอบ:
1. เล่นตามนิสัย พยายามเสนอราคาสูงๆ ก่อน
2. ถ้าเจ้าของร้านเสนอราคามา และคุณพอใจ (ราคานั้น >= {min_price}) ให้คุณตกลงขาย พิมพ์ [SELL_ACCEPTED:ราคาที่เจ้าของร้านเสนอ] ท้ายประโยค (ตัวเลขล้วน ไม่ต้องมีลูกน้ำ)
3. คำเตือน: ห้ามพิมพ์ [SELL_ACCEPTED] เด็ดขาด ถ้าคุณกำลังขอต่อราคาเพิ่ม หรือเป็นฝ่ายเสนอราคาใหม่ คุณจะพิมพ์แท็กนี้ได้ก็ต่อเมื่อคุณ 'ยอมรับ' ราคาที่เจ้าของร้านเสนอมาล่าสุดเท่านั้น!
4. ถ้าถูกกดราคาน่าเกลียดรับไม่ได้ ให้ด่าแล้วใส่ [DEAL_REJECTED] 
5. เริ่มทักทาย เสนอขายของ และบอกราคาที่อยากได้ทันที
6. ***สำคัญมาก: ตอบให้สั้นกระชับที่สุด ไม่เกิน 1-2 ประโยค พูดเหมือนคนคุยกันจริงๆ (ห้ามพิมพ์ยาวเวิ่นเว้อ)***
"""
        initial_user_msg = "สวัสดีครับ วันนี้มีอะไรมาให้ทางร้านดูบ้างครับ?"

    game["history"] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_user_msg}
    ]

    # Expert Appraiser Lv.3 passive: Auto-appraise item immediately!
    staff_data = get_staff_data(shop)
    levels = sync_staff_levels(staff_data.get("hired", []))
    expert_level = levels["expert_level"]
    auto_appraised = (expert_level >= 3)
    if auto_appraised:
        game["appraised"] = True

    custom_key = data.get("groq_key")
    response = call_llm(game["history"], custom_key=custom_key)
    is_fallback = False
    if not response or not strip_tags(response):
        response = generate_fallback_greeting(game)
        is_fallback = True

    clean_resp = strip_tags(response)
    game["fallback_mode"] = is_fallback
    game["history"].append({"role": "assistant", "content": clean_resp})

    bot_price = extract_price(clean_resp)
    if bot_price:
        game["last_bot_price"] = bot_price

    # หักคิวหลังจาก AI ตอบสำเร็จเท่านั้น
    db.update_shop(pin, customers_left=max(0, (shop.get("customers_left") or 0) - 1), active_customer=cid)
    bump_version(pin)

    return jsonify({
        "status": "ok",
        "type": game["transaction_type"],
        "item": {k: item.get(k) for k in ("name", "value", "image", "bought_price", "condition", "condition_name", "condition_color", "condition_pct", "original_value")},
        "avatar": avatar,
        "customer": customer,
        "message": clean_resp,
        "bot_price": game.get("last_bot_price") or 0,
        "fallback": is_fallback,
        "auto_appraised": auto_appraised,
        "is_fake": game.get("is_fake", False) if auto_appraised else None,
        "true_value": (game.get("true_value") or game["value"]) if auto_appraised else None,
        "session": save_session(pin, game),
        "customers_left": max(0, (shop.get("customers_left") or 0) - 1),
        "save": build_save(pin)
    })

@app.route("/api/appraise", methods=["POST"])
def appraise_item():
    data = req_json()
    pin = data.get("pin")

    game = load_session(pin, data.get("session"))
    if not game:
        return jsonify({"status": "error", "message": "Game session not found"})

    if game.get("appraised"):
        return jsonify({"status": "error", "message": "ตรวจสอบสินค้าชิ้นนี้ไปแล้ว"})

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()
    if shop.get("active_customer") != game.get("cid"):
        return jsonify({"status": "error", "message": "Game session not found"})

    # Check expert appraiser discount/free
    staff_data = get_staff_data(shop)
    levels = sync_staff_levels(staff_data.get("hired", []))
    expert_lvl = levels["expert_level"]

    if expert_lvl >= 2:
        cost = 0
    elif expert_lvl == 1:
        cost = 250
    else:
        cost = 500

    if cost > 0 and shop["money"] < cost:
        return jsonify({"status": "error", "message": f"เงินไม่พอค่าตรวจสอบ (ต้องการ ฿{cost:,})"})

    if cost > 0:
        db.update_shop(pin, money=shop["money"] - cost)
        bump_version(pin)

    game["appraised"] = True
    is_fake = bool(game.get("is_fake", False))
    true_val = game.get("true_value") or game["value"]
    trans_type = game.get("transaction_type")

    return jsonify({
        "status": "ok",
        "is_fake": is_fake,
        "true_value": true_val,
        "cost": cost,
        "trans_type": trans_type,
        "session": save_session(pin, game),
        "save": build_save(pin)
    })

@app.route("/api/chat", methods=["POST"])
def chat():
    data = req_json()
    pin = data.get("pin")
    user_message = str(data.get("message") or "").strip()[:500]

    game = load_session(pin, data.get("session"))
    if not game:
        return jsonify({"status": "error", "message": "Game session not found"})

    shop = ensure_shop(pin, data.get("save"))
    if not shop:
        return shop_not_found()
    if shop.get("active_customer") != game.get("cid"):
        # ลูกค้าคนนี้ปิดดีลไปแล้ว หรือเป็นข้อมูลเก่า
        return jsonify({"status": "error", "message": "Game session not found"})

    # --- กรณีผู้เล่นกดยืนยันรับข้อเสนอเงินกู้ (Accept Loan) ---
    if data.get("accept_loan"):
        pending = game.get("pending_loan")
        if not pending:
            return jsonify({"status": "error", "message": "ไม่พบข้อเสนอเงินกู้ที่รอดำเนินการ"})

        item = game.get("item")
        agreed_price = pending["agreed_price"]
        deficit = pending["deficit"]
        interest = pending["interest"]
        total_debt = pending["total_debt"]
        installments = pending.get("installments", 3)

        current_debt = int(shop.get("loan_remaining") or 0)
        credit_limit = 200000 + (shop.get("reputation") or 10) * 20000

        if (current_debt + total_debt) > credit_limit:
            db.update_shop(pin, active_customer=None)
            bump_version(pin)
            current_games.pop(pin, None)
            return jsonify({
                "status": "ok",
                "rejected": True,
                "message": "วงเงินสินเชื่อของคุณเต็มแล้ว ไม่สามารถกู้เพิ่มได้",
                "save": build_save(pin)
            })

        new_total_debt = current_debt + total_debt
        new_daily = (new_total_debt + 2) // installments
        new_principal = int(shop.get("loan_principal") or 0) + deficit

        new_money = 0
        new_rep = (shop.get("reputation") or 10) + 1
        db.update_shop(pin, money=new_money, reputation=new_rep,
                       loan_remaining=new_total_debt, loan_daily=new_daily,
                       loan_days_left=installments, loan_principal=new_principal,
                       active_customer=None)
        db.add_inventory_item(pin, name=item["name"], value=game["value"], true_value=game["true_value"], bought_price=agreed_price, image=item["image"], condition=item.get("condition", 100))
        bump_version(pin)
        current_games.pop(pin, None)

        cond_info = get_condition_info(item.get("condition", 100))
        return jsonify({
            "status": "ok",
            "accepted": True,
            "rejected": False,
            "loan_taken": True,
            "price": agreed_price,
            "type": "sell",
            "message": f"ตกลงทำสัญญาเงินกู้ ฿{deficit:,} (ดอกเบี้ย ฿{interest:,}) และรับซื้อ {item['name']} สำเร็จ!",
            "loan_info": {
                "borrowed": deficit,
                "interest": interest,
                "total_debt": total_debt,
                "loan_remaining": new_total_debt,
                "daily_payment": new_daily,
                "days_left": installments
            },
            "item": {
                "name": item.get("name"),
                "value": item.get("value"),
                "image": item.get("image"),
                "condition": item.get("condition", 100),
                "condition_name": cond_info["name"],
                "condition_color": cond_info["color"]
            },
            "save": build_save(pin)
        })

    # --- กรณีผู้เล่นปฏิเสธข้อเสนอเงินกู้ (Decline Loan) ---
    if data.get("decline_loan"):
        db.update_shop(pin, active_customer=None)
        bump_version(pin)
        current_games.pop(pin, None)
        return jsonify({
            "status": "ok",
            "rejected": True,
            "message": "คุณปฏิเสธการกู้เงิน การซื้อขายจึงถูกยกเลิก",
            "save": build_save(pin)
        })

    if not user_message:
        return jsonify({"status": "error", "message": "กรุณาพิมพ์ข้อความ"})

    history = list(game["history"]) + [{"role": "user", "content": user_message}]

    custom_key = data.get("groq_key")
    bot_response = call_llm(history, custom_key=custom_key)
    is_fallback = False
    if not bot_response:
        bot_response = generate_fallback_chat(game, user_message)
        is_fallback = True

    clean_response = strip_tags(bot_response)
    history.append({"role": "assistant", "content": clean_response})
    game["history"] = history
    game["fallback_mode"] = is_fallback

    bot_price = extract_price(clean_response)
    if bot_price:
        game["last_bot_price"] = bot_price

    trans_type = game["transaction_type"]
    accepted = False
    rejected = bool(TAG_REJECT.search(bot_response))
    agreed_price = 0

    match = TAG_ACCEPT.search(bot_response)
    if match:
        try:
            agreed_price = int(float(match.group(2).replace(',', '')))
            accepted = agreed_price > 0
        except ValueError:
            accepted = False

    result = {
        "status": "ok",
        "message": clean_response,
        "accepted": False,
        "rejected": False,
        "price": agreed_price,
        "type": trans_type,
        "item": None,
        "bot_price": game.get("last_bot_price") or 0,
        "fallback": is_fallback,
    }

    item = game["item"]

    if accepted:
        if trans_type == "sell":
            # ลูกค้าขายของให้เรา -> เช็คเงินฝั่งเซิร์ฟเวอร์ก่อน
            if shop["money"] < agreed_price:
                deficit = agreed_price - shop["money"]
                interest = int(deficit * 0.20)  # ดอกเบี้ย 20%
                total_debt = deficit + interest
                installments = 3
                daily_pay = (total_debt + 2) // installments

                current_debt = int(shop.get("loan_remaining") or 0)
                credit_limit = 200000 + (shop.get("reputation") or 10) * 20000
                can_loan = (current_debt + total_debt) <= credit_limit

                # บันทึกข้อมูลข้อเสนอเงินกู้ไว้ใน session เผื่อผู้เล่นกดยืนยันกู้
                game["pending_loan"] = {
                    "agreed_price": agreed_price,
                    "deficit": deficit,
                    "interest": interest,
                    "total_debt": total_debt,
                    "daily_payment": daily_pay,
                    "installments": installments
                }

                # ส่งข้อเสนอเงินกู้ให้ผู้เล่นตัดสินใจ (ลูกค้ายังรอคอยที่เคาน์เตอร์)
                result["insufficient_funds"] = True
                result["can_loan"] = can_loan
                result["credit_limit_exceeded"] = not can_loan
                result["loan_offer"] = {
                    "deficit": deficit,
                    "interest": interest,
                    "total_debt": total_debt,
                    "daily_payment": daily_pay,
                    "installments": installments,
                    "agreed_price": agreed_price,
                    "current_money": shop["money"],
                    "current_debt": current_debt,
                    "credit_limit": credit_limit,
                    "item_name": item["name"]
                }
                result["session"] = save_session(pin, game)
                result["save"] = build_save(pin)
                return jsonify(result)
            else:
                db.update_shop(pin, money=shop["money"] - agreed_price, reputation=(shop.get("reputation") or 10) + 1)
                db.add_inventory_item(pin, name=item["name"], value=game["value"], true_value=game["true_value"], bought_price=agreed_price, image=item["image"], condition=item.get("condition", 100))
                result["accepted"] = True
                cond_info = get_condition_info(item.get("condition", 100))
                result["item"] = {
                    "name": item.get("name"),
                    "value": item.get("value"),
                    "image": item.get("image"),
                    "condition": item.get("condition", 100),
                    "condition_name": cond_info["name"],
                    "condition_color": cond_info["color"]
                }
        else:
            # ลูกค้ามาซื้อของจากเรา (Shop selling item to customer)
            is_fake_item = bool(game.get("is_fake") or (item.get("true_value", item["value"]) < item["value"]))

            staff_data = get_staff_data(shop)
            levels = sync_staff_levels(staff_data.get("hired", []))
            forger_lvl = levels["forger_level"]
            persona = game.get("persona_desc", "")

            # โอกาสที่ลูกค้าจะตรวจสอบของ (ผู้เชี่ยวชาญ/คนรวยจะส่องถี่กว่า)
            inspect_chance = 0.80 if ("เศรษฐี" in persona or "พ่อค้าคนกลาง" in persona) else (0.35 if ("ซื่อๆ" in persona or "ร้อนเงิน" in persona) else 0.55)
            will_inspect = is_fake_item and (random.random() < inspect_chance)

            caught = False
            if will_inspect:
                # พลังนักปลอมแปลงในการตบตา: 0 ดาว = โดนจับ 100%, 1 ดาว = รอด 60%, 2 ดาว = รอด 85%, 3 ดาว = รอด 100%
                fool_chance = [0.0, 0.60, 0.85, 1.00][min(3, forger_lvl)]
                if random.random() >= fool_chance:
                    caught = True

            if caught:
                # โดนจับได้ว่าขายของปลอม! ปรับเงินชดใช้ + เสียชื่อเสียง + ดีลล่ม
                fine = max(3000, int(agreed_price * 0.40))
                rep_loss = 4
                new_money = max(0, shop["money"] - fine)
                new_rep = max(0, (shop.get("reputation") or 10) - rep_loss)
                db.update_shop(pin, money=new_money, reputation=new_rep, active_customer=None)
                bump_version(pin)
                current_games.pop(pin, None)

                fail_msg = f"🚨 เดี๋ยวนะ! ขอส่องดูชัดๆ ก่อน... เฮ้ย! นี่มันของปลอมนี่หว่า!! แกกล้าเอาของปลอมมาย้อมแมวขายฉันเหรอ?! จ่ายค่าปรับและค่าทำขวัญมา ฿{fine:,} เดี๋ยวนี้! ไม่งั้นฉันแจ้งความแน่!"
                return jsonify({
                    "status": "ok",
                    "accepted": False,
                    "rejected": True,
                    "fake_caught": True,
                    "fine": fine,
                    "rep_loss": rep_loss,
                    "price": agreed_price,
                    "type": "buy",
                    "message": fail_msg,
                    "item": {k: item.get(k) for k in ("name", "value", "image", "bought_price")},
                    "save": build_save(pin)
                })

            # ลูกค้ามาซื้อของจากเรา -> ลบของออกจากคลังก่อน ถ้าลบได้จริงค่อยรับเงิน (กันขายของชิ้นเดิมซ้ำ)
            deleted = db.delete_inventory_item(pin, item_id=item.get("id"), name=item.get("name"), bought_price=item.get("bought_price"))
            if deleted:
                db.update_shop(pin, money=shop["money"] + agreed_price, reputation=(shop.get("reputation") or 10) + 1)
                result["accepted"] = True
                result["item"] = {k: item.get(k) for k in ("name", "value", "image", "bought_price")}
                if is_fake_item:
                    result["fooled_fake"] = True
                    result["forger_level"] = forger_lvl
            else:
                result["item_missing"] = True
                result["rejected"] = True
    elif rejected:
        result["rejected"] = True

    if result["accepted"] or result["rejected"]:
        # ปิดดีล -> ลูกค้าคนนี้ใช้ต่อไม่ได้แล้ว
        db.update_shop(pin, active_customer=None)
        bump_version(pin)
        current_games.pop(pin, None)
    else:
        result["session"] = save_session(pin, game)

    result["save"] = build_save(pin)
    return jsonify(result)

@app.errorhandler(Exception)
def handle_error(e):
    # ให้ API ตอบเป็น JSON เสมอ (หน้าเว็บจะไม่ค้างเพราะ parse HTML error ไม่ได้)
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "message": e.description}), e.code
        return e
    app.logger.exception(e)
    return jsonify({"status": "error", "message": f"เซิร์ฟเวอร์ผิดพลาด: {e}"}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
