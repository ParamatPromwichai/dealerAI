import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None
    Client = None

def is_supabase_configured() -> bool:
    """ตรวจสอบว่าได้ตั้งค่า SUPABASE_URL และ SUPABASE_KEY ใน .env หรือยัง (ไม่ใช่ค่า placeholder)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    if "your-project" in SUPABASE_URL or "your-anon" in SUPABASE_KEY:
        return False
    return bool(create_client)

_supabase_client = None

def get_supabase_client():
    global _supabase_client
    if not is_supabase_configured():
        return None
    if _supabase_client is None:
        try:
            _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
            print(f"[DB] เชื่อมต่อ Supabase: {SUPABASE_URL}")
        except Exception as e:
            print(f"[DB Error] ไม่สามารถเชื่อมต่อ Supabase ได้: {e}")
            return None
    return _supabase_client

# ---------------------------------------------------------------------------
# SQLite Fallback Configuration
# ---------------------------------------------------------------------------
if os.environ.get("VERCEL"):
    DB_FILE = '/tmp/pawnshop.db'
else:
    DB_FILE = 'pawnshop.db'

def get_sqlite_conn():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """สร้างตารางใน SQLite (ถ้าใช้ Supabase แนะนำให้รัน supabase_schema.sql ใน Supabase Dashboard)"""
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS shop (
            id INTEGER PRIMARY KEY,
            pin TEXT UNIQUE,
            nickname TEXT,
            money INTEGER DEFAULT 100000,
            day INTEGER DEFAULT 1,
            reputation INTEGER DEFAULT 10,
            customers_left INTEGER DEFAULT 5,
            guard_level INTEGER DEFAULT 0,
            repair_level INTEGER DEFAULT 0,
            forger_level INTEGER DEFAULT 0,
            ad_level INTEGER DEFAULT 0,
            version INTEGER DEFAULT 0,
            active_customer TEXT,
            loan_remaining INTEGER DEFAULT 0,
            loan_daily INTEGER DEFAULT 0,
            loan_days_left INTEGER DEFAULT 0,
            loan_principal INTEGER DEFAULT 0
        )''')
        # เพิ่มคอลัมน์เงินกู้สำหรับฐานข้อมูล SQLite เดิมที่สร้างไว้ก่อนหน้า
        for col, col_def in [
            ("loan_remaining", "INTEGER DEFAULT 0"),
            ("loan_daily", "INTEGER DEFAULT 0"),
            ("loan_days_left", "INTEGER DEFAULT 0"),
            ("loan_principal", "INTEGER DEFAULT 0")
        ]:
            try:
                c.execute(f"ALTER TABLE shop ADD COLUMN {col} {col_def}")
            except Exception:
                pass

        c.execute('''CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_pin TEXT,
            name TEXT,
            value INTEGER,
            true_value INTEGER DEFAULT 0,
            bought_price INTEGER DEFAULT 0,
            image TEXT
        )''')
        conn.commit()
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Unified Database Operations (รองรับทั้ง Supabase และ SQLite)
# ---------------------------------------------------------------------------

def get_shop(pin: str):
    """ค้นหาร้านค้าจาก PIN"""
    if not pin:
        return None
    sb = get_supabase_client()
    if sb:
        try:
            res = sb.table('shop').select('*').eq('pin', pin).execute()
            if res.data and len(res.data) > 0:
                row = res.data[0]
                if "loan_remaining" not in row or row.get("loan_remaining") is None:
                    # Supabase ยังไม่ได้เพิ่มคอลัมน์ loan -> ดึงจาก SQLite มาผสาน
                    conn = get_sqlite_conn()
                    try:
                        c = conn.cursor()
                        c.execute('SELECT loan_remaining, loan_daily, loan_days_left, loan_principal FROM shop WHERE pin = ?', (pin,))
                        sql_row = c.fetchone()
                        if sql_row:
                            row["loan_remaining"] = sql_row["loan_remaining"] or 0
                            row["loan_daily"] = sql_row["loan_daily"] or 0
                            row["loan_days_left"] = sql_row["loan_days_left"] or 0
                            row["loan_principal"] = sql_row["loan_principal"] or 0
                    except Exception:
                        pass
                    finally:
                        conn.close()
                return row
            return None
        except Exception as e:
            print(f"[DB Error Supabase get_shop] {e}")

    # Fallback to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM shop WHERE pin = ?', (pin,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def create_shop(pin: str, nickname: str):
    """สร้างร้านค้าใหม่เมื่อลงทะเบียน"""
    record = {
        "pin": pin,
        "nickname": nickname,
        "money": 100000,
        "day": 1,
        "reputation": 10,
        "customers_left": 5,
        "guard_level": 0,
        "repair_level": 0,
        "forger_level": 0,
        "ad_level": 0,
        "version": 1,
        "active_customer": None,
        "loan_remaining": 0,
        "loan_daily": 0,
        "loan_days_left": 0,
        "loan_principal": 0
    }
    sb = get_supabase_client()
    created_rec = record
    if sb:
        try:
            res = sb.table('shop').insert(record).execute()
            if res.data and len(res.data) > 0:
                created_rec = res.data[0]
        except Exception as e:
            err_str = str(e)
            if "loan_" in err_str:
                # กรณีใน Supabase ยังไม่ได้เพิ่มคอลัมน์ loan ให้ insert โดยตัดคอลัมน์ loan ออก
                no_loan = {k: v for k, v in record.items() if not k.startswith("loan_")}
                try:
                    res = sb.table('shop').insert(no_loan).execute()
                    if res.data and len(res.data) > 0:
                        created_rec = res.data[0]
                except Exception as ex2:
                    print(f"[DB Error Supabase create_shop fallback] {ex2}")
            print(f"[DB Error Supabase create_shop] {e}")

    # Mirror to SQLite (สำรองข้อมูลและช่วยกรณี Supabase ยังไม่มีคอลัมน์เงินกู้)
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO shop (pin, nickname, money, day, reputation, customers_left, version,
                     loan_remaining, loan_daily, loan_days_left, loan_principal)
                     VALUES (?, ?, 100000, 1, 10, 5, 1, 0, 0, 0, 0)''', (pin, nickname))
        conn.commit()
    except Exception as e_sql:
        print(f"[DB Error SQLite create_shop] {e_sql}")
    finally:
        conn.close()

    return created_rec

def update_shop(pin: str, **fields):
    """อัปเดตข้อมูลร้านค้า เช่น เงิน, วันที่, ชื่อเสียง, หนี้สิน ฯลฯ"""
    if not pin or not fields:
        return
    sb = get_supabase_client()
    if sb:
        try:
            sb.table('shop').update(fields).eq('pin', pin).execute()
        except Exception as e:
            err_str = str(e)
            if "loan_" in err_str:
                # ถ้า Supabase ยังไม่ได้เพิ่มคอลัมน์ loan ให้อัปเดตเฉพาะคอลัมน์อื่น
                clean_fields = {k: v for k, v in fields.items() if not k.startswith("loan_")}
                if clean_fields:
                    try:
                        sb.table('shop').update(clean_fields).eq('pin', pin).execute()
                    except Exception as ex2:
                        print(f"[DB Error Supabase update_shop fallback] {ex2}")
            print(f"[DB Error Supabase update_shop] {e}")

    # Fallback and mirror to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO shop (pin) VALUES (?)', (pin,))
        set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
        values = list(fields.values()) + [pin]
        c.execute(f"UPDATE shop SET {set_clause} WHERE pin = ?", values)
        conn.commit()
    except Exception as e_sql:
        print(f"[DB Error SQLite update_shop] {e_sql}")
    finally:
        conn.close()

def bump_shop_version(pin: str):
    """เพิ่ม version ของร้านขึ้น 1 ทุกครั้งที่มีการเปลี่ยนแปลงสถานะ"""
    if not pin:
        return 0
    sb = get_supabase_client()
    if sb:
        try:
            shop = get_shop(pin)
            if shop:
                new_v = (shop.get("version") or 0) + 1
                sb.table('shop').update({"version": new_v}).eq('pin', pin).execute()
                return new_v
            return 0
        except Exception as e:
            print(f"[DB Error Supabase bump_shop_version] {e}")

    # Fallback to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('UPDATE shop SET version = COALESCE(version, 0) + 1 WHERE pin = ?', (pin,))
        conn.commit()
    finally:
        conn.close()

def get_inventory(pin: str):
    """ดึงรายการสินค้าในคลังของร้านค้าตาม PIN"""
    if not pin:
        return []
    sb = get_supabase_client()
    if sb:
        try:
            res = sb.table('inventory').select('*').eq('shop_pin', pin).order('id').execute()
            return res.data or []
        except Exception as e:
            print(f"[DB Error Supabase get_inventory] {e}")

    # Fallback to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM inventory WHERE shop_pin = ? ORDER BY id', (pin,))
        return [dict(r) for r in c.fetchall()]
    finally:
        conn.close()

def add_inventory_item(pin: str, name: str, value: int, true_value: int, bought_price: int, image: str):
    """เพิ่มสินค้าลงคลัง"""
    item_record = {
        "shop_pin": pin,
        "name": name,
        "value": value,
        "true_value": true_value,
        "bought_price": bought_price,
        "image": image
    }
    sb = get_supabase_client()
    if sb:
        try:
            res = sb.table('inventory').insert(item_record).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
            return item_record
        except Exception as e:
            print(f"[DB Error Supabase add_inventory_item] {e}")

    # Fallback to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('''INSERT INTO inventory (name, value, true_value, bought_price, image, shop_pin)
                     VALUES (?, ?, ?, ?, ?, ?)''', (name, value, true_value, bought_price, image, pin))
        item_id = c.lastrowid
        conn.commit()
        item_record["id"] = item_id
        return item_record
    finally:
        conn.close()

def update_inventory_item(item_id: int, **fields):
    """อัปเดตข้อมูลสินค้า เช่น ชื่อ, ราคา (ตอนซ่อมของพัง)"""
    if not item_id or not fields:
        return False
    sb = get_supabase_client()
    if sb:
        try:
            res = sb.table('inventory').update(fields).eq('id', item_id).execute()
            return bool(res.data)
        except Exception as e:
            print(f"[DB Error Supabase update_inventory_item] {e}")

    # Fallback to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
        values = list(fields.values()) + [item_id]
        c.execute(f"UPDATE inventory SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True
    finally:
        conn.close()

def delete_inventory_item(pin: str, item_id: int = None, name: str = None, bought_price: int = None):
    """ลบสินค้าออกจากคลัง (เมื่อขายให้ลูกค้า)"""
    if not pin:
        return False
    sb = get_supabase_client()
    if sb:
        try:
            if item_id:
                res = sb.table('inventory').delete().eq('id', item_id).eq('shop_pin', pin).execute()
                if res.data and len(res.data) > 0:
                    return True
            if name:
                q = sb.table('inventory').select('id').eq('shop_pin', pin).eq('name', name)
                if bought_price is not None:
                    q = q.eq('bought_price', bought_price)
                found = q.limit(1).execute()
                if found.data and len(found.data) > 0:
                    del_id = found.data[0]['id']
                    del_res = sb.table('inventory').delete().eq('id', del_id).execute()
                    return bool(del_res.data)
            return False
        except Exception as e:
            print(f"[DB Error Supabase delete_inventory_item] {e}")

    # Fallback to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        if item_id:
            c.execute('DELETE FROM inventory WHERE id = ? AND shop_pin = ?', (item_id, pin))
            if c.rowcount > 0:
                conn.commit()
                return True
        if name:
            if bought_price is not None:
                c.execute('''DELETE FROM inventory WHERE id = (
                    SELECT id FROM inventory WHERE shop_pin = ? AND name = ? AND bought_price = ? LIMIT 1
                )''', (pin, name, bought_price))
            else:
                c.execute('''DELETE FROM inventory WHERE id = (
                    SELECT id FROM inventory WHERE shop_pin = ? AND name = ? LIMIT 1
                )''', (pin, name))
            if c.rowcount > 0:
                conn.commit()
                return True
        return False
    finally:
        conn.close()

def clear_inventory(pin: str):
    """ลบสินค้าทั้งหมดในคลังของร้าน (เมื่อล้มละลายหรือรีเซ็ตเริ่มใหม่)"""
    if not pin:
        return
    sb = get_supabase_client()
    if sb:
        try:
            sb.table('inventory').delete().eq('shop_pin', pin).execute()
        except Exception as e:
            print(f"[DB Error Supabase clear_inventory] {e}")

    # Fallback to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM inventory WHERE shop_pin = ?', (pin,))
        conn.commit()
    finally:
        conn.close()

def restore_shop_and_inventory(pin: str, shop_dict: dict, inv_list: list):
    """กู้คืนเซฟจาก signed token ของ Client (ทนต่อการ restart / ข้าม instance)"""
    if not pin:
        return
    sb = get_supabase_client()
    if sb:
        try:
            sb.table('inventory').delete().eq('shop_pin', pin).execute()
            sb.table('shop').delete().eq('pin', pin).execute()

            shop_record = {
                "pin": pin,
                "nickname": shop_dict.get("nickname"),
                "money": shop_dict.get("money", 100000),
                "day": shop_dict.get("day", 1),
                "reputation": shop_dict.get("reputation", 10),
                "customers_left": shop_dict.get("customers_left", 5),
                "guard_level": shop_dict.get("guard_level", 0),
                "repair_level": shop_dict.get("repair_level", 0),
                "forger_level": shop_dict.get("forger_level", 0),
                "ad_level": shop_dict.get("ad_level", 0),
                "version": shop_dict.get("version", 0),
                "active_customer": shop_dict.get("active_customer"),
                "loan_remaining": shop_dict.get("loan_remaining", 0),
                "loan_daily": shop_dict.get("loan_daily", 0),
                "loan_days_left": shop_dict.get("loan_days_left", 0),
                "loan_principal": shop_dict.get("loan_principal", 0)
            }
            try:
                sb.table('shop').insert(shop_record).execute()
            except Exception as ex:
                err_str = str(ex)
                if "loan_" in err_str:
                    clean_rec = {k: v for k, v in shop_record.items() if not k.startswith("loan_")}
                    sb.table('shop').insert(clean_rec).execute()
                else:
                    raise ex

            for it in inv_list:
                inv_rec = {
                    "name": it.get("name"),
                    "value": it.get("value", 0),
                    "true_value": it.get("true_value", 0),
                    "bought_price": it.get("bought_price", 0),
                    "image": it.get("image"),
                    "shop_pin": pin
                }
                sb.table('inventory').insert(inv_rec).execute()
            return
        except Exception as e:
            print(f"[DB Error Supabase restore_shop_and_inventory] {e}")

    # Fallback to SQLite
    conn = get_sqlite_conn()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM shop WHERE pin = ?', (pin,))
        c.execute('DELETE FROM inventory WHERE shop_pin = ?', (pin,))
        c.execute('''INSERT INTO shop (pin, nickname, money, day, reputation, customers_left,
                     guard_level, repair_level, forger_level, ad_level, version, active_customer,
                     loan_remaining, loan_daily, loan_days_left, loan_principal)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (pin, shop_dict.get("nickname"), shop_dict.get("money", 100000), shop_dict.get("day", 1),
                   shop_dict.get("reputation", 10), shop_dict.get("customers_left", 5), shop_dict.get("guard_level", 0),
                   shop_dict.get("repair_level", 0), shop_dict.get("forger_level", 0), shop_dict.get("ad_level", 0),
                   shop_dict.get("version", 0), shop_dict.get("active_customer"),
                   shop_dict.get("loan_remaining", 0), shop_dict.get("loan_daily", 0),
                   shop_dict.get("loan_days_left", 0), shop_dict.get("loan_principal", 0)))
        for it in inv_list:
            vals = (it.get("name"), it.get("value", 0), it.get("true_value", 0), it.get("bought_price", 0), it.get("image"), pin)
            try:
                c.execute('INSERT INTO inventory (id, name, value, true_value, bought_price, image, shop_pin) VALUES (?, ?, ?, ?, ?, ?, ?)',
                          (it.get("id"),) + vals)
            except sqlite3.IntegrityError:
                c.execute('INSERT INTO inventory (name, value, true_value, bought_price, image, shop_pin) VALUES (?, ?, ?, ?, ?, ?)', vals)
        conn.commit()
    finally:
        conn.close()
