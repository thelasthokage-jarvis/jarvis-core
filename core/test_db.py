import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Memuat file .env dari folder config
load_dotenv(dotenv_path="../config/.env")

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(url, key)

# Coba masukkan data percobaan
data = {
    "user_input": "Halo Jarvis, tes koneksi",
    "response": "Halo Tuan, sistem online dan siap!"
}
response = supabase.table("memory_jarvis").insert(data).execute()
print("Berhasil terhubung ke Supabase! Respon:", response)