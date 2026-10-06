"""
AQEL PREMIUM VAPE — Flask + SQLite (v3)
Database: lihat schema.sql  |  Jalankan: python app.py
"""
import csv, html, io, json, os, re, secrets, smtplib, sqlite3, tempfile, threading, time
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from urllib.parse import quote

from flask import (Flask, abort, flash, g, make_response, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "database.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "aqeelamrza")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")
STATUSES = ["Diproses", "Dikirim", "Selesai", "Dibatalkan"]
PAY_METHODS = ["Transfer Bank", "QRIS", "COD"]
PER_PAGE, ORDERS_PER_PAGE = 12, 20
TABLE = {"liquid": "products", "device": "devices"}


def _secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(BASE_DIR, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    return open(path).read().strip()


app = Flask(__name__)
app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=1 * 1024 * 1024)

# ─────────────────────────────────────────  DATABASE  ─────────────────────────────────────────
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def init_db():
    conn = connect()
    had_products = bool(_cols(conn, "products"))
    # --- migrasi dari versi lama (v2) ---
    legacy_orders = "items_json" in _cols(conn, "orders")
    if legacy_orders:
        conn.execute("ALTER TABLE orders RENAME TO orders_legacy")
    if had_products and "stok" not in _cols(conn, "products"):
        conn.execute("ALTER TABLE products ADD COLUMN stok INTEGER NOT NULL DEFAULT 50 CHECK (stok >= 0)")
        conn.execute("ALTER TABLE products ADD COLUMN aktif INTEGER NOT NULL DEFAULT 1")
    if _cols(conn, "devices") and "stok" not in _cols(conn, "devices"):
        conn.execute("ALTER TABLE devices ADD COLUMN stok INTEGER NOT NULL DEFAULT 20 CHECK (stok >= 0)")
        conn.execute("ALTER TABLE devices ADD COLUMN aktif INTEGER NOT NULL DEFAULT 1")
    # --- buat skema + seed ---
    sql = open(SCHEMA_PATH, encoding="utf-8").read()
    ddl, rest = sql.split("-- @SEED_SETTINGS")
    seed_settings, seed_catalog = rest.split("-- @SEED_CATALOG")
    conn.executescript(ddl)
    conn.executescript(seed_settings)
    if not had_products:
        conn.executescript(seed_catalog)
    if legacy_orders:
        _migrate_legacy_orders(conn)
    if not conn.execute("SELECT 1 FROM admins").fetchone():
        conn.execute("INSERT INTO admins(username,password_hash) VALUES (?,?)",
                     (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD)))
    conn.commit()
    conn.close()


def _migrate_legacy_orders(conn):
    for r in conn.execute("SELECT * FROM orders_legacy").fetchall():
        r = dict(r)
        paid = "Lunas" if r["status"] == "Selesai" else "Belum Bayar"
        conn.execute("""INSERT INTO orders(id,order_code,nama,alamat,email,no_hp,subtotal,total,status,
                        status_bayar,catatan,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (r["id"], r["order_code"], r["nama"], r["alamat"], r["email"] or "", r.get("no_hp") or "",
                      r["total"], r["total"], r["status"], paid, r.get("catatan") or "",
                      r["created_at"], r["created_at"]))
        try:
            items = json.loads(r["items_json"] or "[]")
        except ValueError:
            items = []
        for it in items:
            conn.execute("""INSERT INTO order_items(order_id,item_type,item_id,nama,merk,tipe,warna,kategori,
                            rasa,nikotin,ukuran,harga,qty) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (r["id"], it.get("type", "liquid"), str(it.get("id", "")), it.get("nama", ""),
                          it.get("merk", ""), it.get("tipe", ""), it.get("warna", ""), it.get("kategori", ""),
                          it.get("rasa", ""), int(it.get("nikotin", 0) or 0), int(it.get("ukuran", 0) or 0),
                          int(it.get("harga", 0) or 0), max(1, int(it.get("qty", 1) or 1))))
        conn.execute("INSERT INTO order_status_history(order_id,status,catatan,created_at) VALUES (?,?,?,?)",
                     (r["id"], r["status"], "Dimigrasi dari versi lama", r["created_at"]))
    conn.execute("DROP TABLE orders_legacy")


init_db()

# ─────────────────────────────────────────  HELPERS  ─────────────────────────────────────────
DEFAULT_SETTINGS = {"nama_toko": "Aqel Premium Vape", "tagline": "Premium Collection", "wa_admin": "",
                    "ongkir_flat": "15000", "gratis_ongkir_min": "200000", "info_bayar": "", "stok_menipis": "5"}


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in db().execute("SELECT key,value FROM settings")})
    return s


def setting_int(s, key):
    try:
        return int(s.get(key) or 0)
    except ValueError:
        return int(DEFAULT_SETTINGS.get(key, 0))


def rp(val):
    return "Rp " + f"{int(val or 0):,}".replace(",", ".")


app.jinja_env.filters["rp"] = rp


def harga_range(mn, mx):
    return rp(mn) if mn == mx else f"{rp(mn)} – {rp(mx)}"


def to_int(v, default=0, lo=None, hi=None):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def wa_number(hp):
    d = re.sub(r"\D", "", hp or "")
    if d.startswith("0"):
        d = "62" + d[1:]
    elif d.startswith("8"):
        d = "62" + d
    return d


app.jinja_env.filters["wa"] = wa_number


def valid_phone(hp):
    return bool(re.fullmatch(r"(?:\+?62|0)8\d{7,12}", re.sub(r"[\s\-]", "", hp or "")))


def valid_email(e):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e or ""))


def device_dict(r):
    d = dict(r)
    try:
        d["warna"] = json.loads(d.pop("warna_json") or "[]")
    except ValueError:
        d["warna"] = []
    d["harga_fmt"] = harga_range(d["harga_min"], d["harga_max"])
    return d


def product_dict(r):
    d = dict(r)
    d["harga_fmt"] = rp(d["harga"])
    return d


def get_item(conn, typ, iid):
    r = conn.execute(f"SELECT * FROM {TABLE[typ]} WHERE id=?", (iid,)).fetchone()
    if not r:
        return None
    return device_dict(r) if typ == "device" else product_dict(r)


