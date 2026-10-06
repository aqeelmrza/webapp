-- =====================================================================
--  AQEL PREMIUM VAPE — Skema Database (SQLite 3)
--  Pakai manual :  sqlite3 database.db < schema.sql
--  Atau biarkan app.py membuatnya otomatis saat pertama dijalankan.
--  Admin default dibuat oleh app.py (password di-hash, bukan teks biasa).
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------- Pengaturan toko (key-value) ----------
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- ---------- Akun admin ----------
CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Katalog: liquid ----------
CREATE TABLE IF NOT EXISTS products (
    id        TEXT PRIMARY KEY,
    nama      TEXT NOT NULL,
    kategori  TEXT NOT NULL DEFAULT 'Fruity',
    rasa      TEXT NOT NULL DEFAULT '',
    nikotin   INTEGER NOT NULL DEFAULT 3  CHECK (nikotin >= 0),
    ukuran    INTEGER NOT NULL DEFAULT 30 CHECK (ukuran > 0),
    harga     INTEGER NOT NULL CHECK (harga >= 0),
    deskripsi TEXT NOT NULL DEFAULT '',
    stok      INTEGER NOT NULL DEFAULT 0  CHECK (stok >= 0),
    aktif     INTEGER NOT NULL DEFAULT 1  CHECK (aktif IN (0,1))
);

-- ---------- Katalog: device (pod / mod) ----------
CREATE TABLE IF NOT EXISTS devices (
    id         TEXT PRIMARY KEY,
    nama       TEXT NOT NULL,
    merk       TEXT NOT NULL DEFAULT '',
    tipe       TEXT NOT NULL DEFAULT 'Pod',
    harga_min  INTEGER NOT NULL CHECK (harga_min >= 0),
    harga_max  INTEGER NOT NULL CHECK (harga_max >= 0),
    warna_json TEXT NOT NULL DEFAULT '[]',
    deskripsi  TEXT NOT NULL DEFAULT '',
    stok       INTEGER NOT NULL DEFAULT 0 CHECK (stok >= 0),
    aktif      INTEGER NOT NULL DEFAULT 1 CHECK (aktif IN (0,1))
);

-- ---------- Voucher / kode promo ----------
CREATE TABLE IF NOT EXISTS vouchers (
    kode           TEXT PRIMARY KEY,
    tipe           TEXT NOT NULL CHECK (tipe IN ('persen','nominal')),
    nilai          INTEGER NOT NULL CHECK (nilai > 0),
    min_belanja    INTEGER NOT NULL DEFAULT 0,
    maks_diskon    INTEGER NOT NULL DEFAULT 0,      -- 0 = tanpa batas (untuk tipe persen)
    kuota          INTEGER NOT NULL DEFAULT 0,      -- 0 = tak terbatas
    terpakai       INTEGER NOT NULL DEFAULT 0,
    berlaku_sampai TEXT,                            -- 'YYYY-MM-DD' atau NULL
    aktif          INTEGER NOT NULL DEFAULT 1 CHECK (aktif IN (0,1))
);

-- ---------- Pesanan ----------
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_code   TEXT NOT NULL UNIQUE,
    nama         TEXT NOT NULL,
    alamat       TEXT NOT NULL,
    email        TEXT NOT NULL DEFAULT '',
    no_hp        TEXT NOT NULL DEFAULT '',
    subtotal     INTEGER NOT NULL DEFAULT 0,
    diskon       INTEGER NOT NULL DEFAULT 0,
    ongkir       INTEGER NOT NULL DEFAULT 0,
    total        INTEGER NOT NULL DEFAULT 0,
    voucher_kode TEXT REFERENCES vouchers(kode) ON UPDATE CASCADE ON DELETE SET NULL,
    metode_bayar TEXT NOT NULL DEFAULT 'Transfer',
    status_bayar TEXT NOT NULL DEFAULT 'Belum Bayar' CHECK (status_bayar IN ('Belum Bayar','Lunas')),
    status       TEXT NOT NULL DEFAULT 'Diproses'
                 CHECK (status IN ('Diproses','Dikirim','Selesai','Dibatalkan')),
    kurir        TEXT NOT NULL DEFAULT '',
    resi         TEXT NOT NULL DEFAULT '',
    catatan      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Item pesanan (snapshot harga & info saat checkout) ----------
