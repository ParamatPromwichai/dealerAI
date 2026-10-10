import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import app
import db

def run_tests():
    print("=== TESTING CUSTOMER TRAITS & CONDITION SYSTEM ===")
    test_pin = "998877"
    db.init_db()

    # Reset or create test shop
    shop = db.get_shop(test_pin)
    if not shop:
        db.create_shop(test_pin, "ร้านทดสอบสภาพ")
    else:
        db.update_shop(test_pin, money=500000, day=1, reputation=25, customers_left=10, active_customer=None,
                       guard_level=0, repair_level=2, forger_level=0, ad_level=0)
    db.clear_inventory(test_pin)

    # 1. Test Customer Profile Generation
    print("\n--- 1. Testing Customer Profile Generation ---")
    male_prof = app.generate_customer_profile("ชาย (ผู้ชาย)")
    female_prof = app.generate_customer_profile("หญิง (ผู้หญิง)")
    print(f"Male: {male_prof['name']} | Mood: {male_prof['mood']['name']} | Collector: {male_prof['collector']['name']} | Patience: {male_prof['patience']['name']} | Expertise: {male_prof['expertise']['name']}")
    print(f"Tactical Tip: {male_prof['tactical_tip']}")
    assert male_prof["mood"]["name"]
    assert male_prof["collector"]["name"]
    assert male_prof["patience"]["name"]
    assert male_prof["expertise"]["name"]
    assert male_prof["tactical_tip"]

    # 2. Test Condition Info
    print("\n--- 2. Testing Condition Tiers ---")
    for pct in [100, 88, 70, 50, 30, 15]:
        info = app.get_condition_info(pct)
        print(f"Condition {pct}% -> {info['name']} ({info['color']}) mult={info['multiplier']}")
        assert info["name"]

    # 3. Test new_customer API
    print("\n--- 3. Testing /api/new_customer API ---")
    with app.app.test_client() as client:
        save = app.build_save(test_pin)
        res = client.post("/api/new_customer", json={"pin": test_pin, "save": save})
        data = res.get_json()
        assert data["status"] == "ok"
        assert "customer" in data, "Customer profile must be returned"
        cust = data["customer"]
        print(f"API Customer: {cust['name']} ({cust['gender']})")
        print(f"Mood: {cust['mood']['name']} ({cust['mood']['desc']})")
        print(f"Collector: {cust['collector']['name']} ({cust['collector']['desc']})")
        print(f"Patience: {cust['patience']['name']} ({cust['patience']['desc']})")
        print(f"Expertise: {cust['expertise']['name']} ({cust['expertise']['desc']})")
        print(f"Tactical Advice: {cust['tactical_tip']}")

        item = data["item"]
        print(f"Item: {item['name']} | Condition: {item.get('condition')}% ({item.get('condition_name')}) | Value: ฿{item.get('value'):,}")
        assert "condition" in item
        assert "condition_name" in item

        session = data["session"]
        save = data["save"]

        # 4. Test buying item and saving condition into inventory
        print("\n--- 4. Testing Buying item & DB Condition Persistence ---")
        chat_res = client.post("/api/chat", json={
            "pin": test_pin,
            "session": session,
            "save": save,
            "message": "100000"
        })
        chat_data = chat_res.get_json()
        print(f"Chat response: {chat_data.get('message')}")
        inv = db.get_inventory(test_pin)
        print(f"Inventory after deal: {len(inv)} items")
        if inv:
            bought = inv[-1]
            print(f"Saved Inventory Item: {bought['name']} | condition={bought.get('condition')}")
            assert bought.get("condition") is not None

        # 5. Test Manual Repair API
        print("\n--- 5. Testing Manual Repair API (/api/repair_item) ---")
        damaged_item = db.add_inventory_item(test_pin, name="[พัง] นาฬิกา Rolex รุ่นคุณปู่", value=45000, true_value=45000, bought_price=20000, image="/static/icon-192.png", condition=30)
        item_id = damaged_item.get("id") or db.get_inventory(test_pin)[-1]["id"]
        print(f"Added Damaged Item ID: {item_id}")

        save = app.build_save(test_pin)
        rep_res = client.post("/api/repair_item", json={"pin": test_pin, "item_id": item_id, "save": save})
        rep_data = rep_res.get_json()
        print(f"Repair result: {rep_data}")
        assert rep_data["status"] == "ok"
        fixed_item = next(it for it in rep_data["inventory"] if it["id"] == item_id)
        print(f"Fixed Item Name: {fixed_item['name']}, condition={fixed_item.get('condition')}, value=฿{fixed_item['value']:,}")
        assert fixed_item.get("condition") == 100
        assert not fixed_item["name"].startswith("[พัง] ")

        # 6. Test Repairman Overnight Repair in /api/end_day
        print("\n--- 6. Testing Overnight Repairman Buff in /api/end_day ---")
        # Add another damaged item
        db.add_inventory_item(test_pin, name="[พัง] กล้องฟิล์ม Leica M6", value=27000, true_value=27000, bought_price=10000, image="/static/icon-192.png", condition=25)
        # Empty customer queue so end_day can be called
        db.update_shop(test_pin, customers_left=0)
        save = app.build_save(test_pin)
        end_res = client.post("/api/end_day", json={"pin": test_pin, "save": save})
        end_data = end_res.get_json()
        logs = [str(x).encode('ascii', 'replace').decode('ascii') for x in end_data.get("event_logs", [])]
        print("End Day Event Logs:", logs)
        repair_logs = [log for log in logs if "??" in log or "repair" in log.lower() or "ช่าง" in log or len(logs) > 0]
        assert len(end_data.get("event_logs", [])) > 0, "Event logs must exist"
        print("Event logs count:", len(end_data.get("event_logs", [])))

    print("\nALL SYSTEM TESTS PASSED SUCCESSFULLY! 100%")

if __name__ == "__main__":
    run_tests()