def adjust_stock(conn, typ, iid, delta, alasan, ref=""):
    t = TABLE[typ]
    cur = conn.execute(f"UPDATE {t} SET stok = stok + ? WHERE id=? AND stok + ? >= 0", (delta, iid, delta))
    if cur.rowcount == 0:
        return False
    stok = conn.execute(f"SELECT stok FROM {t} WHERE id=?", (iid,)).fetchone()[0]
    conn.execute("INSERT INTO stock_logs(item_type,item_id,perubahan,stok_akhir,alasan,ref) VALUES (?,?,?,?,?,?)",
                 (typ, iid, delta, stok, alasan, ref))
    return True


# ---------- Rekomendasi (content-based filtering, cosine similarity, tanpa sklearn) ----------
def _features(r):
    return {f"k:{r['kategori']}": 1.0, f"n:{r['nikotin']}": 1.0, f"r:{r['rasa'].lower()}": 0.6,
            f"b:{r['nama'].split()[0].lower()}": 0.5, f"u:{r['ukuran']}": 0.4}


def get_recommendations(conn, pid, n=3):
    rows = conn.execute("SELECT * FROM products WHERE aktif=1").fetchall()
    base = next((r for r in rows if r["id"] == pid), None)
    if not base:
        return []
    fb = _features(base)
    nb = sum(v * v for v in fb.values()) ** 0.5
    scored = []
    for pos, r in enumerate(rows):
        if r["id"] == pid:
            continue
        fr = _features(r)
        dot = sum(v * fr.get(k, 0) for k, v in fb.items())
        scored.append((dot / (nb * sum(v * v for v in fr.values()) ** 0.5), pos, r))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [product_dict(s[2]) for s in scored[:n]]


# ---------- Keranjang ----------
def get_cart_count():
    return sum(e.get("qty", 1) if isinstance(e, dict) else 1 for e in session.get("cart", []))


def cart_qty(typ, iid, warna=None):
    return sum(e.get("qty", 1) for e in session.get("cart", [])
               if isinstance(e, dict) and e.get("type") == typ and e.get("id") == iid
               and (warna is None or e.get("warna") == warna))


def resolve_cart(conn):
    """Harga & stok selalu diambil dari DB (bukan dari session)."""
    out = []
    for i, e in enumerate(session.get("cart", [])):
        if not isinstance(e, dict):
            e = {"type": "liquid", "id": e, "qty": 1}
        typ = e.get("type", "liquid")
        if typ not in TABLE:
            continue
        it = get_item(conn, typ, e.get("id"))
        if not it or not it["aktif"]:
            continue
        qty = max(1, to_int(e.get("qty", 1), 1))
        harga = it["harga_min"] if typ == "device" else it["harga"]
        d = {"type": typ, "id": it["id"], "nama": it["nama"], "merk": it.get("merk", ""), "tipe": it.get("tipe", ""),
             "warna": e.get("warna", ""), "kategori": it.get("kategori", ""), "rasa": it.get("rasa", ""),
             "nikotin": it.get("nikotin", 0), "ukuran": it.get("ukuran", 0), "harga": harga, "qty": qty,
             "stok": it["stok"], "over_stock": qty > it["stok"], "cart_index": i}
        d.update(harga_fmt=rp(harga), subtotal=harga * qty, subtotal_fmt=rp(harga * qty))
        out.append(d)
    return out


def check_voucher(conn, kode, subtotal):
    v = conn.execute("SELECT * FROM vouchers WHERE kode=?", ((kode or "").strip().upper(),)).fetchone()
    if not v or not v["aktif"]:
        return None, 0, "Kode voucher tidak valid."
    if v["berlaku_sampai"] and v["berlaku_sampai"] < date.today().isoformat():
        return None, 0, "Voucher sudah kedaluwarsa."
    if v["kuota"] and v["terpakai"] >= v["kuota"]:
        return None, 0, "Kuota voucher sudah habis."
    if subtotal < v["min_belanja"]:
        return None, 0, f"Minimal belanja {rp(v['min_belanja'])} untuk voucher ini."
    if v["tipe"] == "persen":
        diskon = subtotal * v["nilai"] // 100
        if v["maks_diskon"]:
            diskon = min(diskon, v["maks_diskon"])
    else:
        diskon = min(v["nilai"], subtotal)
    return v, diskon, None


def cart_summary(conn, items):
    s = get_settings()
    subtotal = sum(i["subtotal"] for i in items)
    diskon, voucher = 0, None
    if session.get("voucher") and items:
        voucher, diskon, err = check_voucher(conn, session["voucher"], subtotal)
        if err:
            session.pop("voucher", None)
            flash(err, "error")
    flat, free_min = setting_int(s, "ongkir_flat"), setting_int(s, "gratis_ongkir_min")
    ongkir = 0 if (not items or flat == 0 or (free_min and subtotal >= free_min)) else flat
    total = subtotal - diskon + ongkir
    return {"subtotal": subtotal, "diskon": diskon, "ongkir": ongkir, "total": total,
            "voucher": voucher["kode"] if voucher else None, "free_min": free_min, "flat": flat,
            "subtotal_fmt": rp(subtotal), "diskon_fmt": rp(diskon), "ongkir_fmt": rp(ongkir), "total_fmt": rp(total),
            "kurang_gratis": max(0, free_min - subtotal) if (free_min and flat and subtotal < free_min) else 0}


# ---------- Pesanan ----------
def order_dict(conn, row, detail=True):
    o = dict(row)
    for k in ("subtotal", "diskon", "ongkir", "total"):
        o[k + "_fmt"] = rp(o[k])
    o["wa_customer"] = wa_number(o["no_hp"])
    if detail:
        its = []
        for r in conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (o["id"],)):
            d = dict(r)
            d.update(type=d["item_type"], subtotal=d["harga"] * d["qty"], harga_fmt=rp(d["harga"]),
                     subtotal_fmt=rp(d["harga"] * d["qty"]))
            its.append(d)
        o["items"] = its
        o["history"] = [dict(r) for r in conn.execute(
            "SELECT * FROM order_status_history WHERE order_id=? ORDER BY id", (o["id"],))]
    return o


def new_order_code(conn):
    while True:
        code = f"ORD-{datetime.now():%Y%m%d}-{secrets.token_hex(3).upper()}"
        if not conn.execute("SELECT 1 FROM orders WHERE order_code=?", (code,)).fetchone():
            return code


