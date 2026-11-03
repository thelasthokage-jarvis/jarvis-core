import os
import re
import shutil
import pandas as pd

os.environ["PYMUPDF_NO_LAYOUT"] = "1"
import pymupdf as fitz

# Direktori kerja alat JarvisRenamer
TOOL_DIR = r"D:\JARVIS\JarvisTools\JarvisRenamer"
TARGET_FOLDER = os.path.join(TOOL_DIR, "Hasil_PDF")
EXCEL_MAPPING_PATH = os.path.join(TOOL_DIR, "pemetaan_nama.xlsx")

os.makedirs(TOOL_DIR, exist_ok=True)
os.makedirs(TARGET_FOLDER, exist_ok=True)

def load_name_mapping_from_excel():
    mapping = {}
    if not os.path.exists(EXCEL_MAPPING_PATH):
        try:
            df_default = pd.DataFrame({
                "Nama_Asli": ["ARI SUSANTO", "TAN HOEN JAN", "YULI NURHADI"],
                "Nama_Pengganti": ["KERIS GRAFIKA", "NARA OFFSET", "MITRA KARYA"]
            })
            df_default.to_excel(EXCEL_MAPPING_PATH, index=False)
        except Exception:
            pass

    if os.path.exists(EXCEL_MAPPING_PATH):
        try:
            df = pd.read_excel(EXCEL_MAPPING_PATH)
            for _, row in df.iterrows():
                nama_asli = str(row.iloc[0]).strip().upper()
                nama_pengganti = str(row.iloc[1]).strip()
                if nama_asli and nama_pengganti and nama_asli != "NAN" and nama_pengganti != "NAN":
                    mapping[nama_asli] = nama_pengganti
        except Exception:
            pass
    return mapping

def process_pdf_file(file_path):
    """Fungsi utama 'tangan' JarvisRenamer yang dipanggil oleh JARVIS"""
    if not os.path.exists(file_path):
        return None, "File PDF sumber tidak ditemukan."

    try:
        text = ""
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text("text", sort=True)
        text = text.replace('\xa0', ' ')
        text_upper = text.upper()

        doc_type = "UNKNOWN"
        new_filename = ""

        # 1. Modul Surat Perubahan BC 4.0
        if "SURAT PERUBAHAN BC 4.0" in text_upper:
            doc_type = "SURAT PERUBAHAN BC 4.0"
            tgl_match = re.search(r"SURAT\s+PERUBAHAN\s+BC\s+4\.0\s*[\r\n]*Nomor\s*:\s*Tanggal\s*([\d-]{8,10})", text, re.IGNORECASE)
            if not tgl_match:
                tgl_match = re.search(r"\b(\d{2}-\d{2}-\d{4})\b", text)
            tanggal = tgl_match.group(1).strip() if tgl_match else "00-00-0000"

            no_pengajuan_match = re.search(r"Nomor\s+Pengajuan\s*[:：|]?\s*([A-Za-z0-9]{15,30})", text, re.IGNORECASE)
            no_pengajuan = re.sub(r"\D", "", no_pengajuan_match.group(1).strip())[-6:] if no_pengajuan_match else "000000"

            no_pendaftaran_match = re.search(r"Nomor\s+Pendaftaran\s*[:：|]?\s*(\d{4,})", text, re.IGNORECASE)
            no_pendaftaran = no_pendaftaran_match.group(1).strip() if no_pendaftaran_match else "000000"

            new_filename = f"SURAT PERUBAHAN BC 4.0 - {no_pengajuan} - {no_pendaftaran} - {tanggal}.pdf"

        # 2. Modul SPPB (Surat Persetujuan Pemasukan Barang)
        elif "SURAT PERSETUJUAN PEMASUKAN BARANG" in text_upper or "SPPB" in text_upper:
            doc_type = "SPPB"
            tgl_match = re.search(r"Tang?gal.*?([\d-]{8,10})", text, re.IGNORECASE | re.DOTALL)
            tanggal = tgl_match.group(1).strip() if tgl_match else "00-00-0000"

            no_pengajuan_match = re.search(r"Nomor\s+Pengajuan.*?([A-Za-z0-9]{15,30})", text, re.IGNORECASE | re.DOTALL)
            no_pengajuan = no_pengajuan_match.group(1).strip() if no_pengajuan_match else "0000000000"

            no_pendaftaran_match = re.search(r"Nomor\s+Pendaftaran.*?(\d+)", text, re.IGNORECASE | re.DOTALL)
            no_pendaftaran = no_pendaftaran_match.group(1).strip() if no_pendaftaran_match else "000000"

            pengirim = "PERUSAHAAN"
            if "MITRA ABADI" in text_upper:
                pengirim = "MITRA ABADI SOLO"
            elif "TOKAI" in text_upper:
                pengirim = "TOKAI TEXPRINT INDONESIA"

            mapping = load_name_mapping_from_excel()
            for orig, repl in mapping.items():
                if orig in pengirim.upper():
                    pengirim = repl
                    break

            new_filename = f"SPPB-{pengirim}-{no_pendaftaran}-{tanggal}-{no_pengajuan}.pdf"

        # 3. Modul BC 4.0 (Pemberitahuan Pemasukan Barang)
        elif "PEMBERITAHUAN PEMASUKAN BARANG" in text_upper or "BC 4.0" in text_upper:
            doc_type = "BC 4.0"
            tgl_match = re.search(r"\bTanggal\s*[:：]?\s*(\d{2}-\d{2}-\d{4})", text, re.IGNORECASE)
            tanggal = tgl_match.group(1).strip() if tgl_match else "00-00-0000"

            no_pendaftaran_match = re.search(r"Nomor\s+Pendaftaran\s*[:：]?\s*(\d{4,})", text, re.IGNORECASE)
            no_pendaftaran = no_pendaftaran_match.group(1).strip() if no_pendaftaran_match else "000000"

            no_pengajuan_match = re.search(r"Nomor\s+Pengajuan\s*[:：]?\s*([A-Za-z0-9]{15,30})", text, re.IGNORECASE)
            no_pengajuan = re.sub(r"\D", "", no_pengajuan_match.group(1).strip())[-6:] if no_pengajuan_match else "000000"

            pengirim = "PERUSAHAAN"
            if "MITRA ABADI" in text_upper:
                pengirim = "MITRA ABADI SOLO"
            elif "TOKAI" in text_upper:
                pengirim = "TOKAI TEXPRINT INDONESIA"

            mapping = load_name_mapping_from_excel()
            for orig, repl in mapping.items():
                if orig in pengirim.upper():
                    pengirim = repl
                    break

            new_filename = f"BC-{pengirim}-{no_pendaftaran}-{no_pengajuan}-{tanggal}-0000-0000.pdf"
        else:
            doc_type = "UMUM"
            new_filename = "Processed_" + os.path.basename(file_path)

        # Sanitasi Karakter Nama File
        new_filename = re.sub(r'[\\/:*?"<>|]', "-", new_filename)
        new_filename = re.sub(r"\s+", " ", new_filename).strip(" .")
        
        target_file_path = os.path.join(TARGET_FOLDER, new_filename)
        
        # Penanganan nama duplikat
        base, ext = os.path.splitext(new_filename)
        counter = 1
        while os.path.exists(target_file_path):
            new_filename = f"{base}_{counter}{ext}"
            target_file_path = os.path.join(TARGET_FOLDER, new_filename)
            counter += 1

        shutil.copy2(file_path, target_file_path)
        return new_filename, f"Berhasil memproses dokumen tipe **{doc_type}** via JarvisRenamer."

    except Exception as e:
        return None, f"Gagal memproses file: {str(e)}"