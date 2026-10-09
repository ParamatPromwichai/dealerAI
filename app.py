import os
import random
import re
import sqlite3
from flask import Flask, render_template, request, jsonify, send_from_directory
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = Groq()

if os.environ.get("VERCEL"):
    DB_FILE = '/tmp/pawnshop.db'
else:
    DB_FILE = 'pawnshop.db'

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

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
        ('ad_level', 'INTEGER DEFAULT 0')
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
    
    # Check if shop exists
    c.execute('SELECT * FROM shop WHERE id = 1')
    if not c.fetchone():
        c.execute('INSERT INTO shop (id, money, day, reputation, customers_left) VALUES (1, 100000, 1, 10, 5)')
    conn.commit()
    conn.close()

init_db()

current_game = {
    "history": [],
    "item": None,
    "transaction_type": "sell", # 'sell' = customer sells to us, 'buy' = customer buys from us
    "value": 0,
    "min_price": 0, # for seller
    "max_price": 0, # for buyer
}

ITEMS = [
    {"name": "นาฬิกา Rolex รุ่นคุณปู่", "value": 150000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/Rolex_Submariner.jpg/500px-Rolex_Submariner.jpg"},
    {"name": "การ์ด Charizard (Holo)", "value": 20000, "image": "/static/images/charizard.jpg"},
    {"name": "เครื่องเกม PS5 มือสอง", "value": 12000, "image": "https://upload.wikimedia.org/wikipedia/commons/7/77/Black_and_white_Playstation_5_base_edition_with_controller.png"},
    {"name": "กีตาร์ Fender ปี 1990", "value": 35000, "image": "https://thumb.wikimedia.org/wikipedia/commons/thumb/6/63/Fender_Stratocaster_004-2.jpg/500px-Fender_Stratocaster_004-2.jpg"},
    {"name": "สร้อยคอทองคำ 1 บาท", "value": 40000, "image": "/static/images/gold.jpg"},
    {"name": "ภาพวาดสีน้ำมันเก่าเก็บ", "value": 8000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ec/Mona_Lisa%2C_by_Leonardo_da_Vinci%2C_from_C2RMF_retouched.jpg/500px-Mona_Lisa%2C_by_Leonardo_da_Vinci%2C_from_C2RMF_retouched.jpg"},
    {"name": "กระเป๋า Hermes", "value": 250000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/db/Pink_Birkin_bag.jpg/500px-Pink_Birkin_bag.jpg"},
    {"name": "MacBook Pro M3 มือสอง", "value": 45000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/MacBook_Pro_16_%28M1_Pro%2C_2021%29_-_Wikipedia.jpg/500px-MacBook_Pro_16_%28M1_Pro%2C_2021%29_-_Wikipedia.jpg"},
    {"name": "แหวนเพชร 1 กะรัต น้ำ 99", "value": 120000, "image": "https://thumb.wikimedia.org/wikipedia/commons/thumb/0/08/Two_engagement_rings_1.jpg/500px-Two_engagement_rings_1.jpg"},
    {"name": "เหรียญกษาปณ์โบราณ ร.๕", "value": 15000, "image": "https://thumb.wikimedia.org/wikipedia/commons/thumb/e/ee/Kiloware.JPG/500px-Kiloware.JPG"},
    {"name": "กล้องฟิล์ม Leica M6", "value": 90000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e6/Leica_M6_TTL_front.jpg/500px-Leica_M6_TTL_front.jpg"},
    {"name": "รองเท้า Nike Air Jordan 1", "value": 25000, "image": "/static/images/nike.jpg"},
    {"name": "แว่นตา Ray-Ban ยุค 80s", "value": 5000, "image": "/static/images/rayban.jpg"},
    {"name": "ดาบคาตานะญี่ปุ่นแท้", "value": 85000, "image": "https://thumb.wikimedia.org/wikipedia/commons/thumb/9/9b/Katana_-_Motoshige.JPG/500px-Katana_-_Motoshige.JPG"},
    {"name": "จักรยานเสือหมอบคาร์บอน", "value": 60000, "image": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Look_795_30th_Anniversary_Dura-Ace_9100-Mavic_Custom_Build_%2830636542393%29.jpg/500px-Look_795_30th_Anniversary_Dura-Ace_9100-Mavic_Custom_Build_%2830636542393%29.jpg"},
    {"name": "เครื่องพิมพ์ดีดโบราณ", "value": 4500, "image": "https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b7/MEK_II-371.jpg/500px-MEK_II-371.jpg"},
    {"name": "แจกันเครื่องลายครามจีน", "value": 30000, "image": "/static/images/vase.jpg"},
    {"name": "ฟิกเกอร์ Iron Man (Limited)", "value": 18000, "image": "/static/images/robot.jpg"},
    {"name": "กระเป๋าเดินทาง Rimowa", "value": 32000, "image": "https://thumb.wikimedia.org/wikipedia/commons/thumb/c/c0/Suitcase1.jpg/500px-Suitcase1.jpg"},
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
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

@app.route("/api/load_game", methods=["GET"])
def load_game():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM shop WHERE id = 1')
    shop = dict(c.fetchone())
    
    c.execute('SELECT * FROM inventory')
    inv = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify({
        "money": shop["money"],
        "day": shop["day"],
        "reputation": shop.get("reputation", 10),
        "customers_left": shop.get("customers_left", 5),
        "staff": {
            "guard": shop.get("guard_level", 0),
            "repair": shop.get("repair_level", 0),
            "forger": shop.get("forger_level", 0),
            "ad": shop.get("ad_level", 0)
        },
        "inventory": inv
    })

@app.route("/api/end_day", methods=["POST"])
def end_day():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM shop WHERE id = 1')
    shop = dict(c.fetchone())
    
    rent = 1000
    
    wage_guard = [0, 1000, 2000, 3000][shop.get("guard_level", 0) if shop.get("guard_level") is not None else 0]
    wage_repair = [0, 1500, 3000, 5000][shop.get("repair_level", 0) if shop.get("repair_level") is not None else 0]
    wage_forger = [0, 2000, 4000, 7000][shop.get("forger_level", 0) if shop.get("forger_level") is not None else 0]
    wage_ad = [0, 1000, 2500, 5000][shop.get("ad_level", 0) if shop.get("ad_level") is not None else 0]
    wages = wage_guard + wage_repair + wage_forger + wage_ad
    
    total_expenses = rent + wages
    event = dict(random.choice(EVENTS))
    event_logs = []
    
    # Guard protection
    g_level = shop.get("guard_level", 0)
    if event["name"] == "โจรปล้นร้าน!" and g_level > 0:
        reward = [0, 1000, 2000, 5000][g_level]
        event = {"type": "good", "name": "ยามจับโจรได้!", "desc": f"โจรพยายามงัดร้าน แต่ยามที่คุณจ้างไว้ (Lv.{g_level}) จับได้แถมได้รางวัลนำจับ!", "money_mod": reward, "rep_mod": 5}
    elif event["name"] == "ค่าคุ้มครอง" and g_level >= 2:
        event = {"type": "neutral", "name": "ยามไล่นักเลง", "desc": f"นักเลงมาเก็บค่าคุ้มครอง แต่เจอยามล่ำบึ้ก (Lv.{g_level}) ไล่ตะเพิดกลับไป", "money_mod": 0, "rep_mod": 2}
        
    new_money = shop["money"] - total_expenses + event["money_mod"]
    new_rep = shop.get("reputation", 10) + event["rep_mod"]
    
    # Advertiser passive buff
    ad_level = shop.get("ad_level", 0)
    if ad_level > 0:
        rep_buff = [0, 1, 2, 4][ad_level]
        new_rep += rep_buff
        event_logs.append(f"นักโฆษณา (Lv.{ad_level}) ช่วยโปรโมทร้าน ได้ชื่อเสียงเพิ่ม +{rep_buff}")
        
    # Repairman passive buff
    r_level = shop.get("repair_level", 0)
    if r_level > 0:
        c.execute('SELECT * FROM inventory WHERE name LIKE "[พัง] %"')
        broken_items = c.fetchall()
        
        items_to_fix = 0
        if r_level == 1 and random.random() > 0.5: items_to_fix = 1
        elif r_level == 2: items_to_fix = 1
        elif r_level == 3: items_to_fix = 999
        
        fixed_count = 0
        for b_item in broken_items:
            if fixed_count >= items_to_fix: break
            new_name = b_item["name"].replace("[พัง] ", "")
            new_val = int(b_item["value"] / 0.3)
            c.execute('UPDATE inventory SET name = ?, value = ?, true_value = ? WHERE id = ?', (new_name, new_val, new_val, b_item["id"]))
            fixed_count += 1
            
        if fixed_count > 0:
            event_logs.append(f"ช่างซ่อม (Lv.{r_level}) แอบซ่อมของพังให้คุณไป {fixed_count} ชิ้นเมื่อคืนนี้!")
    
    new_day = shop["day"] + 1
    # Queue size based on reputation (min 3, max 20)
    new_queue = min(20, max(3, 3 + (new_rep // 10)))
    
    c.execute('''UPDATE shop 
                 SET money = ?, day = ?, reputation = ?, customers_left = ?
                 WHERE id = 1''', (new_money, new_day, new_rep, new_queue))
    conn.commit()
    conn.close()
    
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
        "new_rep": new_rep
    })

@app.route("/api/upgrade_staff", methods=["POST"])
def upgrade_staff():
    data = request.json
    role = data.get("role")
    
    valid_roles = ["guard", "repair", "forger", "ad"]
    if role not in valid_roles:
        return jsonify({"status": "error", "message": "Role not found"})
        
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM shop WHERE id = 1')
    shop = dict(c.fetchone())
    
    col = f"{role}_level"
    current_lvl = shop.get(col) if shop.get(col) is not None else 0
    
    if current_lvl >= 3:
        conn.close()
        return jsonify({"status": "error", "message": "ระดับสูงสุดแล้ว"})
        
    costs_map = {
        "guard": [10000, 25000, 50000],
        "repair": [15000, 30000, 60000],
        "forger": [30000, 60000, 100000],
        "ad": [20000, 40000, 80000]
    }
    cost = costs_map[role][current_lvl]
    
    if shop["money"] < cost:
        conn.close()
        return jsonify({"status": "error", "message": f"เงินไม่พอ (ต้องการ {cost:,} บาท)"})
        
    c.execute(f'UPDATE shop SET money = money - ?, {col} = ? WHERE id = 1', (cost, current_lvl + 1))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "ok", "message": f"อัปเกรดสำเร็จเป็นระดับ {current_lvl + 1}!"})

@app.route("/api/new_customer", methods=["POST"])
def new_customer():
    global current_game
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT * FROM shop WHERE id = 1')
    shop = dict(c.fetchone())
    
    if shop.get("customers_left", 0) <= 0:
        conn.close()
        return jsonify({"status": "end_of_day"})
        
    c.execute('UPDATE shop SET customers_left = customers_left - 1 WHERE id = 1')
    conn.commit()
    
    c.execute('SELECT * FROM inventory')
    inventory = [dict(row) for row in c.fetchall()]
    conn.close()
    
    avatar_data = random.choice(AVATARS)
    avatar = avatar_data["url"]
    gender = avatar_data["gender"]
    
    # สุ่มว่าจะเป็นคนมาขายของ หรือมาซื้อของ (ถ้าไม่มีของในคลัง ต้องเป็นคนมาขายเท่านั้น)
    is_buyer = False
    if len(inventory) > 0 and random.random() > 0.5:
        is_buyer = True
        
    if is_buyer:
        current_game["transaction_type"] = "buy"
        item = random.choice(inventory)
        persona = random.choice(BUYER_PERSONAS)
        
        forger_level = shop.get("forger_level", 0)
        base_price_for_buyer = item.get("true_value", item["value"])
        
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
            
        current_game["item"] = item
        current_game["value"] = item["value"]
        current_game["true_value"] = base_price_for_buyer
        current_game["max_price"] = max_price
        
        system_prompt = f"""คุณกำลังเล่นเกม Roleplay: คุณคือลูกค้าที่เดินเข้ามาในร้านขายของมือสองเพื่อ 'ขอซื้อของ'
เพศของคุณ: {gender} (ต้องใช้สรรพนามและคำลงท้ายให้ตรงกับเพศ เช่น ชาย=ผม/ครับ หญิง=ฉัน/หนู/ค่ะ/คะ)
ของที่คุณสนใจจะซื้อคือ: {item['name']} (ราคากลางประมาณ: {item['value']} บาท)
ราคาสูงสุดที่คุณยอมจ่ายคือ: {max_price} บาท (ห้ามบอกตัวเลขนี้ให้เจ้าของร้านรู้เด็ดขาด!)
ลักษณะนิสัยของคุณ: {persona}

กฎการตอบ:
1. เล่นตามนิสัยอย่างเคร่งครัด พยายามต่อราคาให้ถูกที่สุดก่อน
2. ถ้าเจ้าของร้านเสนอราคามา และคุณพอใจ (ราคานั้น <= {max_price}) ให้คุณตอบตกลงซื้อ โดยพิมพ์คำว่า [BUY_ACCEPTED:ราคาที่เจ้าของร้านเสนอ] ท้ายประโยค
3. คำเตือน: ห้ามพิมพ์ [BUY_ACCEPTED] เด็ดขาด ถ้าคุณกำลังขอต่อราคา หรือเป็นฝ่ายเสนอราคาใหม่ คุณจะพิมพ์แท็กนี้ได้ก็ต่อเมื่อคุณ 'ยอมรับ' ราคาที่เจ้าของร้านเสนอมาล่าสุดเท่านั้น!
4. ถ้าแพงเกินไปรับไม่ได้จริงๆ ให้ด่า/บ่น แล้วใส่ [DEAL_REJECTED] ท้ายประโยค
5. เริ่มทักทาย ถามราคาของชิ้นนี้ และเสนอราคาที่คุณอยากซื้อทันที
6. ***สำคัญมาก: ตอบให้สั้นกระชับที่สุด ไม่เกิน 1-2 ประโยค พูดเหมือนคนคุยกันจริงๆ (ห้ามพิมพ์ยาวเวิ่นเว้อ)***
"""
        initial_user_msg = "สวัสดีครับ สนใจรับของชิ้นไหนในร้านดีครับ?"
    else:
        current_game["transaction_type"] = "sell"
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

        current_game["item"] = item
        current_game["value"] = item["value"] # ราคากลางที่โชว์ให้ผู้เล่นเห็น
        current_game["true_value"] = true_value # ราคาที่แท้จริง (อาจจะปลอม)
        current_game["min_price"] = min_price
        current_game["is_fake"] = is_fake
        current_game["appraised"] = False
        
        system_prompt = f"""คุณกำลังเล่นเกม Roleplay: คุณคือลูกค้าที่นำของมา 'ขายให้' โรงรับจำนำ
เพศของคุณ: {gender} (ต้องใช้สรรพนามและคำลงท้ายให้ตรงกับเพศ เช่น ชาย=ผม/ครับ หญิง=ฉัน/หนู/ค่ะ/คะ)
ของที่คุณนำมาขายคือ: {item['name']}
ลักษณะนิสัยของคุณ: {persona_desc}
ราคาต่ำสุดที่คุณยอมรับได้คือ: {min_price} บาท (ห้ามบอกให้เจ้าของร้านรู้!)

กฎการตอบ:
1. เล่นตามนิสัย พยายามเสนอราคาสูงๆ ก่อน
2. ถ้าเจ้าของร้านเสนอราคามา และคุณพอใจ (ราคานั้น >= {min_price}) ให้คุณตกลงขาย พิมพ์ [SELL_ACCEPTED:ราคาที่เจ้าของร้านเสนอ] ท้ายประโยค
3. คำเตือน: ห้ามพิมพ์ [SELL_ACCEPTED] เด็ดขาด ถ้าคุณกำลังขอต่อราคาเพิ่ม หรือเป็นฝ่ายเสนอราคาใหม่ คุณจะพิมพ์แท็กนี้ได้ก็ต่อเมื่อคุณ 'ยอมรับ' ราคาที่เจ้าของร้านเสนอมาล่าสุดเท่านั้น!
4. ถ้าถูกกดราคาน่าเกลียดรับไม่ได้ ให้ด่าแล้วใส่ [DEAL_REJECTED] 
5. เริ่มทักทาย เสนอขายของ และบอกราคาที่อยากได้ทันที
6. ***สำคัญมาก: ตอบให้สั้นกระชับที่สุด ไม่เกิน 1-2 ประโยค พูดเหมือนคนคุยกันจริงๆ (ห้ามพิมพ์ยาวเวิ่นเว้อ)***
"""
        initial_user_msg = "สวัสดีครับ วันนี้มีอะไรมาให้ทางร้านดูบ้างครับ?"

    current_game["history"] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_user_msg}
    ]
    
    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=current_game["history"],
            temperature=0.7,
            max_tokens=256,
        )
        response = completion.choices[0].message.content
        current_game["history"].append({"role": "assistant", "content": response})
        
        clean_msg = re.sub(r'\[(SELL|BUY)_ACCEPTED:\d+\]', '', response).replace("[DEAL_REJECTED]", "").strip()
        
        return jsonify({
            "status": "ok",
            "type": current_game["transaction_type"],
            "item": item,
            "avatar": avatar,
            "message": clean_msg
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/appraise", methods=["POST"])
def appraise_item():
    global current_game
    if current_game.get("transaction_type") != "sell":
        return jsonify({"status": "error", "message": "Can only appraise when buying"})
    
    if current_game.get("appraised"):
        return jsonify({"status": "error", "message": "Already appraised"})
        
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT money FROM shop WHERE id = 1')
    shop = c.fetchone()
    
    APPRAISAL_COST = 500
    if shop["money"] < APPRAISAL_COST:
        conn.close()
        return jsonify({"status": "error", "message": "Not enough money to appraise (Cost: 500)"})
        
    c.execute('UPDATE shop SET money = money - ? WHERE id = 1', (APPRAISAL_COST,))
    conn.commit()
    conn.close()
    
    current_game["appraised"] = True
    is_fake = current_game.get("is_fake", False)
    true_value = current_game.get("true_value", current_game["value"])
    
    return jsonify({
        "status": "ok",
        "is_fake": is_fake,
        "true_value": true_value,
        "cost": APPRAISAL_COST
    })

@app.route("/api/chat", methods=["POST"])
def chat():
    global current_game
    data = request.json
    user_message = data.get("message", "")
    
    current_game["history"].append({"role": "user", "content": user_message})
    
    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=current_game["history"],
            temperature=0.7,
            max_tokens=256,
        )
        bot_response = completion.choices[0].message.content
        current_game["history"].append({"role": "assistant", "content": bot_response})
        
        accepted = False
        rejected = "[DEAL_REJECTED]" in bot_response
        agreed_price = 0
        trans_type = current_game["transaction_type"]
        
        # ตรวจสอบการตอบตกลงซื้อหรือขาย
        match_sell = re.search(r'\[SELL_ACCEPTED:(\d+)\]', bot_response)
        match_buy = re.search(r'\[BUY_ACCEPTED:(\d+)\]', bot_response)
        
        if match_sell:
            accepted = True
            agreed_price = int(match_sell.group(1))
            bot_response = bot_response.replace(match_sell.group(0), "")
        elif match_buy:
            accepted = True
            agreed_price = int(match_buy.group(1))
            bot_response = bot_response.replace(match_buy.group(0), "")
            
        clean_response = bot_response.replace("[DEAL_REJECTED]", "").strip()
        
        # ถ้าสำเร็จ ให้อัปเดต DB
        if accepted:
            conn = get_db()
            c = conn.cursor()
            item = current_game["item"]
            
            if trans_type == "sell":
                # ลูกค้าขายของให้เรา -> เราเสียเงิน, ได้ของเข้าคลัง (ใช้มูลค่าจริง อาจจะปลอม), ได้ reputation
                c.execute('UPDATE shop SET money = money - ?, reputation = reputation + 1 WHERE id = 1', (agreed_price,))
                c.execute('INSERT INTO inventory (name, value, true_value, bought_price, image) VALUES (?, ?, ?, ?, ?)', 
                          (item["name"], current_game["value"], current_game["true_value"], agreed_price, item["image"]))
            else:
                # ลูกค้ามาซื้อของจากเรา -> เราได้เงิน, ของหายจากคลัง, ได้ reputation
                c.execute('UPDATE shop SET money = money + ?, reputation = reputation + 1 WHERE id = 1', (agreed_price,))
                c.execute('DELETE FROM inventory WHERE id = ?', (item.get("id", 0),))
                
            conn.commit()
            conn.close()

        return jsonify({
            "status": "ok",
            "message": clean_response,
            "accepted": accepted,
            "rejected": rejected,
            "price": agreed_price,
            "type": trans_type,
            "item": current_game["item"] if accepted else None
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
