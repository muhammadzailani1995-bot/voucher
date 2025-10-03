import os
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template, url_for, send_from_directory
from flask_sqlalchemy import SQLAlchemy

# ---------------- CONFIG ----------------
app = Flask(__name__)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.getenv("DATABASE_URL", "sqlite:///store.db")
app.config["SQLALCHEMY_DATABASE_URI"] = DB_PATH
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
SMS_ACTIVATE_API_KEY = os.getenv("SMS_ACTIVATE_API_KEY", "")
SMS_ACTIVATE_API_BASE = os.getenv("SMS_ACTIVATE_API_BASE", "https://api.sms-activate.ae/stubs/handler_api.php")
PORT = int(os.getenv("PORT", 5000))

# ---------------- MODELS ----------------
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    service_code = db.Column(db.String(50), nullable=True)
    country = db.Column(db.String(10), nullable=True)
    price = db.Column(db.Integer, nullable=False)          # sale price (sen MYR)
    original_price = db.Column(db.Integer, nullable=True)  # original price (sen MYR)
    description = db.Column(db.Text, nullable=True)
    image_filename = db.Column(db.String(200), nullable=True)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    product = db.relationship('Product')
    amount = db.Column(db.Integer, nullable=False)  # price at time (sen)
    stripe_session_id = db.Column(db.String(300), nullable=True)
    stripe_payment_intent = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(50), default="pending")  # pending, paid, fulfilled, failed
    sms_activate_order_id = db.Column(db.String(200), nullable=True)
    number = db.Column(db.String(100), nullable=True)
    raw_response = db.Column(db.Text, nullable=True)
    otp_code = db.Column(db.String(50), nullable=True)  # store received OTP if any

# ---------------- INIT & SEED ----------------
@app.before_first_request
def init_db():
    db.create_all()
    if Product.query.count() == 0:
        products = [
            Product(slug="zus-coffee", name="Zus Coffee Voucher", service_code="aik", country="7", price=150, original_price=180, description="Nikmati diskaun eksklusif untuk minuman pilihan di ZUS Coffee. Sah di cawangan terlibat.", image_filename="zus.jpg"),
            Product(slug="kfc-rm10", name="KFC Voucher — RM10 OFF", service_code="fz", country="7", price=150, original_price=180, description="Gunakan baucar ini untuk potongan RM10 bagi pembelian di KFC (dine-in/takeaway/delivery).", image_filename="kfc.jpg"),
            Product(slug="chagee-b1f1", name="CHAGEE Voucher — Buy 1 Free 1", service_code="bwx", country="7", price=150, original_price=180, description="Beli satu minuman terpilih dan dapatkan satu lagi secara percuma di CHAGEE.", image_filename="chagee.jpg"),
            Product(slug="tealive-voucher", name="Tealive Voucher", service_code="avb", country="7", price=150, original_price=180, description="Baucar istimewa untuk minuman Tealive kegemaran anda. Sah untuk tebus di kaunter.", image_filename="tealive.jpg"),
        ]
        for p in products:
            db.session.add(p)
        db.session.commit()

# ---------------- ROUTES ----------------
@app.route("/")
def index():
    products = Product.query.all()
    return render_template("index.html", products=products)

@app.route("/product/<slug>")
def product_detail(slug):
    p = Product.query.filter_by(slug=slug).first_or_404()
    return render_template("product.html", product=p)

@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    data = request.get_json() or {}
    product_id = data.get("product_id")
    if not product_id:
        return jsonify({"error": "missing product_id"}), 400
    product = Product.query.get(product_id)
    if not product:
        return jsonify({"error": "product not found"}), 404

    order = Order(product=product, amount=product.price, status="pending")
    db.session.add(order)
    db.session.commit()

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "myr",
                    "product_data": {"name": product.name, "description": product.description},
                    "unit_amount": product.price,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=url_for("success", _external=True) + f"?order_id={order.id}",
            cancel_url=url_for("cancel", _external=True),
            metadata={"order_id": order.id, "product_id": product.id},
        )
    except Exception as e:
        db.session.delete(order)
        db.session.commit()
        return jsonify({"error": str(e)}), 500

    order.stripe_session_id = session.id
    db.session.commit()
    return jsonify({"checkout_url": session.url})

