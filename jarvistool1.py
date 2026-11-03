import os
import re
import shutil
import json
import platform
import atexit
import requests
import sys
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, send_from_directory
from google import genai
from supabase import create_client, Client
from dotenv import load_dotenv

# =========================================================
# MEMUAT KONFIGURASI DARI FOLDER CONFIG (.env) SECARA MUTLAK
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # D:\JARVIS\core
PROJECT_ROOT = os.path.dirname(BASE_DIR)              # D:\JARVIS
ENV_PATH = os.path.join(PROJECT_ROOT, "config", ".env") # D:\JARVIS\config\.env

load_dotenv(dotenv_path=ENV_PATH)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("[JARVIS CORE] Berhasil terhubung ke Cloud Supabase!")
    except Exception as e:
        print(f"[JARVIS CORE] Gagal terhubung ke Supabase: {e}")
else:
    print(f"[JARVIS CORE] PERINGATAN: Supabase URL atau Key belum disetel!")
    print(f"[JARVIS CORE] File .env diperiksa di: {ENV_PATH}")

APP_NAME = "JARVIS"
DEVICE_NAME = "JARVIS_HOME"

LOG_DIR = os.path.join(BASE_DIR, "logs")
TOOLS_BASE_DIR = os.path.join(BASE_DIR, "JarvisTools")
UPLOAD_TEMP_FOLDER = os.path.join(BASE_DIR, "temp_upload")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(TOOLS_BASE_DIR, exist_ok=True)
os.makedirs(UPLOAD_TEMP_FOLDER, exist_ok=True)

CODE_PATH = os.path.abspath(__file__)

GEMINI_MODEL = "gemini-2.5-flash"

DRIVEINARY_API_KEY = "drv_live_1dac0b3c3dcd68b6d44d177dfda026aa8b71604f89391e50"
DRIVEINARY_UPLOAD_URL = "https://drive.yoepan.my.id/api/v1/upload"

app = Flask(__name__)

jarvis_hands = {}

def load_jarvis_hand(hand_name, subfolder_name):
    hand_path = os.path.join(TOOLS_BASE_DIR, subfolder_name)
    if os.path.exists(hand_path):
        if hand_path not in sys.path:
            sys.path.append(hand_path)
        try:
            mod = __import__(hand_name)
            jarvis_hands[hand_name] = mod
            print(f"[JARVIS CORE] Tangan '{hand_name}' terpasang.")
            return True
        except Exception as e:
            print(f"[JARVIS CORE] Gagal memuat tangan '{hand_name}': {e}")
    else:
        print(f"[JARVIS CORE] Tangan '{hand_name}' sedang dilepas / tidak ada.")
    return False

load_jarvis_hand("renamer", "JarvisRenamer")

