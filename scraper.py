import os
import requests
import re
from datetime import datetime
from bs4 import BeautifulSoup
import google.generativeai as genai
from supabase import create_client, Client
import json
import tempfile

# ====== KONFIGURASI (Otomatis ambil dari GitHub Secrets) ======
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# ====== SETUP AI & DATABASE ======
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ====== FUNGSI: Scrape Pengumuman IDX ======
def get_latest_announcements():
    """Scrape pengumuman terbaru dari website IDX"""
    url = "https://www.idx.co.id/primary/pengumuman/"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        pdf_links = []
        for link in soup.find_all('a', href=True):
            href = link['href']
            if '.pdf' in href.lower():
                full_url = href if href.startswith('http') else f"https://www.idx.co.id{href}"
                pdf_links.append({
                    'url': full_url,
                    'name': link.get_text(strip=True) or 'Pengumuman'
                })
        
        # Ambil 20 terbaru saja untuk hemat kuota Gemini
        return pdf_links[:20]
    except Exception as e:
        print(f"❌ Error scraping IDX: {e}")
        return []

# ====== FUNGSI: Download PDF ke File Sementara ======
def download_pdf_to_temp(pdf_url):
    """Download PDF ke folder sementara untuk diupload ke Gemini"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        }
        response = requests.get(pdf_url, headers=headers, timeout=60)
        if response.status_code != 200:
            return None
        
        # Simpan ke file sementara
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_file.write(response.content)
        temp_file.close()
        return temp_file.name
    except Exception as e:
        print(f"❌ Error download PDF: {e}")
        return None

# ====== FUNGSI: Analisa PDF dengan Gemini AI ======
def analyze_pdf_with_gemini(pdf_url, pdf_name):
    """Kirim PDF ke Gemini untuk analisa M&A"""
    temp_path = None
    try:
        # Download PDF dulu
        temp_path = download_pdf_to_temp(pdf_url)
        if not temp_path:
            return None
        
        # Quick check: ada keyword M&A di nama file?
        name_lower = pdf_name.lower()
        keywords = ['akuisisi', 'merger', 'pengendali baru', 'pengambilalihan']
        
        # Upload file ke Gemini
        uploaded_file = genai.upload_file(temp_path)
        
        # Prompt untuk Gemini
        prompt = """Anda adalah analis saham profesional untuk Bursa Efek Indonesia (IDX).

Analisa dokumen pengumuman ini dan tentukan:
1. Apakah ada berita tentang: AKUISISI, MERGER, atau PENGENDALI BARU?
2. Jika ADA, ekstrak informasi berikut:
   - Kode saham (4 huruf kapital, contoh: BBCA, TLKM, ASII)
   - Nama perusahaan lengkap
   - Jenis transaksi: "akuisisi" / "merger" / "pengendali baru"
   - Nilai transaksi dalam Rupiah (jika disebutkan)
   - Status transaksi: "wacana" / "MoU" / "perjanjian pasti" / "selesai"
   - Ringkasan singkat (max 2 kalimat, bahasa Indonesia)

PENTING:
- Kode saham IDX SELALU 4 huruf kapital
- Jika dokumen BUKAN tentang M&A (misal: laporan keuangan, dividen, RUPS biasa), jawab "ada_ma": false
- Jika transaksi sudah SELESAI/REALISASI, status = "selesai"
- Jika masih rencana, status = "wacana" atau "MoU"
- Jawab HANYA dalam format JSON, tanpa teks lain

Format JSON:
{
  "ada_ma": true,
  "ticker": "XXXX",
  "company_name": "PT Nama Perusahaan Tbk",
  "type": "akuisisi",
  "value": "Rp 2.5 Triliun",
  "status": "MoU",
  "summary": "PT X akan mengakuisisi 51% saham PT Y senilai Rp 2.5T untuk ekspansi bisnis digital."
}

