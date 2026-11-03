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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

ENV_CANDIDATES = [
    os.path.join(PROJECT_ROOT, "config", ".env"),
    os.path.join(BASE_DIR, "config", ".env"),
    r"D:\JARVIS\config\.env",
]
ENV_PATH = next((p for p in ENV_CANDIDATES if os.path.isfile(p)), ENV_CANDIDATES[0])

load_dotenv(dotenv_path=ENV_PATH, override=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("[JARVIS CORE] Berhasil terhubung ke Cloud Supabase!")
    except Exception as e:
        supabase = None
        print(f"[JARVIS CORE] Gagal mengakses Supabase: {e}")
else:
    print("[JARVIS CORE] PERINGATAN: Supabase URL atau Key belum disetel!")

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

DRIVEINARY_API_KEY = os.environ.get("DRIVEINARY_API_KEY", "").strip()
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
    if not os.path.exists(file_path) or not DRIVEINARY_API_KEY:
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
        print("[DB ERROR] Setting tidak disimpan: Supabase belum terhubung.")
        return False
    try:
        supabase.table("settings").upsert({"key": key, "value": value}).execute()
        return True
    except Exception as e:
        print(f"[DB ERROR] Gagal menyimpan setting: {e}")
        return False

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

def save_memory(user_input, response_text=""):
    if not supabase:
        print("[DB ERROR] Memori tidak disimpan: Supabase belum terhubung.")
        return False
    try:
        supabase.table("memory_jarvis").insert({
            "user_input": user_input,
            "response": response_text
        }).execute()
        return True
    except Exception as e:
        print(f"[DB ERROR] Gagal menyimpan memori ke memory_jarvis: {e}")
        return False

def get_memories(limit=10):
    if not supabase:
        return []
    try:
        res = supabase.table("memory_jarvis").select("id, created_at, user_input, response").order("id", desc=True).limit(limit).execute()
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
    memory_text = "\n".join([f"- User: {m['user_input']} | JARVIS: {m['response']} ({m['created_at']})" for m in memories]) if memories else "Belum ada memori khusus."

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
                        save_memory(message, answer)
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
                save_memory(message, answer)
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
        content = text[len("ingat bahwa "):].strip()
        if not content:
            return "Informasi yang ingin disimpan masih kosong."
        if save_memory(content, "Simpan manual oleh pengguna"):
            return "Baik, informasi berhasil disimpan ke Cloud Supabase (memory_jarvis)."
        return "Maaf, memori belum berhasil disimpan. Periksa koneksi Supabase dan log CMD."
    return ask_ai(text)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JARVIS & Modular Tools (Supabase Cloud)</title>
<style>
body { margin: 0; background: #080d18; color: #e8f4ff; font-family: Arial, sans-serif; }
header { padding: 22px; text-align: center; background: #101a2c; border-bottom: 1px solid #193b58; position: relative; }
header h1 { margin: 0; color: #39c5ff; letter-spacing: 5px; }
header p { color: #91a8c3; margin-bottom: 0; }
.settings-btn { position: absolute; right: 20px; top: 25px; background: #193b58; color: #39c5ff; border: 1px solid #39c5ff; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: bold; }
.settings-btn:hover { background: #234d75; }
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
textarea { flex: 1; min-width: 200px; padding: 12px; border-radius: 7px; border: 1px solid #294766; background: #080f1d; color: white; font-size: 15px; resize: none; font-family: Arial, sans-serif; height: 45px; line-height: 1.4; }
button.send-btn, .upload-btn-label { border: none; border-radius: 7px; padding: 12px 16px; cursor: pointer; font-weight: bold; display: inline-flex; align-items: center; justify-content: center; height: 45px; }
button.send-btn { background: #087db5; color: white; }
button.send-btn:hover { background: #109bdc; }
.upload-btn-label { background: #059669; color: white; }
.upload-btn-label:hover { background: #047857; }
input[type="file"] { display: none; }
.download-link { display: inline-block; background: #10B981; color: white; padding: 6px 12px; border-radius: 5px; text-decoration: none; font-weight: bold; margin-top: 8px; }
.download-link:hover { background: #059669; }

/* Modal Settings & Tabs */
.modal { display: none; position: fixed; z-index: 100; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); align-items: center; justify-content: center; }
.modal-content { background: #111d30; border: 1px solid #39c5ff; padding: 25px; border-radius: 12px; width: 90%; max-width: 550px; color: white; max-height: 90vh; overflow-y: auto; }
.modal-content h3 { color: #39c5ff; margin-top: 0; }
.tabs { display: flex; gap: 10px; margin-bottom: 15px; border-bottom: 1px solid #1c3855; padding-bottom: 10px; }
.tab-btn { background: #162a45; color: #91a8c3; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: bold; }
.tab-btn.active { background: #087db5; color: white; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.modal-input-group { margin-bottom: 12px; }
.modal-input-group label { display: block; font-size: 13px; color: #91a8c3; margin-bottom: 4px; }
.modal-input-group input { width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #294766; background: #080f1d; color: white; }
.modal-buttons { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
.modal-buttons button { padding: 10px 16px; border-radius: 6px; border: none; font-weight: bold; cursor: pointer; }
.btn-save { background: #087db5; color: white; }
.btn-close { background: #294766; color: white; }
</style>
</head>
<body>
<header>
    <h1>J.A.R.V.I.S</h1>
    <p>Modular Core & Supabase Cloud Memory (5 Slot API)</p>
    <button class="settings-btn" onclick="openSettings()">⚙️ PENGATURAN API</button>
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
            <div class="message jarvis">JARVIS: Sistem terhubung ke Supabase. Mendukung 5 Slot API, Shift + Enter untuk baris baru, serta Paste file langsung.</div>
        </div>
        <div class="input-area">
            <textarea id="userInput" placeholder="Ketik perintah... (Shift + Enter untuk baris baru)" autocomplete="off" rows="1"></textarea>
            <button class="send-btn" onclick="sendMessage()">KIRIM</button>
            <label class="upload-btn-label" for="pdfUpload">📁 UPLOAD PDF (RENAMER)</label>
            <input type="file" id="pdfUpload" accept=".pdf" onchange="uploadFile(this)">
        </div>
    </div>
</div>

<!-- Modal Pengaturan 5 Slot API Key -->
<div id="settingsModal" class="modal">
    <div class="modal-content">
        <h3>Pengaturan Multi API Key (5 Slot)</h3>
        
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('groq', event)">⚡ API Groq (Utama)</button>
            <button class="tab-btn" onclick="switchTab('gemini', event)">🤖 API Gemini (Cadangan)</button>
        </div>

        <!-- Tab Groq -->
        <div id="tab-groq" class="tab-content active">
            <p style="font-size: 13px; color: #91a8c3;">Masukkan hingga 5 API Key Groq untuk prioritas utama.</p>
            <div class="modal-input-group"><label>Groq API Key 1</label><input type="text" id="groqKey1" placeholder="gsk_..."></div>
            <div class="modal-input-group"><label>Groq API Key 2</label><input type="text" id="groqKey2" placeholder="Groq Key 2..."></div>
            <div class="modal-input-group"><label>Groq API Key 3</label><input type="text" id="groqKey3" placeholder="Groq Key 3..."></div>
            <div class="modal-input-group"><label>Groq API Key 4</label><input type="text" id="groqKey4" placeholder="Groq Key 4..."></div>
            <div class="modal-input-group"><label>Groq API Key 5</label><input type="text" id="groqKey5" placeholder="Groq Key 5..."></div>
        </div>

        <!-- Tab Gemini -->
        <div id="tab-gemini" class="tab-content">
            <p style="font-size: 13px; color: #91a8c3;">Masukkan hingga 5 API Key Gemini untuk failover cadangan.</p>
            <div class="modal-input-group"><label>Gemini API Key 1</label><input type="text" id="geminiKey1" placeholder="Gemini Key 1..."></div>
            <div class="modal-input-group"><label>Gemini API Key 2</label><input type="text" id="geminiKey2" placeholder="Gemini Key 2..."></div>
            <div class="modal-input-group"><label>Gemini API Key 3</label><input type="text" id="geminiKey3" placeholder="Gemini Key 3..."></div>
            <div class="modal-input-group"><label>Gemini API Key 4</label><input type="text" id="geminiKey4" placeholder="Gemini Key 4..."></div>
            <div class="modal-input-group"><label>Gemini API Key 5</label><input type="text" id="geminiKey5" placeholder="Gemini Key 5..."></div>
        </div>

        <div class="modal-buttons">
            <button class="btn-close" onclick="closeSettings()">Tutup</button>
            <button class="btn-save" onclick="saveSettings()">Simpan Perubahan</button>
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

async function uploadFile(fileObj) {
    if (!fileObj) return;
    addMessage("ANDA", "Mengunggah dokumen PDF: " + fileObj.name + " ke JarvisRenamer...", "user");
    
    const formData = new FormData();
    formData.append("file", fileObj);

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
}

function handleFileInput(inputElement) {
    const file = inputElement.files[0];
    if (file) {
        uploadFile(file);
        inputElement.value = "";
    }
}

// Event Keydown: Enter untuk kirim, Shift + Enter untuk baris baru
input.addEventListener("keydown", function(event) {
    if (event.key === "Enter") {
        if (event.shiftKey) {
            return; // Biarkan baris baru
        } else {
            event.preventDefault();
            sendMessage();
        }
    }
});

// Fitur Paste File dari Explorer (seperti WhatsApp Web)
input.addEventListener("paste", function(event) {
    const items = (event.clipboardData || event.originalEvent.clipboardData).items;
    for (let index in items) {
        let item = items[index];
        if (item.kind === 'file') {
            let blob = item.getAsFile();
            if (blob) {
                event.preventDefault();
                uploadFile(blob);
            }
        }
    }
});

async function loadStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        document.getElementById("activeToolsDisplay").textContent = data.tools.join(", ") || "Tidak Ada";
    } catch(e){}
}
loadStatus();

function switchTab(tabName, event) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tabName).classList.add('active');
    event.currentTarget.classList.add('active');
}

async function openSettings() {
    document.getElementById("settingsModal").style.display = "flex";
    try {
        const res = await fetch("/api/settings");
        const data = await res.json();
        for (let i = 1; i <= 5; i++) {
            document.getElementById(`geminiKey${i}`).value = data[`gemini_${i}`] || "";
            document.getElementById(`groqKey${i}`).value = data[`groq_${i}`] || "";
        }
    } catch(e) {}
}

function closeSettings() { document.getElementById("settingsModal").style.display = "none"; }

async function saveSettings() {
    const keys = {};
    for (let i = 1; i <= 5; i++) {
        keys[`gemini_${i}`] = document.getElementById(`geminiKey${i}`).value.trim();
        keys[`groq_${i}`] = document.getElementById(`groqKey${i}`).value.trim();
    }
    try {
        const res = await fetch("/api/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(keys)
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
            alert(data.message || "Gagal menyimpan ke Supabase.");
            return;
        }
        alert("Pengaturan 5 Slot API berhasil disimpan ke Supabase.");
        closeSettings();
    } catch (e) {
        alert("Tidak dapat menghubungi server JARVIS.");
    }
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
        results = []
        for i in range(1, 6):
            if f"gemini_{i}" in data:
                results.append(save_setting(f"gemini_api_key_{i}", data[f"gemini_{i}"]))
            if f"groq_{i}" in data:
                results.append(save_setting(f"groq_api_key_{i}", data[f"groq_{i}"]))
        success = all(results) if results else False
        return jsonify({
            "success": success,
            "message": "Pengaturan tersimpan ke Supabase." if success else "Pengaturan gagal disimpan."
        }), (200 if success else 503)
    
    keys_data = {}
    for i in range(1, 6):
        keys_data[f"gemini_{i}"] = get_setting(f"gemini_api_key_{i}")
        keys_data[f"groq_{i}"] = get_setting(f"groq_api_key_{i}")
    return jsonify(keys_data)

if __name__ == "__main__":
    print("=" * 60)
    print("    J.A.R.V.I.S (CORE) + SUPABASE CLOUD + MULTI API & TOOLS")
    print("=" * 60)
    print(f"TOOLS DIRECTORY : {TOOLS_BASE_DIR}")
    print(f"ACTIVE HANDS    : {list(jarvis_hands.keys())}")
    
    # Mengambil port dari environment variable (untuk cloud) atau default ke 5000 (untuk lokal)
    port = int(os.environ.get("PORT", 5000))
    print(f"SERVER          : http://0.0.0.0:{port}")
    print("=" * 60)
    
    # host="0.0.0.0" agar bisa diakses dari jaringan luar/cloud
    app.run(host="0.0.0.0", port=port, debug=False)