def set_order_status(conn, order_id, new, note=""):
    o = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    old = o["status"]
    if old == new:
        return True, ""
    items = conn.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    if new == "Dibatalkan":
        for it in items:
            adjust_stock(conn, it["item_type"], it["item_id"], it["qty"], "Pesanan dibatalkan", o["order_code"])
        if o["voucher_kode"]:
            conn.execute("UPDATE vouchers SET terpakai = MAX(0, terpakai-1) WHERE kode=?", (o["voucher_kode"],))
    elif old == "Dibatalkan":
        for it in items:
            if not adjust_stock(conn, it["item_type"], it["item_id"], -it["qty"], "Pesanan diaktifkan lagi", o["order_code"]):
                return False, f"Stok '{it['nama']}' tidak cukup untuk mengaktifkan pesanan ini lagi."
        if o["voucher_kode"]:
            conn.execute("UPDATE vouchers SET terpakai = terpakai+1 WHERE kode=?", (o["voucher_kode"],))
    pay = ", status_bayar='Lunas'" if new == "Selesai" else ""
    conn.execute(f"UPDATE orders SET status=?, updated_at=datetime('now','localtime'){pay} WHERE id=?", (new, order_id))
    conn.execute("INSERT INTO order_status_history(order_id,status,catatan) VALUES (?,?,?)", (order_id, new, note))
    return True, ""


# ---------- Email (Gmail SMTP) ----------
def mail_configured():
    return bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_PASS"))


def send_email(to, subject, body_html):
    if not mail_configured():
        return False, "GMAIL_USER / GMAIL_PASS belum diset"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = subject, os.environ["GMAIL_USER"], to
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as srv:
            srv.login(os.environ["GMAIL_USER"], os.environ["GMAIL_PASS"])
            srv.send_message(msg)
        return True, ""
    except Exception as ex:  # jangan sampai checkout gagal karena email
        return False, str(ex)


def order_email_html(o, store, headline):
    e = html.escape
    rows = "".join(f"<tr><td style='padding:6px 0'>{e(i['nama'])} ×{i['qty']}</td>"
                   f"<td align='right'>{i['subtotal_fmt']}</td></tr>" for i in o["items"])
    resi = f"<p>Kurir: <b>{e(o['kurir'])}</b> — Resi: <b>{e(o['resi'])}</b></p>" if o["resi"] else ""
    return f"""<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#222">
<h2 style="color:#c96b3a">{e(store['nama_toko'])}</h2><h3>{e(headline)}</h3>
<p>Halo <b>{e(o['nama'])}</b>, kode pesanan Anda: <b>{e(o['order_code'])}</b> (status: {e(o['status'])})</p>{resi}
<table width="100%" style="border-top:1px solid #ddd;border-bottom:1px solid #ddd">{rows}
<tr><td style="padding-top:8px">Diskon</td><td align="right">- {o['diskon_fmt']}</td></tr>
<tr><td>Ongkir</td><td align="right">{o['ongkir_fmt']}</td></tr>
<tr><td><b>Total</b></td><td align="right"><b>{o['total_fmt']}</b></td></tr></table>
<p>Metode bayar: {e(o['metode_bayar'])}<br>{e(store.get('info_bayar',''))}</p>
<p>Alamat: {e(o['alamat'])}</p></div>"""


