# anti-judol-hunter

Sistem OSINT otonom, *async verifier*, *AI Judge* modular (OpenAI-compatible), *Local Indonesian ISP Block Checker*, dan *Fast Solo Submitter CLI* untuk kompetisi **Hackathon Berantas Web Ilegal di Indonesia**.

## Fitur Utama
- **Patuh Aturan Kompetisi:** Validasi ketat (`http`/`https`, tanpa port, tanpa user/pass, bukan IP/`localhost`, maks 300 karakter) dan normalisasi anti-duplikat persis aturan panitia (domain lowercase, buang `www.`, buang query/fragment/trailing `/`, path tetap *case-sensitive*) disimpan sebagai `UNIQUE` key di PostgreSQL.
- **Anti-Bentrok Cloud + Lokal:** Menggunakan Supabase PostgreSQL dengan `FOR UPDATE SKIP LOCKED` (`claim_pending_candidates` di [schema.sql](file:///home/thirafi/project/personal/anti-judol/schema.sql)), sehingga GitHub Actions dan PC lokal bisa memproses antrean secara bersamaan tanpa pernah memproses URL yang sama dua kali.
- **OSINT Harvester:** Mengambil domain segar dari `crt.sh`, `URLScan.io`, dan mode *real-time* `Certstream`.
- **AI Classifier Modular:** Menggunakan SDK `openai` dengan `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` dari `.env`. Siap dites dengan Groq/Gemini sekarang, dan tinggal ganti ke DeepSeek Uncensored panitia saat lomba tanpa ubah kode.
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
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (misal Groq `https://api.groq.com/openai/v1` + `llama-3.3-70b-versatile`, atau Gemini OpenAI endpoint).

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
   - `LLM_BASE_URL`
   - `LLM_API_KEY`
   - `LLM_MODEL`
   - *(Opsional)* `URLSCAN_API_KEY`
3. Buka tab **Actions → Anti-Judol Autopilot (Harvest & Verify) → Run workflow** untuk tes pertama kali. Setelah itu workflow otomatis jalan setiap 2 jam (`0 */2 * * *`).
