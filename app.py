from flask import Flask, render_template, request, jsonify, redirect, url_for
import requests
import sqlite3
import stripe
import os

app = Flask(__name__)

# ----------------------------
# ✅ STRIPE CONFIG
# ----------------------------
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_123")  # Ganti bila live
CURRENCY = "myr"

# ----------------------------
# ✅ SMS ACTIVATE CONFIG
# ----------------------------
SMS_API_KEY = os.getenv("SMS_ACTIVATE_API_KEY", "your_api_key_here")
SMS_COUNTRY = 7  # Malaysia

PRODUCT_MAPPING = {
    "zus": "aik",
    "kfc": "fz",
    "chagee": "bwx",
    "tealive": "avb"
}

# ----------------------------
# ✅ DATABASE SETUP
# ----------------------------
def init_db():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            code TEXT,
            original_price INTEGER,
            price INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            phone_number TEXT,
            otp_code TEXT,
            amount_paid INTEGER
        )
    """)
    conn.commit()
    conn.close()

def seed_products():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    products = [
        ("Zus Coffee", "zus", 180, 150),
        ("KFC RM10 OFF", "kfc", 180, 150),
        ("CHAGEE Buy1Free1", "chagee", 180, 150),
        ("Tealive Voucher", "tealive", 180, 150)
    ]
    for p in products:
        c.execute("SELECT id FROM products WHERE code = ?", (p[1],))
        if not c.fetchone():
            c.execute("INSERT INTO products (name, code, original_price, price) VALUES (?, ?, ?, ?)", p)
    conn.commit()
    conn.close()

# ----------------------------
# ✅ ROUTES
# ----------------------------
@app.route('/')
def home():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("SELECT id, name, original_price, price, code FROM products")
    products = c.fetchall()
    conn.close()
    return render_template("index.html", products=products)

@app.route('/checkout/<product_code>', methods=['POST'])
def create_checkout_session(product_code):
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("SELECT id, name, price FROM products WHERE code = ?", (product_code,))
    product = c.fetchone()
    conn.close()

    if not product:
        return "Product not found", 404

    product_id, name, price = product

    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            "price_data": {
                "currency": CURRENCY,
                "product_data": {"name": name},
                "unit_amount": price * 100  # Stripe pakai sen
            },
            "quantity": 1
        }],
        mode='payment',
        success_url=url_for('success', product_code=product_code, _external=True),
        cancel_url=url_for('home', _external=True)
    )
    return redirect(session.url, code=303)

@app.route('/success/<product_code>')
def success(product_code):
    api_key = SMS_API_KEY
    service_code = PRODUCT_MAPPING.get(product_code)

    if not service_code:
        return "Invalid product code", 400

    url = f"https://sms-activate.ru/stubs/handler_api.php?api_key={api_key}&action=getNumber&service={service_code}&country={SMS_COUNTRY}"
    r = requests.get(url)

    if "ACCESS_NUMBER" in r.text:
        parts = r.text.split(':')
        activation_id = parts[1]
        phone = parts[2]

        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("SELECT id, price FROM products WHERE code = ?", (product_code,))
        p = c.fetchone()
        c.execute(
            "INSERT INTO orders (product_id, phone_number, otp_code, amount_paid) VALUES (?, ?, ?, ?)",
            (p[0], phone, None, p[1])
        )
        conn.commit()
        conn.close()

        return render_template("success.html", phone=phone, activation_id=activation_id)
    else:
        return "Gagal dapat nombor. Cuba lagi."

@app.route('/get_otp', methods=['POST'])
def get_otp():
    activation_id = request.form.get("activation_id")
    url = f"https://sms-activate.ru/stubs/handler_api.php?api_key={SMS_API_KEY}&action=getStatus&id={activation_id}"
    r = requests.get(url)

    if "STATUS_OK" in r.text:
        otp = r.text.split(':')[1]
        return jsonify({"status": "success", "otp": otp})
    else:
        return jsonify({"status": "pending", "otp": None})

# ----------------------------
# ✅ RUN
# ----------------------------
if __name__ == '__main__':
    init_db()
    seed_products()
    app.run(host='0.0.0.0', port=5000)
