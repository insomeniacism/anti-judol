# anti-judol-hunter

Sistem OSINT otonom, *async verifier*, *AI Judge* modular (OpenAI-compatible), *Local Indonesian ISP Block Checker*, dan *Fast Solo Submitter CLI* untuk kompetisi **Hackathon Berantas Web Ilegal di Indonesia**.

## Fitur Utama
- **Patuh Aturan Kompetisi:** Validasi ketat (`http`/`https`, tanpa port, tanpa user/pass, bukan IP/`localhost`, maks 300 karakter) dan normalisasi anti-duplikat persis aturan panitia (domain lowercase, buang `www.`, buang query/fragment/trailing `/`, path tetap *case-sensitive*) disimpan sebagai `UNIQUE` key di PostgreSQL.
- **Anti-Bentrok Cloud + Lokal:** Menggunakan Supabase PostgreSQL dengan `FOR UPDATE SKIP LOCKED` (`claim_pending_candidates` di [schema.sql](file:///home/thirafi/project/personal/anti-judol/schema.sql)), sehingga GitHub Actions dan PC lokal bisa memproses antrean secara bersamaan tanpa pernah memproses URL yang sama dua kali.
- **OSINT Harvester:** Mengambil domain segar dari `crt.sh`, `URLScan.io`, dan mode *real-time* `Certstream`.
- **AI Classifier Multi-Provider (konsep 9router):** Menggilir banyak provider free-tier (Groq, Cerebras, Gemini, OpenRouter, GitHub Models, Mistral, Zhipu GLM, NVIDIA, atau endpoint OpenAI-compatible apa pun) secara round-robin. Provider yang kena 429 otomatis di-*cooldown* dan request pindah ke provider lain. Halaman judol yang sangat jelas diputus oleh *keyword pre-filter* tanpa memanggil LLM. Konfigurasi lama `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` (mis. DeepSeek panitia) tetap didukung.
- **Cek Blokir ISP Indonesia:** Menandai mana situs ilegal yang benar-benar masih tembus dari jaringan Indonesia (belum diblokir TrustPositif/Komdigi).
- **Fast Solo Submitter CLI:** Menampilkan link terbaik satu per satu, otomatis menyalin ke *clipboard*, dan menandai `SUBMITTED` hanya dengan menekan `Enter`.

---

## Panduan Setup Cepat (Dari Nol Sampai Jalan Malam Ini)

### 1. Siapkan Database Supabase (Gratis)
1. Buat project baru di [supabase.com](https://supabase.com).
2. Buka **SQL Editor**, salin seluruh isi [schema.sql](file:///home/thirafi/project/personal/anti-judol/schema.sql), lalu klik **Run**.
3. Buka **Project Settings → Database → Connection string (URI)** (gunakan Session/Transaction Pooler atau Direct URI) dan salin URL-nya.

### 2. Install Dependency Lokal dengan `uv`
```bash
uv sync
cp .env.example .env
```
Edit `.env` dan isi:
- `DATABASE_URL` dari Supabase
- Minimal satu API key LLM gratis: `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `ZHIPU_API_KEY`, `NVIDIA_API_KEY`, atau `GHMODELS_API_KEY=$(gh auth token)`. Makin banyak key, makin lama kuota gratisnya bertahan. Link pendaftaran ada di `.env.example`.
- Uji semua key: `uv run anti-judol llm-check`

### 3. Jalankan Unit Test
```bash
uv run pytest
```

### 4. Jalankan Pipeline dari PC Lokal
```bash
# 1. Panen kandidat domain baru dari crt.sh & URLScan.io
uv run anti-judol harvest

# (Opsional) Tangkap domain baru real-time dari Certstream selama 2 menit
uv run anti-judol certstream --duration 120

# 2. Verifikasi HTTP & klasifikasi AI (bisa ubah --loops sesuai kebutuhan)
uv run anti-judol verify --loops 3

# 3. Cek dari PC Indonesia mana situs yang masih tembus ISP (belum diblokir Komdigi)
uv run anti-judol check-isp --limit 100

# 4. Lihat statistik antrean
uv run anti-judol stats

# 5. Mode Solo Submit Cepat (auto-copy ke clipboard, Enter = SUBMITTED & lanjut)
uv run anti-judol submit
```

### 5. Aktifkan Autopilot Gratis di GitHub Actions
1. Push repo ini ke GitHub.
2. Di repo GitHub, buka **Settings → Secrets and variables → Actions**, tambahkan *Repository secrets*:
   - `DATABASE_URL`
   - Key LLM yang kamu punya (yang tidak diisi otomatis dilewati): `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `ZHIPU_API_KEY`, `NVIDIA_API_KEY`
   - *(Opsional)* `LLM_PROVIDERS` (JSON provider custom) atau `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` (DeepSeek panitia)
   - Cara cepat dari terminal: `gh secret set GROQ_API_KEY` (lalu tempel key-nya)
   - *(Opsional)* `URLSCAN_API_KEY`
3. Buka tab **Actions → Anti-Judol Autopilot (Harvest & Verify) → Run workflow** untuk tes pertama kali. Setelah itu workflow otomatis jalan setiap 2 jam (`0 */2 * * *`).
