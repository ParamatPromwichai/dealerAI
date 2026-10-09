import os
import random
import re
import sqlite3
import json
import hmac
import hashlib
import base64
import zlib
import secrets
from flask import Flask, render_template, request, jsonify, send_from_directory
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
try:
    # timeout สั้นพอให้ไม่โดน Vercel ตัด function กลางทาง
    client = Groq(timeout=25.0, max_retries=1)
except Exception as e:  # ไม่มี GROQ_API_KEY -> อย่าให้ทั้งแอปพัง
    print("Groq init failed:", e)
    client = None

if os.environ.get("VERCEL"):
    DB_FILE = '/tmp/pawnshop.db'
else:
    DB_FILE = 'pawnshop.db'

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

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
               "version", "active_customer"]
INV_FIELDS = ["name", "value", "true_value", "bought_price", "image"]

def lvl(shop, key):
    v = shop.get(key)
    try:
        return max(0, min(3, int(v or 0)))
    except (TypeError, ValueError):
        return 0

def build_save(conn, pin):
    c = conn.cursor()
    c.execute('SELECT * FROM shop WHERE pin = ?', (pin,))
    row = c.fetchone()
    if not row:
        return None
    shop = dict(row)
    c.execute('SELECT * FROM inventory WHERE shop_pin = ? ORDER BY id', (pin,))
    inv = [{k: r[k] for k in ["id"] + INV_FIELDS} for r in c.fetchall()]
    return make_token({"t": "save", "pin": pin, "shop": {k: shop.get(k) for k in SHOP_FIELDS}, "inv": inv})