def sync_to_driveinary(file_path, folder_name):
    if not os.path.exists(file_path):
        return
    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "application/octet-stream")}
            data = {"folder": folder_name}
            headers = {
                "Authorization": f"Bearer {DRIVEINARY_API_KEY}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            requests.post(DRIVEINARY_UPLOAD_URL, headers=headers, files=files, data=data, timeout=30)
    except Exception:
        pass

atexit.register(lambda: sync_to_driveinary(CODE_PATH, "JARVIS_Source_Code"))

def save_setting(key, value):
    if not supabase:
        return
    try:
        supabase.table("settings").upsert({"key": key, "value": value}).execute()
    except Exception as e:
        print(f"[DB ERROR] Gagal menyimpan setting: {e}")

def get_setting(key):
    if not supabase:
        return ""
    try:
        res = supabase.table("settings").select("value").eq("key", key).execute()
        if res.data:
            return res.data[0]["value"]
    except Exception as e:
        print(f"[DB ERROR] Gagal mengambil setting: {e}")
    return ""

def get_active_gemini_keys():
    keys = []
    for i in range(1, 6):
        k = get_setting(f"gemini_api_key_{i}").strip()
        if k:
            keys.append((i, k))
    if not keys:
        env_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if env_key:
            keys.append((1, env_key))
    return keys

def get_active_groq_keys():
    keys = []
    for i in range(1, 6):
        k = get_setting(f"groq_api_key_{i}").strip()
        if k:
            keys.append((i, k))
    if not keys:
        env_key = os.environ.get("GROQ_API_KEY", "").strip()
        if env_key:
            keys.append((1, env_key))
    return keys

def save_memory(content):
    if not supabase:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        supabase.table("memories").insert({"content": content, "created_at": now}).execute()
    except Exception as e:
        print(f"[DB ERROR] Gagal menyimpan memori: {e}")

def get_memories(limit=10):
    if not supabase:
        return []
    try:
        res = supabase.table("memories").select("id, content, created_at").order("id", desc=True).limit(limit).execute()
        return res.data if res.data else []
    except Exception as e:
        print(f"[DB ERROR] Gagal mengambil memori: {e}")
        return []

def save_chat(role, content):
    if not supabase:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        supabase.table("chat_history").insert({"role": role, "content": content, "created_at": now}).execute()
    except Exception as e:
        print(f"[DB ERROR] Gagal menyimpan chat history: {e}")

def ask_ai(message):
    memories = get_memories(limit=10)
    memory_text = "\n".join([f"- {m['content']} ({m['created_at']})" for m in memories]) if memories else "Belum ada memori khusus."

    system_instruction = (
        "Kamu adalah JARVIS, asisten pribadi digital milik Putro Harianto (dipanggil Put). "
        "Kamu dapat menggunakan tangan/tool yang terpasang (seperti JarvisRenamer) untuk membantu pekerjaan pengguna. "
        "Gunakan bahasa Indonesia yang alami, ramah, dan cerdas.\n\n"
        f"Database ingatan:\n{memory_text}"
    )

    groq_keys = get_active_groq_keys()
    if groq_keys:
        try:
            from groq import Groq
            for _, groq_key in groq_keys:
                try:
                    groq_client = Groq(api_key=groq_key)
                    completion = groq_client.chat.completions.create(
                        model="openai/gpt-oss-120b",
                        messages=[{"role": "system", "content": system_instruction}, {"role": "user", "content": message}],
                        temperature=0.7, max_tokens=1024
                    )
                    answer = completion.choices[0].message.content.strip()
                    if answer:
                        save_chat("user", message)
                        save_chat("model", answer)
                        return answer
                except Exception:
                    continue
        except ImportError:
            pass

    gemini_keys = get_active_gemini_keys()
    if not gemini_keys:
        return "API Key belum diatur di menu Pengaturan."

    for _, api_key in gemini_keys:
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=GEMINI_MODEL, contents=message,
                config={"system_instruction": system_instruction, "temperature": 0.7, "max_output_tokens": 1024}
            )
            answer = response.text.strip()
            if answer:
                save_chat("user", message)
                save_chat("model", answer)
                return answer + "\n\n*(Ditanggapi via Gemini AI)*"
        except Exception:
            continue

    return "Kendala koneksi AI atau kuota habis."

def process_command(message):
    text = message.strip()
    lower = text.lower()
    if not text:
        return "Silakan masukkan perintah."
    if any(word in lower for word in ["halo", "hai", "hello", "hi jarvis"]):
        active_tools_list = ", ".join(jarvis_hands.keys()) if jarvis_hands else "Tidak ada"
        return f"Halo tuan! JARVIS aktif. Tangan yang terpasang saat ini: [{active_tools_list}]."
    if lower.startswith("ingat bahwa "):
        save_memory(text[len("ingat bahwa "):].strip())
        return "Baik, informasi berhasil disimpan ke Cloud Supabase."
    return ask_ai(text)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JARVIS & Modular Tools (Cloud Supabase)</title>