@app.route("/success")
def success():
    order_id = request.args.get("order_id")
    return render_template("success.html", order_id=order_id)

@app.route("/order/<int:order_id>")
def view_order(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("order.html", order=order)

@app.route("/admin/orders")
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    products = Product.query.all()
    stats = []
    total_revenue = 0
    total_orders = len(orders)
    for p in products:
        count = Order.query.filter(Order.product_id==p.id, Order.status.in_(["paid","fulfilled"])).count()
        revenue = db.session.query(db.func.coalesce(db.func.sum(Order.amount), 0)).filter(Order.product_id==p.id, Order.status.in_(["paid","fulfilled"])).scalar() or 0
        stats.append({"product": p, "count": count, "revenue_cents": int(revenue)})
        total_revenue += int(revenue)
    return render_template("admin_orders.html", orders=orders, stats=stats, total_orders=total_orders, total_revenue_cents=total_revenue)

# ---------------- STRIPE WEBHOOK ----------------
@app.route("/webhook", methods=["POST"])
def webhook():
    import stripe, json
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
    else:
        try:
            event = stripe.Event.construct_from(request.get_json(), stripe.api_key)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata") or {}
        order_id = metadata.get("order_id")
        if order_id:
            order = Order.query.get(int(order_id))
            if order and order.status == "pending":
                order.status = "paid"
                order.stripe_payment_intent = session.get("payment_intent")
                db.session.commit()
                fulfill_order_with_sms_activate(order.id)
    return jsonify({"received": True})

# ---------------- SMS-ACTIVATE INTEGRATION ----------------
def fulfill_order_with_sms_activate(order_id):
    order = Order.query.get(order_id)
    if not order:
        return
    product = order.product
    if not SMS_ACTIVATE_API_KEY:
        order.raw_response = "sms_activate_not_configured"
        order.status = "fulfilled"
        db.session.commit()
        return

    params = {
        "api_key": SMS_ACTIVATE_API_KEY,
        "action": "getNumber",
        "service": product.service_code,
        "country": product.country
    }
    try:
        r = requests.get(SMS_ACTIVATE_API_BASE, params=params, timeout=30)
        txt = r.text.strip()
        order.raw_response = txt
        if txt.startswith("ACCESS_NUMBER"):
            parts = txt.split(":")
            if len(parts) >= 3:
                sa_order_id = parts[1]
                phone = parts[2]
                order.sms_activate_order_id = sa_order_id
                order.number = phone
                order.status = "fulfilled"
                db.session.commit()
                # start polling for SMS (in simple loop; for production use background job)
                poll_for_otp(order.id, sa_order_id)
                return
        order.status = "failed"
        db.session.commit()
    except Exception as e:
        order.status = "failed"
        order.raw_response = f"exception:{str(e)}"
        db.session.commit()

def poll_for_otp(order_id, sa_order_id, attempts=15, delay_seconds=5):
    import time
    order = Order.query.get(order_id)
    if not order:
        return
    for i in range(attempts):
        try:
            params = {"api_key": SMS_ACTIVATE_API_KEY, "action": "getStatus", "id": sa_order_id}
            r = requests.get(SMS_ACTIVATE_API_BASE, params=params, timeout=20)
            txt = r.text.strip()
            # Example responses: STATUS_OK:code, STATUS_WAIT_CODE, STATUS_CANCEL
            if txt.startswith("STATUS_OK"):
                # format STATUS_OK:code
                parts = txt.split(":")
                if len(parts) >= 2:
                    code = parts[1]
                    order.otp_code = code
                    db.session.commit()
                    return
            # wait then retry
        except Exception:
            pass
        time.sleep(delay_seconds)

# ---------------- STATIC FILES SERVE (if needed) ----------------
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=PORT)
