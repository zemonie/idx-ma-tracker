import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import datetime, timedelta

# ====== KONFIGURASI ======
st.set_page_config(
    page_title="IDX M&A Tracker",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Ambil credentials dari Streamlit secrets
try:
    SUPABASE_URL = st.secrets.get('SUPABASE_URL')
    SUPABASE_KEY = st.secrets.get('SUPABASE_KEY')
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("❌ Konfigurasi Supabase belum di-set di Streamlit secrets!")
        st.stop()
except Exception as e:
    st.error(f"❌ Error loading secrets: {e}")
    st.stop()

# Setup Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ====== FUNGSI: Load Data ======
@st.cache_data(ttl=300)  # Cache 5 menit
def get_all_data():
    """Ambil semua data dari database"""
    result = supabase.table('announcements').select('*').order('created_at', desc=True).execute()
    if not result.data:
        return pd.DataFrame()
    return pd.DataFrame(result.data)

# ====== HEADER ======
st.title("📊 IDX M&A Tracker Dashboard")
st.markdown("**AI-powered tracker untuk berita akuisisi, merger, dan pengendali baru di Bursa Efek Indonesia**")
st.markdown("---")

# ====== LOAD DATA ======
df = get_all_data()

if df.empty:
    st.warning("📭 Belum ada data. Tunggu scraper berjalan pertama kali (bisa 15-30 menit).")
    st.info("💡 **Tips:** Klik tombol 'Rerun' di kanan atas untuk refresh data manual.")
    if st.button("🔄 Rerun"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

# Konversi tipe data tanggal
df['published_date'] = pd.to_datetime(df['published_date']).dt.date
df['created_at'] = pd.to_datetime(df['created_at'])

# ====== SIDEBAR FILTER ======
st.sidebar.header("🔍 Filter Data")

# Filter rentang tanggal
min_date = df['published_date'].min()
max_date = df['published_date'].max()

date_range = st.sidebar.date_input(
    "Rentang Tanggal",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date
)

# Filter jenis transaksi
types = ['Semua'] + df['announcement_type'].dropna().unique().tolist()
selected_type = st.sidebar.selectbox("Jenis Transaksi", types)

# Filter status
statuses = ['Semua'] + df['status'].dropna().unique().tolist()
selected_status = st.sidebar.selectbox("Status", statuses)

# Search ticker
search_ticker = st.sidebar.text_input("Cari Kode Saham", "").upper().strip()

# Tombol reset filter
if st.sidebar.button("🔄 Reset Filter"):
    st.rerun()

# ====== APLIKASI FILTER ======
filtered_df = df.copy()

if len(date_range) == 2:
    filtered_df = filtered_df[
        (filtered_df['published_date'] >= date_range[0]) &
        (filtered_df['published_date'] <= date_range[1])
    ]

if selected_type != 'Semua':
    filtered_df = filtered_df[filtered_df['announcement_type'] == selected_type]

if selected_status != 'Semua':
    filtered_df = filtered_df[filtered_df['status'] == selected_status]

if search_ticker:
    filtered_df = filtered_df[filtered_df['ticker'].str.upper().str.contains(search_ticker, na=False)]

# ====== STATISTIK (4 KOLOM) ======
st.subheader("📈 Statistik")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Total Pengumuman", len(filtered_df))

with col2:
    akuisisi_count = len(filtered_df[filtered_df['announcement_type'] == 'akuisisi'])
    st.metric("🏢 Akuisisi", akuisisi_count)

with col3:
    merger_count = len(filtered_df[filtered_df['announcement_type'] == 'merger'])
    st.metric("🤝 Merger", merger_count)

with col4:
    pengendali_count = len(filtered_df[filtered_df['announcement_type'] == 'pengendali baru'])
    st.metric("👤 Pengendali Baru", pengendali_count)

st.markdown("---")

# ====== TABEL DATA ======
st.subheader(f"📋 Daftar Pengumuman M&A ({len(filtered_df)} data)")

if filtered_df.empty:
    st.info("Tidak ada data yang sesuai dengan filter Anda.")
else:
    for idx, row in filtered_df.iterrows():
        # Emoji berdasarkan tipe
        type_emoji = {
            'akuisisi': '🏢',
            'merger': '🤝',
            'pengendali baru': '👤'
        }.get(str(row['announcement_type']).lower(), '📊')
        
        # Emoji berdasarkan status
        status_emoji = {
            'wacana': '💭',
            'mou': '📝',
            'perjanjian pasti': '📋',
            'selesai': '✅'
        }.get(str(row['status']).lower(), '📌')
        
        with st.expander(f"{type_emoji} **{row['ticker']}** - {row['company_name']} ({row['announcement_type']})"):
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown(f"**Kode Saham:** `{row['ticker']}`")
                st.markdown(f"**Perusahaan:** {row['company_name']}")
                st.markdown(f"**Jenis:** {row['announcement_type']}")
                st.markdown(f"**Nilai:** {row['value_amount'] or 'Tidak disebutkan'}")
            
            with col2:
                st.markdown(f"**Status:** {status_emoji} {row['status']}")
                st.markdown(f"**Tanggal:** {row['published_date']}")
                st.markdown(f"**Waktu Scan:** {row['created_at'].strftime('%Y-%m-%d %H:%M')}")
            
            st.markdown("### 📝 Ringkasan AI")
            st.info(row['summary'])
            
            st.markdown(f"🔗 **[Baca PDF Lengkap]({row['pdf_url']})**")

# ====== EXPORT DATA ======
st.markdown("---")
st.subheader("💾 Export Data")

col1, col2 = st.columns([1, 3])
with col1:
    csv = filtered_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name=f"idx_ma_tracker_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        type="primary"
    )

with col2:
    st.caption(f"Export {len(filtered_df)} baris data dengan filter yang sedang aktif")

# ====== FOOTER ======
st.markdown("---")
st.markdown(
    """
    <div style='text-align: center; color: gray; padding: 20px;'>
    <p><strong>IDX M&A Tracker v1.0</strong></p>
    <p>Powered by 🤖 Google Gemini AI | 💾 Supabase | 📊 Streamlit</p>
    <p>Data auto-update setiap 15 menit via GitHub Actions</p>
    </div>
    """,
    unsafe_allow_html=True
)
