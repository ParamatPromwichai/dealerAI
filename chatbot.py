import os
from groq import Groq
from dotenv import load_dotenv

# โหลดค่าต่างๆ จากไฟล์ .env อัตโนมัติ
load_dotenv()

def main():
    print("🤖 ยินดีต้อนรับสู่ Groq Chatbot! (พิมพ์ 'exit' เพื่อออก)")
    
    # สร้าง Client (จะดึง API Key จาก os.environ["GROQ_API_KEY"] อัตโนมัติถ้าตั้งไว้)
    # หรือใส่ตรงๆ: client = Groq(api_key="your_api_key_here")
    try:
        client = Groq()
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการเชื่อมต่อ: กรุณาตรวจสอบว่าได้ตั้งค่า GROQ_API_KEY แล้วหรือยัง\nรายละเอียด: {e}")
        return

    # เก็บประวัติการสนทนา
    chat_history = [
        {"role": "system", "content": "You are a helpful and friendly assistant. Please reply in Thai."}
    ]

    while True:
        user_input = input("\nคุณ: ")
        
        if user_input.lower() in ['exit', 'quit', 'ออก']:
            print("🤖 ลาก่อน!")
            break
            
        if not user_input.strip():
            continue

        # เพิ่มข้อความผู้ใช้ลงในประวัติ
        chat_history.append({"role": "user", "content": user_input})

        try:
            # เรียกใช้งาน Groq API (ใช้โมเดลที่บัญชีของคุณรองรับ)
            completion = client.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=chat_history,
                temperature=0.7,
                max_tokens=1024,
                top_p=1,
                stream=False,
            )

            # ดึงคำตอบ
            bot_response = completion.choices[0].message.content
            print(f"\n🤖 บอท: {bot_response}")

            # เพิ่มคำตอบของบอทลงในประวัติ
            chat_history.append({"role": "assistant", "content": bot_response})

        except Exception as e:
            print(f"\n❌ เกิดข้อผิดพลาดจาก API: {e}")

if __name__ == "__main__":
    main()