Jika TIDAK ada berita M&A:
{
  "ada_ma": false
}"""
        
        # Generate content dengan Gemini
        response = model.generate_content([prompt, uploaded_file])
        
        # Parse response
        result_text = response.text.strip()
        
        # Bersihkan markdown code block jika ada
        if '```json' in result_text:
            result_text = result_text.split('```json')[1].split('```')[0].strip()
        elif '```' in result_text:
            result_text = result_text.split('```')[1].split('```')[0].strip()
        
        # Parse JSON
        result = json.loads(result_text)
        
        # Hapus file sementara
        try:
            genai.delete_file(uploaded_file.name)
        except:
            pass
        
        if result.get('ada_ma') == True:
            return result
        else:
            return None
            
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse error: {e}")
        print(f"Response: {result_text[:200] if 'result_text' in locals() else 'No response'}")
        return None
    except Exception as e:
        print(f"❌ Error analyzing PDF: {e}")
        return None
    finally:
        # Cleanup temp file
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

# ====== FUNGSI: Kirim Notifikasi Telegram ======
def send_telegram_alert(analysis, pdf_url, pdf_name):
    """Kirim notifikasi ke Telegram"""
    type_emoji = {
        'akuisisi': '🏢',
        'merger': '🤝',
        'pengendali baru': '👤'
    }.get(analysis.get('type', '').lower(), '📊')
    
    status_emoji = {
        'wacana': '💭',
        'mou': '📝',
        'perjanjian pasti': '📋',
        'selesai': '✅'
    }.get(analysis.get('status', '').lower(), '📌')
    
    message = f"""{type_emoji} *ALERT: {analysis.get('type', 'M&A').upper()}* {type_emoji}

📊 *Ringkasan AI:*
• Saham: *{analysis.get('ticker', 'N/A')}*
• Perusahaan: {analysis.get('company_name', 'N/A')}
• Jenis: {analysis.get('type', 'N/A')}
• Nilai: {analysis.get('value', 'Tidak disebutkan')}
• Status: {status_emoji} *{analysis.get('status', 'N/A')}*

📝 *Analisa:*
{analysis.get('summary', 'N/A')}

🔗 [Baca PDF Lengkap]({pdf_url})
⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            print(f"✅ Telegram alert sent: {analysis.get('ticker')}")
        else:
            print(f"❌ Telegram failed: {response.text}")
    except Exception as e:
        print(f"❌ Telegram error: {e}")

# ====== FUNGSI: Simpan ke Database Supabase ======
def save_to_database(analysis, pdf_url, pdf_name):
    """Simpan hasil analisa ke Supabase"""
    try:
        data = {
            'pdf_url': pdf_url,
            'pdf_name': pdf_name,
            'ticker': analysis.get('ticker', '').upper(),
            'company_name': analysis.get('company_name'),
            'announcement_type': analysis.get('type'),
            'summary': analysis.get('summary'),
            'status': analysis.get('status'),
            'value_amount': analysis.get('value'),
            'ai_analysis': json.dumps(analysis, ensure_ascii=False),
            'published_date': datetime.now().date().isoformat()
        }
        
        supabase.table('announcements').insert(data).execute()
        print(f"✅ Saved to DB: {analysis.get('ticker')}")
    except Exception as e:
        print(f"❌ DB error: {e}")

# ====== FUNGSI: Cek Duplikat ======
def is_duplicate(pdf_url):
    """Cek apakah PDF sudah pernah diproses"""
    try:
        result = supabase.table('announcements').select('id').eq('pdf_url', pdf_url).execute()
        return len(result.data) > 0
    except:
        return False

# ====== MAIN PROGRAM ======
def main():
    print(f"🚀 Starting IDX M&A Scraper at {datetime.now()}")
    print("=" * 50)
    
    # Verifikasi credentials
    if not all([GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SUPABASE_URL, SUPABASE_KEY]):
        print("❌ ERROR: Ada credential yang kosong!")
        return
    
    # 1. Scrape pengumuman IDX
    announcements = get_latest_announcements()
    print(f"📄 Found {len(announcements)} announcements from IDX")
    
    if not announcements:
        print("⚠️  No announcements found. IDX website might be down.")
        return
    
    # 2. Analisa setiap PDF
    ma_found = 0
    for i, ann in enumerate(announcements, 1):
        pdf_url = ann['url']
        pdf_name = ann['name'][:50]  # Potong nama kalau kepanjangan
        
        print(f"\n[{i}/{len(announcements)}] 🔍 Checking: {pdf_name}")
        
        # Cek duplikat
        if is_duplicate(pdf_url):
            print(f"   ⏭️  Duplicate, skipping")
            continue
        
        # Analisa dengan Gemini
        analysis = analyze_pdf_with_gemini(pdf_url, ann['name'])
        
        if analysis:
            ma_found += 1
            print(f"   ✅ M&A FOUND: {analysis.get('ticker')} - {analysis.get('type')}")
            save_to_database(analysis, pdf_url, ann['name'])
            send_telegram_alert(analysis, pdf_url, pdf_url)
        else:
            print(f"   ❌ No M&A in this document")
    
    print("\n" + "=" * 50)
    print(f"✅ Scraper finished. Found {ma_found} M&A announcements.")
    print(f"⏰ Finished at: {datetime.now()}")

if __name__ == "__main__":
    main()