CREATE TABLE IF NOT EXISTS order_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id  INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL CHECK (item_type IN ('liquid','device')),
    item_id   TEXT NOT NULL,
    nama      TEXT NOT NULL,
    merk      TEXT NOT NULL DEFAULT '',
    tipe      TEXT NOT NULL DEFAULT '',
    warna     TEXT NOT NULL DEFAULT '',
    kategori  TEXT NOT NULL DEFAULT '',
    rasa      TEXT NOT NULL DEFAULT '',
    nikotin   INTEGER NOT NULL DEFAULT 0,
    ukuran    INTEGER NOT NULL DEFAULT 0,
    harga     INTEGER NOT NULL CHECK (harga >= 0),
    qty       INTEGER NOT NULL DEFAULT 1 CHECK (qty > 0)
);

-- ---------- Riwayat status pesanan (timeline tracking) ----------
CREATE TABLE IF NOT EXISTS order_status_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status     TEXT NOT NULL,
    catatan    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Log mutasi stok ----------
CREATE TABLE IF NOT EXISTS stock_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type  TEXT NOT NULL CHECK (item_type IN ('liquid','device')),
    item_id    TEXT NOT NULL,
    perubahan  INTEGER NOT NULL,
    stok_akhir INTEGER NOT NULL,
    alasan     TEXT NOT NULL DEFAULT '',
    ref        TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Ulasan & rating produk liquid ----------
CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE ON UPDATE CASCADE,
    nama       TEXT NOT NULL,
    rating     INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    komentar   TEXT NOT NULL DEFAULT '',
    approved   INTEGER NOT NULL DEFAULT 1 CHECK (approved IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Index ----------
CREATE INDEX IF NOT EXISTS idx_products_kategori ON products(kategori, aktif);
CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created    ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_hp         ON orders(no_hp);
CREATE INDEX IF NOT EXISTS idx_items_order       ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_items_item        ON order_items(item_type, item_id);
CREATE INDEX IF NOT EXISTS idx_history_order     ON order_status_history(order_id);
CREATE INDEX IF NOT EXISTS idx_stock_item        ON stock_logs(item_type, item_id);
CREATE INDEX IF NOT EXISTS idx_reviews_product   ON reviews(product_id, approved);

-- @SEED_SETTINGS
-- ---------- Data awal: pengaturan ----------
INSERT OR IGNORE INTO settings(key,value) VALUES
 ('nama_toko',        'Aqel Premium Vape'),
 ('tagline',          'Premium Collection'),
 ('wa_admin',         '6281234567890'),
 ('ongkir_flat',      '15000'),
 ('gratis_ongkir_min','200000'),
 ('info_bayar',       'Transfer BCA 1234567890 a.n. Aqel Premium Vape — kirim bukti bayar via WhatsApp.'),
 ('stok_menipis',     '5');

-- @SEED_CATALOG
-- ---------- Data awal: liquid ----------
INSERT OR IGNORE INTO products(id,nama,kategori,rasa,nikotin,ukuran,harga,deskripsi,stok) VALUES
 ('F01','Foom Mango Ice','Fruity','Mango',15,30,110000,'Sensasi mangga segar dengan efek ice yang kuat.',40),
 ('F02','Foom Strawberry Ice','Fruity','Strawberry',15,30,110000,'Rasa strawberry manis dengan tarikan ice yang menyegarkan.',35),
 ('F03','Foom Grape Ice','Fruity','Grape',15,30,110000,'Anggur premium dengan sentuhan dingin yang memanjakan.',30),
 ('F04','Foom Vanilla Custard','Creamy','Vanilla',15,30,110000,'Creamy vanilla custard yang smooth dan penuh rasa.',25),
 ('P01','Crackz Banana Softcake','Creamy','Banana Cake',3,60,140000,'Banana cake lembut dengan rasa creamy yang nampol banget.',20),
 ('P02','Crackz Banana Softcake','Creamy','Banana Cake',6,60,140000,'Versi 6mg dari banana softcake klasik Crackz yang populer.',20),
 ('P03','Crackz Ice Cookies','Creamy','Cookies',3,60,135000,'Cookies creamy dengan sentuhan ice yang unik dan adiktif.',18),
 ('P04','Crackz Ice Cookies','Creamy','Cookies',6,60,135000,'Ice cookies 6mg, rasa penuh dengan tarikan ice yang smooth.',18),
 ('P05','Emkay Bule Bolu','Dessert','Bolu',3,60,150000,'Inspirasi kue bolu lokal dengan rasa dessert yang autentik.',15),
 ('P06','Emkay Bule Bolu','Dessert','Bolu',6,60,150000,'Bolu 6mg — richer flavor profile, cocok buat heavy vapers.',15),
 ('P07','Movi Es Buah','Fruity','Buah Segar',3,60,130000,'Campuran buah segar es campur yang bikin nagih sepanjang hari.',22),
 ('P08','Glu Ice Soursop','Fruity','Sirsak',3,60,145000,'Sirsak tropis dengan double ice — segar dan exotic.',12);

-- ---------- Data awal: device ----------
INSERT OR IGNORE INTO devices(id,nama,merk,tipe,harga_min,harga_max,warna_json,deskripsi,stok) VALUES
 ('D01','RELX Device','RELX','Pod',150000,300000,'["Black","White","Navy Blue","Red","Gold","Aurora Gradient"]','Pod vape RELX dengan teknologi FEELM, smooth draw, cocok untuk pemula hingga daily vaper.',25),
 ('D02','VEEV ONE','IQOS','Pod',99000,179000,'["Black","Silver","Blue","Purple"]','Pod sistem dari IQOS dengan pod pre-filled, mudah digunakan dan praktis dibawa kemana saja.',30),
 ('D03','Aspire Minican 2','Aspire','Pod',185000,185000,'["Black","Red","Green","Blue","Orange"]','Pod compact dari Aspire dengan coil mesh 0.8ohm, flavor luar biasa dalam ukuran yang ringkas.',20),
 ('D04','Aspire Vilter Pro','Aspire','Pod',750000,750000,'["Black","Gunmetal","Rose Gold"]','Pod premium Aspire dengan teknologi SaltSwitch, bisa switch antara freebase dan saltnic.',8),
 ('M01','SMOK RPM 40','SMOK','Mod',350000,450000,'["Black","Red","Gold","Rainbow","Blue"]','Pod mod SMOK RPM 40 dengan output 40W, layar TFT berwarna, dan kompatibel dengan berbagai coil RPM.',14),
 ('M02','OXVA VPrime','OXVA','Mod',400000,600000,'["Black Leather","Blue Leather","Brown Leather","Carbon Fiber"]','Pod mod premium OXVA dengan finishing leather mewah, output hingga 60W, dan chip OXVA terbaru.',10),
 ('M03','Vandy Vape Gemini','Vandy Vape','Mod',500000,800000,'["Black","Gunmetal","Silver","Gradient"]','Pod mod Vandy Vape Gemini dengan desain ergonomis, layar OLED, dan mendukung coil mesh MTL/DTL.',9),
 ('M04','Aspire Zero G','Aspire','Mod',999000,999000,'["Black","Silver","Grey"]','Mod/Pod hybrid premium Aspire dengan output hingga 80W, desain futuristik, dan layar sentuh penuh.',5);

-- ---------- Data awal: voucher contoh ----------
INSERT OR IGNORE INTO vouchers(kode,tipe,nilai,min_belanja,maks_diskon,kuota) VALUES
 ('WELCOME10','persen',10,100000,30000,0),
 ('HEMAT20K','nominal',20000,150000,0,100);
