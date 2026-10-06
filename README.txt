AQEL PREMIUM VAPE — v3 (Database lengkap + fitur baru)
======================================================

CARA JALANKAN
  pip install -r requirements.txt     (hanya Flask — pandas & scikit-learn tidak dipakai lagi)
  python app.py                       -> http://127.0.0.1:5000
  Admin: /admin  (user: aqeelamrza / password awal: 123456  -> WAJIB diganti di Admin > Pengaturan)

DATABASE (SQLite)  — file: database.db, skema: schema.sql
  settings              pengaturan toko (nama, ongkir, WA admin, info bayar)
  admins                akun admin (password di-hash)
  products              liquid  (+ stok, aktif)
  devices               pod/mod (+ stok, aktif)
  vouchers              kode promo (persen/nominal, kuota, masa berlaku)
  orders                pesanan (subtotal, diskon, ongkir, total, metode & status bayar, kurir, resi)
  order_items           item tiap pesanan (snapshot harga saat checkout)
  order_status_history  timeline status pesanan
  stock_logs            semua mutasi stok (jual, batal, edit admin)
  reviews               ulasan & rating liquid
  Buat manual: sqlite3 database.db < schema.sql   (atau biarkan app.py membuatnya otomatis)
  Data dari versi lama (v2) OTOMATIS dimigrasi saat app pertama dijalankan (pesanan lama -> order_items).

FITUR BARU
  Pelanggan : stok real-time & label "Habis", sortir (terlaris/rating/harga), pagination,
              ulasan & rating bintang, voucher, ongkir otomatis (gratis ongkir minimal belanja),
              pilihan pembayaran (Transfer/QRIS/COD), gerbang usia 18+, tracking status + resi di invoice,
              halaman sukses aman-refresh, konfirmasi via WhatsApp ke admin, email konfirmasi otomatis.
  Admin     : dashboard (grafik 7 hari, terlaris, stok menipis, pesanan terbaru, mutasi stok),
              atur stok langsung dari tabel, produk/device aktif-nonaktif, manajemen voucher,
              moderasi ulasan, status bayar/kurir/resi, riwayat status, pencarian & filter tanggal pesanan,
              export CSV (Excel-friendly), backup database 1 klik, ganti password, pengaturan toko.
  Keamanan  : password admin di-hash, CSRF token otomatis di semua form POST, batas percobaan login,
              secret key acak (tersimpan di .secret_key), harga & stok selalu dicek dari database
              (bukan dari cookie), checkout atomik (stok tidak bisa minus / oversell).
  Perbaikan : kode pesanan unik acak (ORD-YYYYMMDD-XXXXXX), cart_count konsisten, font yang tidak
              pernah termuat, pembatalan pesanan mengembalikan stok & kuota voucher.

EMAIL GMAIL (opsional)
  Windows CMD:  set GMAIL_USER=email@gmail.com   &   set GMAIL_PASS=app_password_16_karakter
  Buat App Password: myaccount.google.com/apppasswords. Tes di Admin > Pengaturan.

VARIABEL OPSIONAL: SECRET_KEY, DB_PATH, ADMIN_USERNAME, ADMIN_PASSWORD (hanya untuk admin pertama), FLASK_DEBUG=1
CATATAN: untuk produksi gunakan HTTPS + server WSGI (gunicorn/waitress), bukan app.run().