<style>
body { margin: 0; background: #080d18; color: #e8f4ff; font-family: Arial, sans-serif; }
header { padding: 22px; text-align: center; background: #101a2c; border-bottom: 1px solid #193b58; position: relative; }
header h1 { margin: 0; color: #39c5ff; letter-spacing: 5px; }
header p { color: #91a8c3; margin-bottom: 0; }
.settings-btn { position: absolute; right: 20px; top: 25px; background: #193b58; color: #39c5ff; border: 1px solid #39c5ff; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: bold; }
.container { max-width: 1000px; margin: auto; padding: 20px; }
.status { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
.status-card { background: #111d30; border: 1px solid #1c3855; border-radius: 10px; padding: 16px; flex: 1; min-width: 180px; }
.status-card small { color: #8daac7; }
.status-card h3 { color: #39c5ff; margin-bottom: 0; }
.chat-box { background: #101a2c; border: 1px solid #1c3855; border-radius: 12px; padding: 18px; }
.chat-box h2 { color: #39c5ff; }
#chat { height: 400px; overflow-y: auto; background: #080f1d; padding: 15px; border-radius: 8px; margin-bottom: 15px; }
.message { padding: 12px 14px; margin-bottom: 12px; border-radius: 8px; white-space: pre-wrap; line-height: 1.5; }
.user { background: #163b5b; margin-left: 35px; }
.jarvis { background: #17283c; margin-right: 35px; border-left: 3px solid #39c5ff; }
.input-area { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
input[type="text"] { flex: 1; min-width: 200px; padding: 12px; border-radius: 7px; border: 1px solid #294766; background: #080f1d; color: white; font-size: 15px; }
button.send-btn, .upload-btn-label { border: none; border-radius: 7px; padding: 12px 16px; cursor: pointer; font-weight: bold; display: inline-flex; align-items: center; justify-content: center; }
button.send-btn { background: #087db5; color: white; }
.upload-btn-label { background: #059669; color: white; }
input[type="file"] { display: none; }
.download-link { display: inline-block; background: #10B981; color: white; padding: 6px 12px; border-radius: 5px; text-decoration: none; font-weight: bold; margin-top: 8px; }
.modal { display: none; position: fixed; z-index: 100; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); align-items: center; justify-content: center; }
.modal-content { background: #111d30; border: 1px solid #39c5ff; padding: 25px; border-radius: 12px; width: 90%; max-width: 500px; color: white; }
.modal-input-group { margin-bottom: 12px; }
.modal-input-group label { display: block; font-size: 13px; color: #91a8c3; margin-bottom: 4px; }
.modal-input-group input { width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #294766; background: #080f1d; color: white; }
</style>
</head>
<body>
<header>
    <h1>J.A.R.V.I.S</h1>
    <p>Modular Core & Supabase Cloud Memory</p>
    <button class="settings-btn" onclick="openSettings()">⚙️ PENGATURAN</button>
</header>

<div class="container">
    <div class="status">
        <div class="status-card"><small>STATUS OTAK</small><h3>ONLINE (CLOUD)</h3></div>
        <div class="status-card"><small>TANGAN AKTIF</small><h3 style="color: #10B981;" id="activeToolsDisplay">MEMUAT...</h3></div>
        <div class="status-card"><small>WAKTU</small><h3 id="clock">--:--:--</h3></div>
    </div>
    <div class="chat-box">
        <h2>COMMAND CENTER</h2>
        <div id="chat">
            <div class="message jarvis">JARVIS: Sistem utama terhubung ke Supabase. Ingatan dan riwayat kini tersinkronisasi secara real-time.</div>
        </div>
        <div class="input-area">
            <input type="text" id="userInput" placeholder="Ketik perintah atau 'ingat bahwa ...'..." autocomplete="off">
            <button class="send-btn" onclick="sendMessage()">KIRIM</button>
            <label class="upload-btn-label" for="pdfUpload">📁 UPLOAD PDF (RENAMER)</label>
            <input type="file" id="pdfUpload" accept=".pdf" onchange="uploadFile(this)">
        </div>
    </div>
</div>

<div id="settingsModal" class="modal">
    <div class="modal-content">
        <h3>Pengaturan API Key</h3>
        <div class="modal-input-group"><label>Groq API Key 1</label><input type="text" id="groqKey1"></div>
        <div class="modal-input-group"><label>Gemini API Key 1</label><input type="text" id="geminiKey1"></div>
        <div style="text-align: right; margin-top: 15px;">
            <button onclick="closeSettings()" style="background:#294766; color:white; padding:8px 14px; border:none; border-radius:5px; cursor:pointer;">Tutup</button>
            <button onclick="saveSettings()" style="background:#087db5; color:white; padding:8px 14px; border:none; border-radius:5px; cursor:pointer;">Simpan</button>
        </div>
    </div>
</div>

<script>
const chat = document.getElementById("chat");
const input = document.getElementById("userInput");

function addMessage(sender, htmlContent, type) {
    const div = document.createElement("div");
    div.className = "message " + type;
    div.innerHTML = "<b>" + sender + ":</b> " + htmlContent;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
}

async function sendMessage() {
    const message = input.value.trim();
    if (!message) return;
    addMessage("ANDA", message, "user");
    input.value = "";
    try {
        const res = await fetch("/api/chat", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({message: message})
        });
        const data = await res.json();
        addMessage("JARVIS", data.reply, "jarvis");
    } catch (e) {
        addMessage("JARVIS", "Gagal terhubung.", "jarvis");
    }
}

async function uploadFile(inputElement) {
    const file = inputElement.files[0];
    if (!file) return;
    addMessage("ANDA", "Mengunggah dokumen PDF: " + file.name + " ke JarvisRenamer...", "user");
    
    const formData = new FormData();
    formData.append("file", file);

    try {
        const res = await fetch("/api/renamer/upload", { method: "POST", body: formData });
        const data = await res.json();
        if (data.success) {
            let replyHtml = data.message + "<br><a class='download-link' href='" + data.download_url + "' download>📥 Download File Hasil (" + data.filename + ")</a>";
            addMessage("JARVIS", replyHtml, "jarvis");
        } else {
            addMessage("JARVIS", "Gagal: " + data.message, "jarvis");
        }
    } catch (e) {
        addMessage("JARVIS", "Kesalahan saat mengunggah file.", "jarvis");
    }
    inputElement.value = "";
}

input.addEventListener("keydown", function(e) { if (e.key === "Enter") sendMessage(); });

async function loadStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        document.getElementById("activeToolsDisplay").textContent = data.tools.join(", ") || "Tidak Ada";
    } catch(e){}
}
loadStatus();

function openSettings() { document.getElementById("settingsModal").style.display = "flex"; }
function closeSettings() { document.getElementById("settingsModal").style.display = "none"; }
async function saveSettings() {
    const k1 = document.getElementById("groqKey1").value.trim();
    const g1 = document.getElementById("geminiKey1").value.trim();
    await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({groq_1: k1, gemini_1: g1}) });
    alert("Disimpan ke Cloud!"); closeSettings();
}

setInterval(() => { document.getElementById("clock").textContent = new Date().toLocaleTimeString("id-ID"); }, 1000);
</script>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML_PAGE)

@app.route("/api/status")
def api_status():
    return jsonify({"tools": list(jarvis_hands.keys())})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    return jsonify({"reply": process_command(data.get("message", ""))})

@app.route("/api/renamer/upload", methods=["POST"])
def api_renamer_upload():
    if "renamer" not in jarvis_hands:
        return jsonify({"success": False, "message": "Modul JarvisRenamer sedang dilepas/tidak aktif."}), 400

    if "file" not in request.files:
        return jsonify({"success": False, "message": "Tidak ada file."}), 400
    
    file = request.files["file"]
    if file.filename == "" or not file.filename.lower().endswith(".pdf"):
        return jsonify({"success": False, "message": "File harus berformat PDF."}), 400

    temp_path = os.path.join(UPLOAD_TEMP_FOLDER, file.filename)
    file.save(temp_path)

    new_filename, message = jarvis_hands["renamer"].process_pdf_file(temp_path)

    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except:
        pass

    if new_filename:
        target_dir = jarvis_hands["renamer"].TARGET_FOLDER
        download_url = f"/api/renamer/download/{new_filename}"
        return jsonify({"success": True, "message": message, "filename": new_filename, "download_url": download_url})
    else:
        return jsonify({"success": False, "message": message}), 500

@app.route("/api/renamer/download/<path:filename>")
def api_renamer_download(filename):
    if "renamer" in jarvis_hands:
        target_dir = jarvis_hands["renamer"].TARGET_FOLDER
        return send_from_directory(target_dir, filename, as_attachment=True)
    return "Modul tidak aktif", 404

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if "gemini_1" in data: save_setting("gemini_api_key_1", data["gemini_1"])
        if "groq_1" in data: save_setting("groq_api_key_1", data["groq_1"])
        return jsonify({"success": True})
    return jsonify({"gemini_1": get_setting("gemini_api_key_1"), "groq_1": get_setting("groq_api_key_1")})

if __name__ == "__main__":
    print("=" * 60)
    print("    J.A.R.V.I.S (CORE) + SUPABASE CLOUD MEMORY")
    print("=" * 60)
    print(f"TOOLS DIRECTORY : {TOOLS_BASE_DIR}")
    print(f"ACTIVE HANDS    : {list(jarvis_hands.keys())}")
    print("SERVER          : http://127.0.0.1:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False)