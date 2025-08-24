import yfinance as yf
from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, abort
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import requests
from bs4 import BeautifulSoup
import io
import csv
from datetime import date
import os # <-- Make sure this import is here

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_super_secret_key_that_is_hard_to_guess'
bcrypt = Bcrypt(app)

# --- DATABASE CONFIGURATION ---
# This code smartly chooses which database to use
if 'PYTHONANYWHERE_HOSTNAME' in os.environ:
    # On PythonAnywhere, use SQLite
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fintrack.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
else:
    # On your local computer, use PostgreSQL
    app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:Adhil%401@localhost:5432/fintrack_db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- DATABASE MODELS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class Asset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    asset_type = db.Column(db.String(50), nullable=False)
    asset_name = db.Column(db.String(120), nullable=False)
    ticker_symbol = db.Column(db.String(20), nullable=True)
    quantity = db.Column(db.Float, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    purchase_date = db.Column(db.Date, nullable=False)

# --- HELPER FUNCTION FOR GOLD PRICE (API VERSION) ---
def get_gold_price():
    try:
        api_key = '761313e48ee09b1877b1ccf4819fccde' # Your API Key
        url = f"https://api.metalpriceapi.com/v1/latest?api_key={api_key}&base=XAU&currencies=INR"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        price_per_ounce_inr = data['rates']['INR']
        price_per_gram_inr = price_per_ounce_inr / 31.1035
        return price_per_gram_inr
    except Exception as e:
        print(f"An error occurred while fetching gold price from API: {e}")
        return 0

# --- ROUTES ---
@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/sw.js')
def sw():
    return send_from_directory('.', 'sw.js')

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    current_user = User.query.get(session['user_id'])
    user_assets = Asset.query.filter_by(user_id=session['user_id']).all()
    total_portfolio_value = 0
    total_invested_value = 0
    enriched_assets = []
    gold_price_per_gram = get_gold_price()
    mf_navs = {}
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = "https://www.amfiindia.com/spages/NAVAll.txt"
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            csv_file = io.StringIO(response.text)
            reader = csv.reader(csv_file, delimiter=';')
            for row in reader:
                if len(row) >= 5 and row[0].strip().isdigit():
                    try:
                        nav_value = float(row[4].strip())
                        mf_navs[row[0].strip()] = nav_value
                    except ValueError:
                        continue 
    except Exception as e:
        print(f"Could not fetch MF NAVs: {e}")

    for asset in user_assets:
        is_live_price = False
        invested_value = asset.quantity * asset.purchase_price
        current_value = invested_value
        if asset.asset_type == 'STOCK' and asset.ticker_symbol:
            try:
                stock_data = yf.Ticker(asset.ticker_symbol)
                history = stock_data.history(period='1d', timeout=10)
                if not history.empty:
                    current_price = history['Close'].iloc[-1]
                    current_value = asset.quantity * current_price
                    is_live_price = True
            except Exception as e:
                print(f"Could not fetch stock price for {asset.ticker_symbol}: {e}")
        elif asset.asset_type == 'GOLD':
            if gold_price_per_gram > 0:
                current_value = asset.quantity * gold_price_per_gram
                is_live_price = True
        elif asset.asset_type == 'MF' and asset.ticker_symbol:
            nav = mf_navs.get(asset.ticker_symbol.strip())
            if nav:
                current_value = asset.quantity * nav
                is_live_price = True
        elif asset.asset_type == 'FD':
            principal = asset.quantity
            interest_rate = asset.purchase_price / 100
            days_active = (date.today() - asset.purchase_date).days
            years_active = days_active / 365.25
            interest_earned = principal * interest_rate * years_active
            current_value = principal + interest_earned
            is_live_price = True
        
        total_portfolio_value += current_value
        total_invested_value += invested_value
        enriched_assets.append({
            'db_asset': asset,
            'current_value': round(current_value, 2),
            'invested_value': round(invested_value, 2),
            'is_live': is_live_price
        })
    overall_gain_loss = total_portfolio_value - total_invested_value
    return render_template(
        'dashboard.html', 
        current_user=current_user, 
        assets=enriched_assets, 
        total_value=round(total_portfolio_value, 2),
        total_invested=round(total_invested_value, 2),
        gain_loss=round(overall_gain_loss, 2)
    )

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(email=email, password_hash=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        else:
            return "Login Failed. Please check your email and password."
    return render_template('login.html')

@app.route('/add_asset', methods=['GET', 'POST'])
def add_asset():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        new_asset = Asset(
            user_id=session['user_id'],
            asset_name=request.form['asset_name'],
            asset_type=request.form['asset_type'],
            ticker_symbol=request.form['ticker_symbol'],
            quantity=float(request.form['quantity']),
            purchase_price=float(request.form['purchase_price']),
            purchase_date=request.form['purchase_date']
        )
        db.session.add(new_asset)
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('add_asset.html')

@app.route('/edit_asset/<int:asset_id>', methods=['GET', 'POST'])
def edit_asset(asset_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    asset_to_edit = Asset.query.get_or_404(asset_id)
    if asset_to_edit.user_id != session['user_id']:
        abort(403)
    if request.method == 'POST':
        asset_to_edit.asset_name = request.form['asset_name']
        asset_to_edit.asset_type = request.form['asset_type']
        asset_to_edit.ticker_symbol = request.form['ticker_symbol']
        asset_to_edit.quantity = float(request.form['quantity'])
        asset_to_edit.purchase_price = float(request.form['purchase_price'])
        asset_to_edit.purchase_date = request.form['purchase_date']
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('edit_asset.html', asset=asset_to_edit)

@app.route('/delete_asset/<int:asset_id>', methods=['POST'])
def delete_asset(asset_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    asset_to_delete = Asset.query.get_or_404(asset_id)
    if asset_to_delete.user_id != session['user_id']:
        abort(403)
    db.session.delete(asset_to_delete)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))