def ensure_shop(conn, pin, save_token=None):
    """คืนค่า shop (dict) หรือ None
    ถ้า instance นี้ไม่มีข้อมูล หรือข้อมูลเก่ากว่าเซฟของ Client -> กู้คืนจากเซฟอัตโนมัติ"""
    if not pin:
        return None
    c = conn.cursor()
    c.execute('SELECT * FROM shop WHERE pin = ?', (pin,))
    row = c.fetchone()

    snap = read_token(save_token)
    if snap and (snap.get("t") != "save" or snap.get("pin") != pin):
        snap = None

    if snap and (row is None or (row["version"] or 0) < (snap["shop"].get("version") or 0)):
        s = snap["shop"]
        c.execute('DELETE FROM shop WHERE pin = ?', (pin,))
        c.execute('DELETE FROM inventory WHERE shop_pin = ?', (pin,))
        c.execute('''INSERT INTO shop (pin, nickname, money, day, reputation, customers_left,
                     guard_level, repair_level, forger_level, ad_level, version, active_customer)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (pin, s.get("nickname"), s.get("money", 100000), s.get("day", 1), s.get("reputation", 10),
                   s.get("customers_left", 5), s.get("guard_level", 0), s.get("repair_level", 0),
                   s.get("forger_level", 0), s.get("ad_level", 0), s.get("version", 0), s.get("active_customer")))
        for it in snap.get("inv", []):
            vals = (it.get("name"), it.get("value", 0), it.get("true_value", 0), it.get("bought_price", 0), it.get("image"), pin)
            try:
                c.execute('INSERT INTO inventory (id, name, value, true_value, bought_price, image, shop_pin) VALUES (?, ?, ?, ?, ?, ?, ?)',
                          (it.get("id"),) + vals)
            except sqlite3.IntegrityError:
                c.execute('INSERT INTO inventory (name, value, true_value, bought_price, image, shop_pin) VALUES (?, ?, ?, ?, ?, ?)', vals)
        conn.commit()
        c.execute('SELECT * FROM shop WHERE pin = ?', (pin,))
        row = c.fetchone()

    return dict(row) if row else None

def bump_version(c, pin):
    c.execute('UPDATE shop SET version = COALESCE(version, 0) + 1 WHERE pin = ?', (pin,))

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

def llm(messages):
    if client is None:
        raise RuntimeError("ยังไม่ได้ตั้งค่า GROQ_API_KEY บนเซิร์ฟเวอร์")
    completion = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=400,
    )
    return clean_llm(completion.choices[0].message.content)

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS shop (id INTEGER PRIMARY KEY, money INTEGER, day INTEGER)''')
    
    # Add new columns safely
    new_cols = [
        ('reputation', 'INTEGER DEFAULT 10'),
        ('customers_left', 'INTEGER DEFAULT 5'),
        ('guard_level', 'INTEGER DEFAULT 0'),
        ('repair_level', 'INTEGER DEFAULT 0'),
        ('forger_level', 'INTEGER DEFAULT 0'),
        ('ad_level', 'INTEGER DEFAULT 0'),
        ('pin', 'TEXT'),
        ('nickname', 'TEXT'),
        ('version', 'INTEGER DEFAULT 0'),
        ('active_customer', 'TEXT')
    ]
    for col, definition in new_cols:
        try:
            c.execute(f'ALTER TABLE shop ADD COLUMN {col} {definition}')
        except:
            pass
            
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER, bought_price INTEGER, image TEXT)''')
    try:
        c.execute('ALTER TABLE inventory ADD COLUMN true_value INTEGER DEFAULT 0')
    except:
        pass
    try:
        c.execute('ALTER TABLE inventory ADD COLUMN shop_pin TEXT')
    except:
        pass
    
    conn.commit()
    conn.close()

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

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT pin FROM shop WHERE pin = ?', (pin,))
        if c.fetchone():
            return jsonify({"status": "error", "message": "รหัส PIN นี้ถูกใช้งานแล้ว กรุณาใช้รหัสอื่น"})

        c.execute('INSERT INTO shop (pin, nickname, money, day, reputation, customers_left, version) VALUES (?, ?, 100000, 1, 10, 5, 1)', (pin, nickname))
        conn.commit()
        return jsonify({"status": "ok", "pin": pin, "nickname": nickname, "save": build_save(conn, pin)})
    finally:
        conn.close()

@app.route("/api/login", methods=["POST"])
def login():
    data = req_json()
    pin = str(data.get("pin") or "").strip()
    if not pin:
        return jsonify({"status": "error", "message": "PIN is required"})

    conn = get_db()
    try:
        shop = ensure_shop(conn, pin, data.get("save"))
        if shop:
            return jsonify({"status": "ok", "nickname": shop.get("nickname"), "pin": pin, "save": build_save(conn, pin)})
        return jsonify({"status": "error", "message": "ไม่พบ PIN นี้ในระบบ"})
    finally:
        conn.close()

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

    conn = get_db()
    try:
        shop = ensure_shop(conn, pin, save)
        if not shop:
            return shop_not_found()

        c = conn.cursor()
        c.execute('SELECT * FROM inventory WHERE shop_pin = ? ORDER BY id', (pin,))
        inv = [dict(row) for row in c.fetchall()]

        return jsonify({
            "status": "ok",
            "money": shop["money"],
            "day": shop["day"],
            "reputation": shop.get("reputation") if shop.get("reputation") is not None else 10,
            "customers_left": shop.get("customers_left") if shop.get("customers_left") is not None else 5,
            "nickname": shop.get("nickname") or "Unknown",
            "staff": {
                "guard": lvl(shop, "guard_level"),
                "repair": lvl(shop, "repair_level"),
                "forger": lvl(shop, "forger_level"),
                "ad": lvl(shop, "ad_level")
            },
            "inventory": inv,
            "save": build_save(conn, pin)
        })
    finally:
        conn.close()

@app.route("/api/end_day", methods=["POST"])
def end_day():
    data = req_json()
    pin = data.get("pin")

    conn = get_db()
    try:
        shop = ensure_shop(conn, pin, data.get("save"))
        if not shop:
            return shop_not_found()
        c = conn.cursor()

        # กันการกดซ้ำ/ส่งคำขอซ้ำ (เช่นเน็ต iPad กระตุก) ทำให้ข้ามวันสองรอบ
        if (shop.get("customers_left") or 0) > 0:
            return jsonify({"status": "error", "message": "ยังมีลูกค้ารอคิวอยู่", "save": build_save(conn, pin)})

        rent = 1000

        g_level = lvl(shop, "guard_level")
        r_level = lvl(shop, "repair_level")
        f_level = lvl(shop, "forger_level")
        ad_level = lvl(shop, "ad_level")

        wages = [0, 1000, 2000, 3000][g_level] + [0, 1500, 3000, 5000][r_level] \
              + [0, 2000, 4000, 7000][f_level] + [0, 1000, 2500, 5000][ad_level]

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

        # Advertiser passive buff
        if ad_level > 0:
            rep_buff = [0, 1, 2, 4][ad_level]
            new_rep += rep_buff
            event_logs.append(f"นักโฆษณา (Lv.{ad_level}) ช่วยโปรโมทร้าน ได้ชื่อเสียงเพิ่ม +{rep_buff}")

        # Repairman passive buff
        if r_level > 0:
            c.execute("SELECT * FROM inventory WHERE shop_pin = ? AND name LIKE ?", (pin, "[พัง] %"))
            broken_items = c.fetchall()

            items_to_fix = 0
            if r_level == 1 and random.random() > 0.5: items_to_fix = 1
            elif r_level == 2: items_to_fix = 1
            elif r_level == 3: items_to_fix = 999

            fixed_count = 0
            for b_item in broken_items:
                if fixed_count >= items_to_fix: break
                new_name = b_item["name"].replace("[พัง] ", "", 1)
                new_val = int(b_item["value"] / 0.3)
                c.execute('UPDATE inventory SET name = ?, value = ?, true_value = ? WHERE id = ?', (new_name, new_val, new_val, b_item["id"]))
                fixed_count += 1

            if fixed_count > 0:
                event_logs.append(f"ช่างซ่อม (Lv.{r_level}) แอบซ่อมของพังให้คุณไป {fixed_count} ชิ้นเมื่อคืนนี้!")

        new_day = shop["day"] + 1
        # Queue size based on reputation (min 3, max 20)
        new_queue = min(20, max(3, 3 + (new_rep // 10)))

        c.execute('''UPDATE shop
                     SET money = ?, day = ?, reputation = ?, customers_left = ?, active_customer = NULL
                     WHERE pin = ?''', (new_money, new_day, new_rep, new_queue, pin))
        bump_version(c, pin)
        conn.commit()

        return jsonify({
            "status": "ok",
            "expenses": total_expenses,
            "rent": rent,
            "wages": wages,
            "event": event,
            "event_logs": event_logs,
            "new_day": new_day,
            "new_queue": new_queue,
            "new_money": new_money,
            "new_rep": new_rep,
            "save": build_save(conn, pin)
        })
    finally:
        conn.close()

@app.route("/api/upgrade_staff", methods=["POST"])
def upgrade_staff():
    data = req_json()
    pin = data.get("pin")
    role = data.get("role")

    valid_roles = ["guard", "repair", "forger", "ad"]
    if role not in valid_roles:
        return jsonify({"status": "error", "message": "Role not found"})

    conn = get_db()
    try:
        shop = ensure_shop(conn, pin, data.get("save"))
        if not shop:
            return shop_not_found()
        c = conn.cursor()

        col = f"{role}_level"
        current_lvl = lvl(shop, col)

        if current_lvl >= 3:
            return jsonify({"status": "error", "message": "ระดับสูงสุดแล้ว"})

        costs_map = {
            "guard": [10000, 25000, 50000],
            "repair": [15000, 30000, 60000],
            "forger": [30000, 60000, 100000],
            "ad": [20000, 40000, 80000]
        }
        cost = costs_map[role][current_lvl]

        if shop["money"] < cost:
            return jsonify({"status": "error", "message": f"เงินไม่พอ (ต้องการ {cost:,} บาท)"})

        c.execute(f'UPDATE shop SET money = money - ?, {col} = ? WHERE pin = ?', (cost, current_lvl + 1, pin))
        bump_version(c, pin)
        conn.commit()

        return jsonify({"status": "ok", "message": f"อัปเกรดสำเร็จเป็นระดับ {current_lvl + 1}!", "save": build_save(conn, pin)})
    finally:
        conn.close()

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

    conn = get_db()
    try:
        shop = ensure_shop(conn, pin, data.get("save"))
        if not shop:
            conn.close()
            return shop_not_found()
        c = conn.cursor()

        if (shop.get("customers_left") or 0) <= 0:
            resp = jsonify({"status": "end_of_day", "save": build_save(conn, pin)})
            conn.close()
            return resp

        c.execute('SELECT * FROM inventory WHERE shop_pin = ?', (pin,))
        inventory = [dict(row) for row in c.fetchall()]
    except Exception:
        conn.close()
        raise

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

    # สุ่มว่าจะเป็นคนมาขายของ หรือมาซื้อของ (ถ้าไม่มีของในคลัง ต้องเป็นคนมาขายเท่านั้น)
    is_buyer = False
    if len(inventory) > 0 and random.random() > 0.5:
        is_buyer = True

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

        game["item"] = item
        game["value"] = item["value"]
        game["true_value"] = base_price_for_buyer
        game["max_price"] = max_price

        system_prompt = f"""คุณกำลังเล่นเกม Roleplay: คุณคือลูกค้าที่เดินเข้ามาในร้านขายของมือสองเพื่อ 'ขอซื้อของ'
เพศของคุณ: {gender} (ต้องใช้สรรพนามและคำลงท้ายให้ตรงกับเพศ เช่น ชาย=ผม/ครับ หญิง=ฉัน/หนู/ค่ะ/คะ)
ของที่คุณสนใจจะซื้อคือ: {item['name']} (ราคากลางประมาณ: {item['value']} บาท)
ราคาสูงสุดที่คุณยอมจ่ายคือ: {max_price} บาท (ห้ามบอกตัวเลขนี้ให้เจ้าของร้านรู้เด็ดขาด!)
ลักษณะนิสัยของคุณ: {persona}

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

        if is_fake:
            true_value = int(item["value"] * 0.1) # มูลค่าจริงเหลือแค่ 10%
            min_price = int(true_value * 0.5) # ยอมขายถูกมากเพราะรู้ว่าเป็นของปลอม
            persona_desc = "คุณเป็นมิจฉาชีพที่นำของ 'ปลอม' มาหลอกขายให้เนียนที่สุด ห้ามให้เจ้าของร้านรู้เด็ดขาด พยายามหลอกล่อให้เขาซื้อในราคาสูง"
        else:
            true_value = item["value"]
            persona = random.choice(SELLER_PERSONAS)
            persona_desc = persona

            if is_broken:
                item["name"] = f"[พัง] {item['name']}"
                item["value"] = int(item["value"] * 0.3)
                true_value = item["value"]

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

        system_prompt = f"""คุณกำลังเล่นเกม Roleplay: คุณคือลูกค้าที่นำของมา 'ขายให้' โรงรับจำนำ
เพศของคุณ: {gender} (ต้องใช้สรรพนามและคำลงท้ายให้ตรงกับเพศ เช่น ชาย=ผม/ครับ หญิง=ฉัน/หนู/ค่ะ/คะ)
ของที่คุณนำมาขายคือ: {item['name']}
ลักษณะนิสัยของคุณ: {persona_desc}
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

    try:
        try:
            response = llm(game["history"])
        except Exception as e:
            # AI ล่ม/timeout -> ไม่หักคิวลูกค้า ให้กดเรียกใหม่ได้
            return jsonify({"status": "error", "message": f"AI ไม่ตอบสนอง กรุณากดเรียกลูกค้าใหม่ ({e})", "save": build_save(conn, pin)})

        if not strip_tags(response):
            response = "สวัสดีครับ วันนี้เอาของมาให้ดูครับ" if game["transaction_type"] == "sell" else "สวัสดีครับ ขอดูของชิ้นนี้หน่อยครับ"
        game["history"].append({"role": "assistant", "content": response})

        # หักคิวหลังจาก AI ตอบสำเร็จเท่านั้น
        c = conn.cursor()
        c.execute('UPDATE shop SET customers_left = customers_left - 1, active_customer = ? WHERE pin = ?', (cid, pin))
        bump_version(c, pin)
        conn.commit()

        return jsonify({
            "status": "ok",
            "type": game["transaction_type"],
            "item": {k: item.get(k) for k in ("name", "value", "image", "bought_price")},
            "avatar": avatar,
            "message": strip_tags(response),
            "session": save_session(pin, game),
            "customers_left": max(0, (shop.get("customers_left") or 0) - 1),
            "save": build_save(conn, pin)
        })
    finally:
        conn.close()

@app.route("/api/appraise", methods=["POST"])
def appraise_item():
    data = req_json()
    pin = data.get("pin")

    game = load_session(pin, data.get("session"))
    if not game:
        return jsonify({"status": "error", "message": "Game session not found"})

    if game.get("transaction_type") != "sell":
        return jsonify({"status": "error", "message": "ตรวจสอบได้เฉพาะตอนลูกค้ามาขายของเท่านั้น"})

    if game.get("appraised"):
        return jsonify({"status": "error", "message": "ตรวจสอบสินค้าชิ้นนี้ไปแล้ว"})

    conn = get_db()
    try:
        shop = ensure_shop(conn, pin, data.get("save"))
        if not shop:
            return shop_not_found()
        if shop.get("active_customer") != game.get("cid"):
            return jsonify({"status": "error", "message": "Game session not found"})

        APPRAISAL_COST = 500
        if shop["money"] < APPRAISAL_COST:
            return jsonify({"status": "error", "message": "เงินไม่พอค่าตรวจสอบ (ต้องใช้ ฿500)"})

        c = conn.cursor()
        c.execute('UPDATE shop SET money = money - ? WHERE pin = ?', (APPRAISAL_COST, pin))
        bump_version(c, pin)
        conn.commit()

        game["appraised"] = True
        return jsonify({
            "status": "ok",
            "is_fake": bool(game.get("is_fake", False)),
            "true_value": game.get("true_value") or game["value"],
            "cost": APPRAISAL_COST,
            "session": save_session(pin, game),
            "save": build_save(conn, pin)
        })
    finally:
        conn.close()

@app.route("/api/chat", methods=["POST"])
def chat():
    data = req_json()
    pin = data.get("pin")
    user_message = str(data.get("message") or "").strip()[:500]

    if not user_message:
        return jsonify({"status": "error", "message": "กรุณาพิมพ์ข้อความ"})

    game = load_session(pin, data.get("session"))
    if not game:
        return jsonify({"status": "error", "message": "Game session not found"})

    conn = get_db()
    try:
        shop = ensure_shop(conn, pin, data.get("save"))
        if not shop:
            return shop_not_found()
        if shop.get("active_customer") != game.get("cid"):
            # ลูกค้าคนนี้ปิดดีลไปแล้ว หรือเป็นข้อมูลเก่า
            return jsonify({"status": "error", "message": "Game session not found"})

        history = list(game["history"]) + [{"role": "user", "content": user_message}]

        try:
            bot_response = llm(history)
        except Exception as e:
            # ไม่บันทึกข้อความลงประวัติ เพื่อให้ส่งใหม่ได้
            return jsonify({"status": "error", "message": f"AI ไม่ตอบสนอง ลองส่งใหม่อีกครั้ง ({e})"})

        if not bot_response:
            bot_response = "อืม... ว่าไงนะครับ?"
        history.append({"role": "assistant", "content": bot_response})
        game["history"] = history

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

        clean_response = strip_tags(bot_response)
        result = {
            "status": "ok",
            "message": clean_response,
            "accepted": False,
            "rejected": False,
            "price": agreed_price,
            "type": trans_type,
            "item": None,
        }

        c = conn.cursor()
        item = game["item"]

        if accepted:
            if trans_type == "sell":
                # ลูกค้าขายของให้เรา -> เช็คเงินฝั่งเซิร์ฟเวอร์ก่อน (กันเงินติดลบ)
                if shop["money"] < agreed_price:
                    result["insufficient_funds"] = True
                    result["rejected"] = True
                else:
                    c.execute('UPDATE shop SET money = money - ?, reputation = reputation + 1 WHERE pin = ?', (agreed_price, pin))
                    c.execute('INSERT INTO inventory (name, value, true_value, bought_price, image, shop_pin) VALUES (?, ?, ?, ?, ?, ?)',
                              (item["name"], game["value"], game["true_value"], agreed_price, item["image"], pin))
                    result["accepted"] = True
                    result["item"] = {k: item.get(k) for k in ("name", "value", "image")}
            else:
                # ลูกค้ามาซื้อของจากเรา -> ลบของออกจากคลังก่อน ถ้าลบได้จริงค่อยรับเงิน (กันขายของชิ้นเดิมซ้ำ)
                c.execute('DELETE FROM inventory WHERE id = ? AND shop_pin = ? AND name = ?', (item.get("id", 0), pin, item["name"]))
                if c.rowcount == 0:
                    c.execute('''DELETE FROM inventory WHERE id = (
                                   SELECT id FROM inventory WHERE shop_pin = ? AND name = ? AND bought_price = ? LIMIT 1)''',
                              (pin, item["name"], item.get("bought_price", 0)))
                if c.rowcount > 0:
                    c.execute('UPDATE shop SET money = money + ?, reputation = reputation + 1 WHERE pin = ?', (agreed_price, pin))
                    result["accepted"] = True
                    result["item"] = {k: item.get(k) for k in ("name", "value", "image", "bought_price")}
                else:
                    result["item_missing"] = True
                    result["rejected"] = True
        elif rejected:
            result["rejected"] = True

        if result["accepted"] or result["rejected"]:
            # ปิดดีล -> ลูกค้าคนนี้ใช้ต่อไม่ได้แล้ว
            c.execute('UPDATE shop SET active_customer = NULL WHERE pin = ?', (pin,))
            bump_version(c, pin)
            conn.commit()
            current_games.pop(pin, None)
        else:
            result["session"] = save_session(pin, game)

        result["save"] = build_save(conn, pin)
        return jsonify(result)
    finally:
        conn.close()

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
