-- ===================================================================
-- Supabase Schema for AI Dealer's Life
-- วิธีใช้งาน:
-- 1. ไปที่ Supabase Dashboard (https://supabase.com/dashboard)
-- 2. เลือกโปรเจกต์ของคุณ -> ไปที่เมนู "SQL Editor" ด้านซ้าย
-- 3. กด "New Query" วางโค้ดด้านล่างนี้ทั้งหมด แล้วกดปุ่ม "Run"
-- ===================================================================

-- 1. สร้างตาราง shop (เก็บข้อมูลร้านค้าของผู้เล่น)
CREATE TABLE IF NOT EXISTS public.shop (
    id BIGSERIAL PRIMARY KEY,
    pin TEXT UNIQUE NOT NULL,
    nickname TEXT,
    money BIGINT DEFAULT 100000,
    day INTEGER DEFAULT 1,
    reputation INTEGER DEFAULT 10,
    customers_left INTEGER DEFAULT 5,
    guard_level INTEGER DEFAULT 0,
    repair_level INTEGER DEFAULT 0,
    forger_level INTEGER DEFAULT 0,
    ad_level INTEGER DEFAULT 0,
    version INTEGER DEFAULT 0,
    active_customer TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. สร้างตาราง inventory (เก็บของในคลังสินค้า)
CREATE TABLE IF NOT EXISTS public.inventory (
    id BIGSERIAL PRIMARY KEY,
    shop_pin TEXT NOT NULL REFERENCES public.shop(pin) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value BIGINT NOT NULL DEFAULT 0,
    true_value BIGINT DEFAULT 0,
    bought_price BIGINT DEFAULT 0,
    image TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. สร้าง Index เพื่อเพิ่มความเร็วในการค้นหาด้วย PIN
CREATE INDEX IF NOT EXISTS idx_shop_pin ON public.shop(pin);
CREATE INDEX IF NOT EXISTS idx_inventory_shop_pin ON public.inventory(shop_pin);

-- 4. ปิด Row Level Security (RLS) เพื่อให้ Backend เข้าถึงข้อมูลได้
ALTER TABLE public.shop DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory DISABLE ROW LEVEL SECURITY;

-- 5. สร้าง Policy ปลดล็อคสิทธิ์เต็ม (เผื่อกรณีระบบ Supabase บังคับ RLS)
DROP POLICY IF EXISTS "Allow all for shop" ON public.shop;
CREATE POLICY "Allow all for shop" ON public.shop FOR ALL TO public USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow all for inventory" ON public.inventory;
CREATE POLICY "Allow all for inventory" ON public.inventory FOR ALL TO public USING (true) WITH CHECK (true);

-- 6. เพิ่มคอลัมน์ระบบเงินกู้และผ่อนชำระ (Loan & Installment System)
ALTER TABLE public.shop ADD COLUMN IF NOT EXISTS loan_remaining BIGINT DEFAULT 0;
ALTER TABLE public.shop ADD COLUMN IF NOT EXISTS loan_daily BIGINT DEFAULT 0;
ALTER TABLE public.shop ADD COLUMN IF NOT EXISTS loan_days_left INTEGER DEFAULT 0;
ALTER TABLE public.shop ADD COLUMN IF NOT EXISTS loan_principal BIGINT DEFAULT 0;