def email_order_async(order_id, headline):
    def work():
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if row and row["email"]:
                store = dict(DEFAULT_SETTINGS)
                store.update({r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM settings")})
                o = order_dict(conn, row)
                send_email(o["email"], f"{headline} — {o['order_code']}", order_email_html(o, store, headline))
        finally:
            conn.close()
    if mail_configured():
        threading.Thread(target=work, daemon=True).start()


# ─────────────────────────────────────────  KEAMANAN  ─────────────────────────────────────────
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


_FORM_RE = re.compile(r"(<form\b[^>]*\bmethod\s*=\s*[\"']?post[\"']?[^>]*>)", re.I)


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        if not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Sesi kedaluwarsa. Muat ulang halaman lalu coba lagi.")


@app.after_request
def inject_csrf(resp):
    """Sisipkan token CSRF otomatis ke semua <form method=POST>."""
    if resp.mimetype == "text/html" and not resp.direct_passthrough:
        tag = f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'
        resp.set_data(_FORM_RE.sub(lambda m: m.group(1) + tag, resp.get_data(as_text=True)))
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    return resp


@app.context_processor
def inject_globals():
    return {"store": get_settings(), "cart_count": get_cart_count(), "year": datetime.now().year,
            "pay_methods": PAY_METHODS}


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(500)
def on_error(e):
    code = getattr(e, "code", 500)
    msg = getattr(e, "description", "Terjadi kesalahan.") if code != 500 else "Terjadi kesalahan pada server."
    return render_template("error.html", code=code, message=msg), code


def login_required(f):
    @wraps(f)
    def deco(*a, **kw):
        if not session.get("admin_logged_in"):
            flash("Silakan login terlebih dahulu.", "error")
            return redirect(url_for("admin_login", next=request.path))
        return f(*a, **kw)
    return deco


_fails = {}


# ─────────────────────────────────────────  STOREFRONT  ─────────────────────────────────────────
SORTS = {"default": "p.rowid", "terlaris": "terjual DESC, p.rowid", "termurah": "p.harga ASC",
         "termahal": "p.harga DESC", "rating": "rating DESC, ulasan DESC"}


@app.route("/")
def index():
    conn = db()
    kategori = request.args.get("kategori", "Semua")
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "default")
    sort = sort if sort in SORTS else "default"
    page = to_int(request.args.get("page", 1), 1, 1)
    where, args = ["p.aktif=1"], []
    if kategori != "Semua":
        where.append("p.kategori=?")
        args.append(kategori)
    if q:
        where.append("(p.nama LIKE ? OR p.rasa LIKE ? OR p.kategori LIKE ?)")
        args += [f"%{q}%"] * 3
    base = f"""FROM products p
      LEFT JOIN (SELECT product_id, AVG(rating) rating, COUNT(*) ulasan FROM reviews WHERE approved=1
                 GROUP BY product_id) r ON r.product_id=p.id
      LEFT JOIN (SELECT oi.item_id, SUM(oi.qty) terjual FROM order_items oi JOIN orders o ON o.id=oi.order_id
                 WHERE oi.item_type='liquid' AND o.status!='Dibatalkan' GROUP BY oi.item_id) s ON s.item_id=p.id
      WHERE {' AND '.join(where)}"""
    total = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    pages = max(1, -(-total // PER_PAGE))
    page = min(page, pages)
    rows = conn.execute(f"""SELECT p.*, COALESCE(r.rating,0) rating, COALESCE(r.ulasan,0) ulasan,
                            COALESCE(s.terjual,0) terjual {base} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?""",
                        args + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    cats = ["Semua"] + [r[0] for r in conn.execute("SELECT DISTINCT kategori FROM products WHERE aktif=1 ORDER BY 1")]
    return render_template("index.html", products=[product_dict(r) for r in rows], categories=cats,
                           active_cat=kategori, q=q, sort=sort, page=page, pages=pages, total=total)


@app.route("/product/<pid>")
def detail(pid):
    conn = db()
    p = get_item(conn, "liquid", pid)
    if not p or (not p["aktif"] and not session.get("admin_logged_in")):
        return redirect(url_for("index"))
    reviews = [dict(r) for r in conn.execute(
        "SELECT * FROM reviews WHERE product_id=? AND approved=1 ORDER BY id DESC LIMIT 30", (pid,))]
    avg = round(sum(r["rating"] for r in reviews) / len(reviews), 1) if reviews else 0
    return render_template("detail.html", product=p, recommendations=get_recommendations(conn, pid),
                           reviews=reviews, avg_rating=avg,
                           low_stock=0 < p["stok"] <= setting_int(get_settings(), "stok_menipis"))


@app.route("/product/<pid>/review", methods=["POST"])
def add_review(pid):
    conn = db()
    if not get_item(conn, "liquid", pid):
        return redirect(url_for("index"))
    done = session.get("reviewed", [])
    nama = request.form.get("nama", "").strip()[:40]
    rating = to_int(request.form.get("rating"), 0)
    if pid in done:
        flash("Kamu sudah memberi ulasan untuk produk ini.", "info")
    elif not nama or not 1 <= rating <= 5:
        flash("Isi nama dan pilih rating 1–5.", "error")
    else:
        conn.execute("INSERT INTO reviews(product_id,nama,rating,komentar) VALUES (?,?,?,?)",
                     (pid, nama, rating, request.form.get("komentar", "").strip()[:500]))
        conn.commit()
        session["reviewed"] = done + [pid]
        flash("Terima kasih atas ulasanmu! ⭐", "success")
    return redirect(url_for("detail", pid=pid) + "#ulasan")


@app.route("/devices")
def devices_page():
    tipe = request.args.get("tipe", "Semua")
    q = request.args.get("q", "").strip().lower()
    devs = [device_dict(r) for r in db().execute("SELECT * FROM devices WHERE aktif=1 ORDER BY rowid")]
    if tipe != "Semua":
        devs = [d for d in devs if d["tipe"] == tipe]
    if q:
        devs = [d for d in devs if q in d["nama"].lower() or q in d["merk"].lower()]
    return render_template("devices.html", devices=devs, active_tipe=tipe, q=q)


@app.route("/device/<dev_id>")
def device_detail(dev_id):
    conn = db()
    dev = get_item(conn, "device", dev_id)
    if not dev or (not dev["aktif"] and not session.get("admin_logged_in")):
        return redirect(url_for("devices_page"))
    related = [device_dict(r) for r in conn.execute(
        "SELECT * FROM devices WHERE tipe=? AND id!=? AND aktif=1 LIMIT 3", (dev["tipe"], dev_id))]
    return render_template("device_detail.html", device=dev, related=related)


@app.route("/add_device_to_cart", methods=["POST"])
def add_device_to_cart():
    dev = get_item(db(), "device", request.form.get("dev_id"))
    if not dev or not dev["aktif"]:
        return redirect(url_for("devices_page"))
    warna = request.form.get("warna")
    qty = to_int(request.form.get("qty", 1), 1, 1, 99)
    if warna not in dev["warna"]:
        flash("Pilih warna yang tersedia.", "error")
        return redirect(url_for("device_detail", dev_id=dev["id"]))
    if cart_qty("device", dev["id"]) + qty > dev["stok"]:
        flash(f"Stok {dev['nama']} tinggal {dev['stok']}.", "error")
        return redirect(url_for("device_detail", dev_id=dev["id"]))
    cart = session.get("cart", [])
    for it in cart:
        if isinstance(it, dict) and it.get("type") == "device" and it["id"] == dev["id"] and it.get("warna") == warna:
            it["qty"] = it.get("qty", 1) + qty
            break
    else:
        cart.append({"type": "device", "id": dev["id"], "warna": warna, "qty": qty})
    session["cart"] = cart
    flash(f"✅ {dev['nama']} ditambahkan ke keranjang!", "success")
    return redirect(url_for("cart"))


@app.route("/add_to_cart/<pid>")
def add_to_cart(pid):
    p = get_item(db(), "liquid", pid)
    if not p or not p["aktif"]:
        return redirect(url_for("index"))
    if cart_qty("liquid", pid) + 1 > p["stok"]:
        flash(f"Stok {p['nama']} habis / sudah maksimal di keranjang.", "error")
        return redirect(url_for("detail", pid=pid))
    cart = session.get("cart", [])
    for it in cart:
        if isinstance(it, dict) and it.get("type") == "liquid" and it.get("id") == pid:
            it["qty"] = it.get("qty", 1) + 1
            break
    else:
        cart.append({"type": "liquid", "id": pid, "qty": 1})
    session["cart"] = cart
    flash("✅ Produk ditambahkan ke keranjang!", "success")
    return redirect(url_for("cart"))


@app.route("/update_qty/<int:index>", methods=["POST"])
def update_qty(index):
    qty = to_int(request.form.get("qty", 1), 1)
    cart = session.get("cart", [])
    if 0 <= index < len(cart):
        if qty <= 0:
            cart.pop(index)
        elif isinstance(cart[index], dict):
            it = get_item(db(), cart[index].get("type", "liquid"), cart[index].get("id"))
            if it and qty > it["stok"]:
                flash(f"Stok maksimal {it['stok']}.", "error")
                qty = it["stok"]
            cart[index]["qty"] = max(1, qty)
    session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/remove_from_cart/<int:index>")
def remove_from_cart(index):
    cart = session.get("cart", [])
    if 0 <= index < len(cart):
        cart.pop(index)
        session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/cart")
def cart():
    conn = db()
    items = resolve_cart(conn)
    sm = cart_summary(conn, items)
    return render_template("cart.html", items=items, total=sm["total"], total_fmt=sm["total_fmt"], sm=sm,
                           blocked=any(i["over_stock"] for i in items))


@app.route("/cart/voucher", methods=["POST"])
def apply_voucher():
    conn = db()
    items = resolve_cart(conn)
    sub = sum(i["subtotal"] for i in items)
    v, diskon, err = check_voucher(conn, request.form.get("kode", ""), sub)
    if err:
        flash(err, "error")
    else:
        session["voucher"] = v["kode"]
        flash(f"🎟️ Voucher {v['kode']} dipakai — hemat {rp(diskon)}", "success")
    return redirect(url_for("cart"))


@app.route("/cart/voucher/remove", methods=["POST"])
def remove_voucher():
    session.pop("voucher", None)
    return redirect(url_for("cart"))


class CheckoutError(Exception):
    pass


@app.route("/checkout", methods=["POST"])
def checkout():
    f = request.form
    nama, alamat = f.get("nama", "").strip(), f.get("alamat", "").strip()
    email, no_hp = f.get("email", "").strip(), f.get("no_hp", "").strip()
    metode = f.get("metode_bayar") if f.get("metode_bayar") in PAY_METHODS else PAY_METHODS[0]
    errors = []
    if len(nama) < 2:
        errors.append("Nama minimal 2 karakter.")
    if len(alamat) < 10:
        errors.append("Alamat terlalu singkat — tulis lengkap.")
    if not valid_phone(no_hp):
        errors.append("Nomor WhatsApp tidak valid (contoh 08123456789).")
    if email and not valid_email(email):
        errors.append("Format email tidak valid.")
    if not f.get("umur"):
        errors.append("Kamu harus menyatakan berusia minimal 18 tahun.")
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("cart"))

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        items = resolve_cart(conn)
        if not items:
            raise CheckoutError("Keranjang kosong.")
        for it in items:
            if it["over_stock"]:
                raise CheckoutError(f"Stok {it['nama']} tidak cukup (tersisa {it['stok']}).")
        sm = cart_summary(conn, items)
        code = new_order_code(conn)
        cur = conn.execute("""INSERT INTO orders(order_code,nama,alamat,email,no_hp,subtotal,diskon,ongkir,total,
                              voucher_kode,metode_bayar) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (code, nama, alamat, email, no_hp, sm["subtotal"], sm["diskon"], sm["ongkir"],
                            sm["total"], sm["voucher"], metode))
        oid = cur.lastrowid
        for it in items:
            conn.execute("""INSERT INTO order_items(order_id,item_type,item_id,nama,merk,tipe,warna,kategori,rasa,
                            nikotin,ukuran,harga,qty) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (oid, it["type"], it["id"], it["nama"], it["merk"], it["tipe"], it["warna"], it["kategori"],
                          it["rasa"], it["nikotin"], it["ukuran"], it["harga"], it["qty"]))
            if not adjust_stock(conn, it["type"], it["id"], -it["qty"], "Penjualan", code):
                raise CheckoutError(f"Stok {it['nama']} baru saja habis.")
        if sm["voucher"]:
            conn.execute("UPDATE vouchers SET terpakai = terpakai+1 WHERE kode=?", (sm["voucher"],))
        conn.execute("INSERT INTO order_status_history(order_id,status,catatan) VALUES (?,?,?)",
                     (oid, "Diproses", "Pesanan dibuat"))
        conn.commit()
    except CheckoutError as ex:
        conn.rollback()
        flash(str(ex), "error")
        return redirect(url_for("cart"))
    except Exception:
        conn.rollback()
        raise
    session["cart"], session["last_order"] = [], code
    session.pop("voucher", None)
    email_order_async(oid, "Konfirmasi Pesanan")
    return redirect(url_for("success", order_code=code))


@app.route("/sukses/<order_code>")
def success(order_code):
    conn = db()
    row = conn.execute("SELECT * FROM orders WHERE order_code=?", (order_code,)).fetchone()
    if not row or session.get("last_order") != order_code:
        return redirect(url_for("invoice_by_code", order_code=order_code))
    o = order_dict(conn, row)
    s = get_settings()
    text = f"Halo {s['nama_toko']}! Saya sudah order.\nKode: {o['order_code']}\nTotal: {o['total_fmt']}\nBayar: {o['metode_bayar']}"
    wa_admin = f"https://wa.me/{wa_number(s['wa_admin'])}?text={quote(text)}" if s["wa_admin"] else ""
    return render_template("success.html", order=o, nama=o["nama"], alamat=o["alamat"], order_code=o["order_code"],
                           total_fmt=o["total_fmt"], items=o["items"], email=o["email"], no_hp=o["no_hp"],
                           wa_admin=wa_admin, email_sent=bool(o["email"] and mail_configured()))


# ─────────────────────────────────────────  INVOICE (publik)  ─────────────────────────────────────────
def _order_by_code(code):
    row = db().execute("SELECT * FROM orders WHERE order_code=?", (code.strip().upper(),)).fetchone()
    return order_dict(db(), row) if row else None


@app.route("/cek-invoice", methods=["GET", "POST"])
def cek_invoice():
    if request.method == "POST":
        o = _order_by_code(request.form.get("order_code", ""))
        if o:
            return redirect(url_for("invoice_by_code", order_code=o["order_code"]))
        flash("Kode pesanan tidak ditemukan.", "error")
    return render_template("cek_invoice.html")


@app.route("/invoice/<order_code>")
def invoice_by_code(order_code):
    o = _order_by_code(order_code)
    if not o:
        flash("Kode pesanan tidak ditemukan.", "error")
        return redirect(url_for("cek_invoice"))
    return render_template("invoice.html", order=o)


@app.route("/invoice/<order_code>/download")
def download_invoice(order_code):
    o = _order_by_code(order_code)
    if not o:
        abort(404, "Invoice tidak ditemukan")
    return render_template("invoice_print.html", order=o)


# ─────────────────────────────────────────  ADMIN: PESANAN  ─────────────────────────────────────────
@app.route("/orders")
@login_required
def orders():
    conn = db()
    status = request.args.get("status", "Semua")
    q = request.args.get("q", "").strip()
    d1, d2 = request.args.get("dari", ""), request.args.get("sampai", "")
    page = to_int(request.args.get("page", 1), 1, 1)
    where, args = ["1=1"], []
    if status in STATUSES:
        where.append("o.status=?"); args.append(status)
    if q:
        where.append("(o.order_code LIKE ? OR o.nama LIKE ? OR o.no_hp LIKE ? OR o.email LIKE ?)")
        args += [f"%{q}%"] * 4
    if d1:
        where.append("date(o.created_at) >= ?"); args.append(d1)
    if d2:
        where.append("date(o.created_at) <= ?"); args.append(d2)
    w = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM orders o WHERE {w}", args).fetchone()[0]
    pages = max(1, -(-total // ORDERS_PER_PAGE))
    page = min(page, pages)
    rows = conn.execute(f"""SELECT o.*, (SELECT COALESCE(SUM(qty),0) FROM order_items WHERE order_id=o.id) item_count
                            FROM orders o WHERE {w} ORDER BY o.id DESC LIMIT ? OFFSET ?""",
                        args + [ORDERS_PER_PAGE, (page - 1) * ORDERS_PER_PAGE]).fetchall()
    counts = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM orders GROUP BY status")}
    return render_template("orders.html", orders=[order_dict(conn, r, False) | {"item_count": r["item_count"]} for r in rows],
                           status_filter=status, q=q, dari=d1, sampai=d2, page=page, pages=pages, total=total,
                           counts=counts, all_count=sum(counts.values()))


@app.route("/orders/<int:order_id>/edit", methods=["GET", "POST"])
@login_required
def edit_order(order_id):
    conn = db()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return redirect(url_for("orders"))
    if request.method == "POST":
        f = request.form
        new_status = f.get("status") if f.get("status") in STATUSES else row["status"]
        ok, msg = set_order_status(conn, order_id, new_status, f.get("catatan_status", "").strip())
        if not ok:
            conn.rollback()
            flash(msg, "error")
            return redirect(url_for("edit_order", order_id=order_id))
        conn.execute("""UPDATE orders SET nama=?,alamat=?,email=?,no_hp=?,catatan=?,kurir=?,resi=?,metode_bayar=?,
                        updated_at=datetime('now','localtime') WHERE id=?""",
                     (f.get("nama", "").strip(), f.get("alamat", "").strip(), f.get("email", "").strip(),
                      f.get("no_hp", "").strip(), f.get("catatan", "").strip(), f.get("kurir", "").strip(),
                      f.get("resi", "").strip(), f.get("metode_bayar") if f.get("metode_bayar") in PAY_METHODS else row["metode_bayar"],
                      order_id))
        if f.get("status_bayar") in ("Lunas", "Belum Bayar") and new_status != "Selesai":
            conn.execute("UPDATE orders SET status_bayar=? WHERE id=?", (f["status_bayar"], order_id))
        conn.commit()
        if new_status != row["status"] and new_status in ("Dikirim", "Selesai"):
            email_order_async(order_id, "Status Pesanan: " + new_status)
        flash("Pesanan berhasil diperbarui!", "success")
        return redirect(url_for("orders"))
    return render_template("order_edit.html", order=order_dict(conn, row), statuses=STATUSES)


@app.route("/orders/<int:order_id>/delete", methods=["POST"])
@login_required
def delete_order(order_id):
    conn = db()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if row:
        if row["status"] in ("Diproses", "Dikirim"):
            set_order_status(conn, order_id, "Dibatalkan", "Pesanan dihapus admin")
        conn.execute("DELETE FROM orders WHERE id=?", (order_id,))
        conn.commit()
    flash("Pesanan dihapus (stok dikembalikan bila belum selesai).", "info")
    return redirect(url_for("orders"))


@app.route("/orders/<int:order_id>/invoice")
@login_required
def invoice_by_id(order_id):
    row = db().execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return redirect(url_for("orders"))
    return render_template("invoice.html", order=order_dict(db(), row))


@app.route("/orders/export-csv")
@login_required
def export_csv():
    conn = db()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Kode", "Tanggal", "Nama", "Alamat", "Email", "No HP", "Item", "Subtotal", "Diskon", "Ongkir",
                "Total", "Voucher", "Metode", "Bayar", "Status", "Kurir", "Resi", "Catatan"])
    for r in conn.execute("SELECT * FROM orders ORDER BY id DESC"):
        o = order_dict(conn, r)
        items = "; ".join(f"{i['nama']}{' ('+i['warna']+')' if i['warna'] else ''} x{i['qty']}" for i in o["items"])
        w.writerow([o["order_code"], o["created_at"], o["nama"], o["alamat"], o["email"], o["no_hp"], items,
                    o["subtotal"], o["diskon"], o["ongkir"], o["total"], o["voucher_kode"] or "", o["metode_bayar"],
                    o["status_bayar"], o["status"], o["kurir"], o["resi"], o["catatan"]])
    resp = make_response("\ufeff" + out.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=pesanan.csv"
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    return resp


# ─────────────────────────────────────────  ADMIN: DASHBOARD & KATALOG  ─────────────────────────────────────────
@app.route("/admin")
@login_required
def admin():
    conn = db()
    s = get_settings()
    thr = setting_int(s, "stok_menipis")
    one = lambda sql, a=(): conn.execute(sql, a).fetchone()[0]
    stats = {"orders": one("SELECT COUNT(*) FROM orders"),
             "revenue": rp(one("SELECT COALESCE(SUM(total),0) FROM orders WHERE status!='Dibatalkan'")),
             "diproses": one("SELECT COUNT(*) FROM orders WHERE status='Diproses'"),
             "selesai": one("SELECT COUNT(*) FROM orders WHERE status='Selesai'"),
             "belum_bayar": one("SELECT COUNT(*) FROM orders WHERE status_bayar='Belum Bayar' AND status!='Dibatalkan'")}
    days = [(date.today() - timedelta(days=i)) for i in range(6, -1, -1)]
    sales = {r[0]: r[1] for r in conn.execute(
        "SELECT date(created_at), SUM(total) FROM orders WHERE status!='Dibatalkan' AND date(created_at)>=? GROUP BY 1",
        (days[0].isoformat(),))}
    chart = [{"label": d.strftime("%d/%m"), "val": sales.get(d.isoformat(), 0)} for d in days]
    mx = max([c["val"] for c in chart] + [1])
    for c in chart:
        c["pct"] = max(3, int(c["val"] * 100 / mx)) if c["val"] else 3
        c["fmt"] = rp(c["val"])
    top = [dict(r) | {"rev_fmt": rp(r["rev"])} for r in conn.execute("""
        SELECT oi.nama, SUM(oi.qty) qty, SUM(oi.qty*oi.harga) rev FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE o.status!='Dibatalkan' GROUP BY oi.item_type, oi.item_id ORDER BY qty DESC LIMIT 5""")]
    low = [dict(r) for r in conn.execute(
        f"""SELECT 'liquid' typ,id,nama,stok FROM products WHERE aktif=1 AND stok<=?
            UNION ALL SELECT 'device',id,nama,stok FROM devices WHERE aktif=1 AND stok<=? ORDER BY stok""", (thr, thr))]
    return render_template("admin.html", stats=stats, chart=chart, top=top, low=low, thr=thr,
                           products=[product_dict(r) for r in conn.execute("SELECT * FROM products ORDER BY rowid")],
                           devices=[device_dict(r) for r in conn.execute("SELECT * FROM devices ORDER BY rowid")],
                           recent=[order_dict(conn, r, False) for r in conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 5")],
                           stock_logs=conn.execute("SELECT * FROM stock_logs ORDER BY id DESC LIMIT 8").fetchall(),
                           default_pw=session.get("default_pw"), mail_ok=mail_configured())


def _log_manual_stock(conn, typ, iid, old, new, alasan):
    if old != new:
        conn.execute("INSERT INTO stock_logs(item_type,item_id,perubahan,stok_akhir,alasan) VALUES (?,?,?,?,?)",
                     (typ, iid, new - old, new, alasan))


@app.route("/admin/product/add", methods=["GET", "POST"])
@login_required
def admin_add_product():
    if request.method == "POST":
        f, conn = request.form, db()
        pid = re.sub(r"[^A-Z0-9_-]", "", f.get("id", "").upper())
        if not pid or not f.get("nama", "").strip():
            flash("Kode dan nama wajib diisi.", "error")
            return redirect(url_for("admin_add_product"))
        try:
            stok = to_int(f.get("stok"), 0, 0)
            conn.execute("""INSERT INTO products(id,nama,kategori,rasa,nikotin,ukuran,harga,deskripsi,stok,aktif)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (pid, f["nama"].strip(), f.get("kategori", "Fruity"), f.get("rasa", "").strip(),
                          to_int(f.get("nikotin"), 3, 0), to_int(f.get("ukuran"), 30, 1), to_int(f.get("harga"), 0, 0),
                          f.get("deskripsi", "").strip(), stok, 1 if f.get("aktif") else 0))
            _log_manual_stock(conn, "liquid", pid, 0, stok, "Stok awal")
            conn.commit()
            flash("Produk berhasil ditambahkan!", "success")
        except sqlite3.IntegrityError:
            flash("Kode produk sudah ada.", "error")
        return redirect(url_for("admin"))
    return render_template("admin_product_form.html", mode="add", product=None)


@app.route("/admin/product/edit/<pid>", methods=["GET", "POST"])
@login_required
def admin_edit_product(pid):
    conn = db()
    p = get_item(conn, "liquid", pid)
    if not p:
        return redirect(url_for("admin"))
    if request.method == "POST":
        f = request.form
        stok = to_int(f.get("stok"), p["stok"], 0)
        conn.execute("""UPDATE products SET nama=?,kategori=?,rasa=?,nikotin=?,ukuran=?,harga=?,deskripsi=?,stok=?,aktif=?
                        WHERE id=?""",
                     (f.get("nama", "").strip(), f.get("kategori", "Fruity"), f.get("rasa", "").strip(),
                      to_int(f.get("nikotin"), 3, 0), to_int(f.get("ukuran"), 30, 1), to_int(f.get("harga"), 0, 0),
                      f.get("deskripsi", "").strip(), stok, 1 if f.get("aktif") else 0, pid))
        _log_manual_stock(conn, "liquid", pid, p["stok"], stok, "Edit produk")
        conn.commit()
        flash("Produk berhasil diperbarui!", "success")
        return redirect(url_for("admin"))
    return render_template("admin_product_form.html", mode="edit", product=p)


@app.route("/admin/product/delete/<pid>", methods=["POST"])
@login_required
def admin_delete_product(pid):
    db().execute("DELETE FROM products WHERE id=?", (pid,))
    db().commit()
    flash("Produk dihapus (riwayat pesanan tetap aman).", "info")
    return redirect(url_for("admin"))


def _device_form(f, old=None):
    warna = [w.strip() for w in f.get("warna_list", "").split(",") if w.strip()]
    mn, mx = to_int(f.get("harga_min"), 0, 0), to_int(f.get("harga_max"), 0, 0)
    return (f.get("nama", "").strip(), f.get("merk", "").strip(), f.get("tipe", "Pod"), mn, max(mn, mx),
            json.dumps(warna), f.get("deskripsi", "").strip(), to_int(f.get("stok"), old["stok"] if old else 0, 0),
            1 if f.get("aktif") else 0)


@app.route("/admin/device/add", methods=["GET", "POST"])
@login_required
def admin_add_device():
    if request.method == "POST":
        conn = db()
        did = re.sub(r"[^A-Z0-9_-]", "", request.form.get("id", "").upper())
        data = _device_form(request.form)
        if not did or not data[0]:
            flash("Kode dan nama wajib diisi.", "error")
            return redirect(url_for("admin_add_device"))
        try:
            conn.execute("""INSERT INTO devices(id,nama,merk,tipe,harga_min,harga_max,warna_json,deskripsi,stok,aktif)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""", (did,) + data)
            _log_manual_stock(conn, "device", did, 0, data[7], "Stok awal")
            conn.commit()
            flash("Device berhasil ditambahkan!", "success")
        except sqlite3.IntegrityError:
            flash("Kode device sudah ada.", "error")
        return redirect(url_for("admin"))
    return render_template("admin_device_form.html", mode="add", device=None)


@app.route("/admin/device/edit/<did>", methods=["GET", "POST"])
@login_required
def admin_edit_device(did):
    conn = db()
    d = get_item(conn, "device", did)
    if not d:
        return redirect(url_for("admin"))
    if request.method == "POST":
        data = _device_form(request.form, d)
        conn.execute("""UPDATE devices SET nama=?,merk=?,tipe=?,harga_min=?,harga_max=?,warna_json=?,deskripsi=?,
                        stok=?,aktif=? WHERE id=?""", data + (did,))
        _log_manual_stock(conn, "device", did, d["stok"], data[7], "Edit device")
        conn.commit()
        flash("Device berhasil diperbarui!", "success")
        return redirect(url_for("admin"))
    return render_template("admin_device_form.html", mode="edit", device=d)


@app.route("/admin/device/delete/<did>", methods=["POST"])
@login_required
def admin_delete_device(did):
    db().execute("DELETE FROM devices WHERE id=?", (did,))
    db().commit()
    flash("Device dihapus.", "info")
    return redirect(url_for("admin"))


@app.route("/admin/stock/<typ>/<iid>", methods=["POST"])
@login_required
def admin_stock(typ, iid):
    if typ not in TABLE:
        abort(404)
    delta = to_int(request.form.get("delta"), 0)
    if delta and adjust_stock(db(), typ, iid, delta, request.form.get("alasan") or "Penyesuaian admin"):
        db().commit()
        flash(f"Stok {iid} diubah {delta:+d}.", "success")
    else:
        flash("Perubahan stok tidak valid (stok tak boleh negatif).", "error")
    return redirect(url_for("admin"))


# ─────────────────────────────────────────  ADMIN: VOUCHER, ULASAN, SETELAN  ─────────────────────────────────────────
@app.route("/admin/vouchers", methods=["GET", "POST"])
@login_required
def admin_vouchers():
    conn = db()
    if request.method == "POST":
        f = request.form
        kode = re.sub(r"[^A-Z0-9_-]", "", f.get("kode", "").upper())
        nilai = to_int(f.get("nilai"), 0, 0)
        tipe = f.get("tipe") if f.get("tipe") in ("persen", "nominal") else "persen"
        if not kode or nilai <= 0 or (tipe == "persen" and nilai > 100):
            flash("Data voucher tidak valid.", "error")
        else:
            try:
                conn.execute("""INSERT INTO vouchers(kode,tipe,nilai,min_belanja,maks_diskon,kuota,berlaku_sampai)
                                VALUES (?,?,?,?,?,?,?)""",
                             (kode, tipe, nilai, to_int(f.get("min_belanja"), 0, 0), to_int(f.get("maks_diskon"), 0, 0),
                              to_int(f.get("kuota"), 0, 0), f.get("berlaku_sampai") or None))
                conn.commit()
                flash("Voucher ditambahkan.", "success")
            except sqlite3.IntegrityError:
                flash("Kode voucher sudah ada.", "error")
        return redirect(url_for("admin_vouchers"))
    return render_template("admin_vouchers.html", vouchers=conn.execute("SELECT * FROM vouchers ORDER BY kode").fetchall(),
                           today=date.today().isoformat())


@app.route("/admin/vouchers/<kode>/<action>", methods=["POST"])
@login_required
def admin_voucher_action(kode, action):
    if action == "toggle":
        db().execute("UPDATE vouchers SET aktif = 1-aktif WHERE kode=?", (kode,))
    elif action == "delete":
        db().execute("DELETE FROM vouchers WHERE kode=?", (kode,))
    db().commit()
    return redirect(url_for("admin_vouchers"))


@app.route("/admin/reviews")
@login_required
def admin_reviews():
    rows = db().execute("""SELECT r.*, p.nama produk FROM reviews r JOIN products p ON p.id=r.product_id
                           ORDER BY r.id DESC LIMIT 200""").fetchall()
    return render_template("admin_reviews.html", reviews=rows)


@app.route("/admin/reviews/<int:rid>/<action>", methods=["POST"])
@login_required
def admin_review_action(rid, action):
    if action == "toggle":
        db().execute("UPDATE reviews SET approved = 1-approved WHERE id=?", (rid,))
    elif action == "delete":
        db().execute("DELETE FROM reviews WHERE id=?", (rid,))
    db().commit()
    return redirect(url_for("admin_reviews"))


@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    conn = db()
    if request.method == "POST":
        for k in DEFAULT_SETTINGS:
            if k in request.form:
                v = request.form[k].strip()
                if k in ("ongkir_flat", "gratis_ongkir_min", "stok_menipis"):
                    v = str(to_int(v, 0, 0))
                conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        conn.commit()
        flash("Pengaturan disimpan.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html", s=get_settings(), mail_ok=mail_configured(),
                           admin_user=session.get("admin_user", ""))


@app.route("/admin/password", methods=["POST"])
@login_required
def admin_password():
    f, conn = request.form, db()
    row = conn.execute("SELECT * FROM admins WHERE username=?", (session.get("admin_user"),)).fetchone()
    if not row or not check_password_hash(row["password_hash"], f.get("lama", "")):
        flash("Password lama salah.", "error")
    elif len(f.get("baru", "")) < 8 or f.get("baru") != f.get("ulang"):
        flash("Password baru minimal 8 karakter dan harus sama dengan konfirmasi.", "error")
    else:
        conn.execute("UPDATE admins SET password_hash=? WHERE id=?", (generate_password_hash(f["baru"]), row["id"]))
        conn.commit()
        session["default_pw"] = False
        flash("Password berhasil diganti.", "success")
    return redirect(url_for("admin_settings"))


@app.route("/admin/test-email", methods=["POST"])
@login_required
def admin_test_email():
    to = request.form.get("to", "").strip()
    ok, err = send_email(to, "Tes Email Toko", "<h3>Email berfungsi ✅</h3>") if valid_email(to) else (False, "Email tujuan tidak valid")
    flash("Email tes terkirim ✅" if ok else f"Gagal kirim email: {err}", "success" if ok else "error")
    return redirect(url_for("admin_settings"))


@app.route("/admin/backup")
@login_required
def admin_backup():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dest = sqlite3.connect(path)
    db().backup(dest)
    dest.close()
    data = open(path, "rb").read()
    os.remove(path)
    return send_file(io.BytesIO(data), as_attachment=True, mimetype="application/x-sqlite3",
                     download_name=f"backup-{datetime.now():%Y%m%d-%H%M}.db")


# ─────────────────────────────────────────  ADMIN: LOGIN  ─────────────────────────────────────────
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin"))
    if request.method == "POST":
        ip = request.remote_addr or "?"
        rec = _fails.get(ip, [0, 0])
        if rec[0] >= 5 and time.time() - rec[1] < 600:
            flash("Terlalu banyak percobaan. Coba lagi 10 menit lagi.", "error")
            return render_template("admin_login.html")
        user, pw = request.form.get("username", "").strip(), request.form.get("password", "")
        row = db().execute("SELECT * FROM admins WHERE username=?", (user,)).fetchone()
        if row and check_password_hash(row["password_hash"], pw):
            _fails.pop(ip, None)
            session.clear()
            session.update(admin_logged_in=True, admin_user=user, default_pw=(pw == "123456"))
            session.permanent = True
            flash("Login berhasil! Selamat datang, Admin.", "success")
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("admin"))
        _fails[ip] = [rec[0] + 1 if time.time() - rec[1] < 600 else 1, time.time()]
        flash("Username atau password salah.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    for k in ("admin_logged_in", "admin_user", "default_pw"):
        session.pop(k, None)
    flash("Kamu telah logout.", "info")
    return redirect(url_for("admin_login"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
