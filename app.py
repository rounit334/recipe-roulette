from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import mysql.connector
import requests
from config import SPOONACULAR_API_KEY, DB_CONFIG
import config
import hashlib
import datetime
from authlib.integrations.flask_client import OAuth
import json

app = Flask(__name__)
app.secret_key = 'recipe123secret'

# Initialize OAuth
oauth = OAuth(app)

# Configure Google OAuth
google = oauth.register(
    name='google',
    client_id=config.GOOGLE_CLIENT_ID,
    client_secret=config.GOOGLE_CLIENT_SECRET,
    server_metadata_url=config.GOOGLE_DISCOVERY_URL,
    client_kwargs={'scope': 'openid email profile'}
)

# Database connection function
def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

# Home route - serves the HTML page (requires login)
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

#dashboard routes
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get user info from session
    username = session.get('username', 'User')
    user_email = session.get('email', 'user@example.com')
    user_id = session.get('user_id')
    
    # Get statistics from database
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Count total searches
    cursor.execute("SELECT COUNT(*) as total FROM user_activity WHERE user_id = %s AND activity_type = 'search'", (user_id,))
    total_searches = cursor.fetchone()['total']
    
    # Count shopping list items
    cursor.execute("SELECT COUNT(*) as total FROM shopping_list WHERE purchased = FALSE", ())
    shopping_items = cursor.fetchone()['total']
    # Get recent activities (last 5)
    cursor.execute("""
    SELECT activity_type, activity_details, activity_date 
    FROM user_activity 
    WHERE user_id = %s 
    ORDER BY activity_date DESC 
    LIMIT 5
    """, (user_id,))
    recent_activities = cursor.fetchall()
    
    # Get or create monthly budget
    current_month = datetime.datetime.now().strftime('%Y-%m')

    cursor.execute("""
        SELECT monthly_budget 
        FROM user_budget 
        WHERE user_id = %s AND month_year = %s
    """, (user_id, current_month))
    budget_data = cursor.fetchone()

    if budget_data:
        monthly_budget = budget_data['monthly_budget']
    else:
        # Create default budget
        cursor.execute("""
            INSERT INTO user_budget (user_id, month_year, monthly_budget) 
            VALUES (%s, %s, 3000.00)
        """, (user_id, current_month))
        conn.commit()
        monthly_budget = 3000.00

    # For now, use fake spent amount (you can enhance this later)
    budget_spent = float(2340.00)
    budget_remaining = float(monthly_budget) - budget_spent
    budget_percentage = int((budget_spent / float(monthly_budget)) * 100)
    budget_percentage_str = f"{budget_percentage}%"
    
    cursor.close()
    conn.close()
    
    # Calculate other stats (these can be enhanced later)
    recipes_found = total_searches * 6  # Average 6 recipes per search
    
    return render_template('dashboard.html', 
                     username=username, 
                     email=user_email,
                     total_searches=total_searches,
                     recipes_found=recipes_found,
                     shopping_items=shopping_items,
                     recent_activities=recent_activities,
                     monthly_budget=monthly_budget,
                     budget_spent=budget_spent,
                     budget_remaining=budget_remaining,
                     budget_percentage=budget_percentage,
                     budget_percentage_str=budget_percentage_str)

# Update monthly budget
@app.route('/update-budget', methods=['POST'])
def update_budget():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    new_budget = data.get('budget')
    
    if not new_budget or float(new_budget) <= 0:
        return jsonify({'error': 'Invalid budget amount'}), 400
    
    user_id = session.get('user_id')
    current_month = datetime.datetime.now().strftime('%Y-%m')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Update or insert budget
    cursor.execute("""
        INSERT INTO user_budget (user_id, month_year, monthly_budget) 
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE monthly_budget = %s
    """, (user_id, current_month, new_budget, new_budget))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'message': 'Budget updated successfully'})

# Search recipes route
@app.route('/search-recipes', methods=['POST'])
def search_recipes():
    data = request.get_json()
    ingredients = data.get('ingredients', [])
    
    # Join ingredients into comma-separated string
    ingredients_str = ','.join(ingredients)
    
    # Call Spoonacular API
    url = f"https://api.spoonacular.com/recipes/findByIngredients"
    params = {
        'ingredients': ingredients_str,
        'number': 6,
        'apiKey': SPOONACULAR_API_KEY
    }
    
    response = requests.get(url, params=params)
    recipes = response.json()
    
    # Track the search activity
    if 'user_id' in session:
        conn = get_db_connection()
        cursor = conn.cursor()
        activity_details = f"With: {', '.join(ingredients[:3])}"  # First 3 ingredients
        cursor.execute("INSERT INTO user_activity (user_id, activity_type, activity_details) VALUES (%s, 'search', %s)", 
                      (session['user_id'], activity_details))
        conn.commit()
        cursor.close()
        conn.close()
    
    return jsonify(recipes)

#add to list

@app.route('/add-to-list', methods=['POST'])
def add_to_list():
    data = request.get_json()
    ingredient = data.get('ingredient')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if ingredient already exists
    cursor.execute("SELECT * FROM shopping_list WHERE ingredient_name = %s AND purchased = FALSE", (ingredient,))
    existing = cursor.fetchone()
    
    if existing:
        cursor.close()
        conn.close()
        return jsonify({'message': 'Ingredient already in list'}), 400
    
    # Add ingredient
    cursor.execute("INSERT INTO shopping_list (ingredient_name) VALUES (%s)", (ingredient,))
    conn.commit()
    
    # Track activity
    if 'user_id' in session:
        cursor.execute("INSERT INTO user_activity (user_id, activity_type, activity_details) VALUES (%s, 'add_to_list', %s)", 
                      (session['user_id'], ingredient))
        conn.commit()
    
    cursor.close()
    conn.close()
    
    return jsonify({'message': 'Ingredient added successfully'})

# Get shopping list
@app.route('/get-shopping-list', methods=['GET'])
def get_shopping_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM shopping_list WHERE purchased = FALSE ORDER BY date_added DESC")
    items = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return jsonify(items)

# Mark item as purchased
@app.route('/mark-purchased', methods=['POST'])
def mark_purchased():
    data = request.get_json()
    item_id = data.get('id')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE shopping_list SET purchased = TRUE WHERE id = %s", (item_id,))
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'message': 'Item marked as purchased'})

# Login/Signup page
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        print(f"Login attempt - Email: {email}")  # DEBUG
        
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        print(f"Hashed password: {hashed_password}")  # DEBUG
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s AND password = %s", (email, hashed_password))
        user = cursor.fetchone()
        
        print(f"User found: {user}")  # DEBUG
        
        cursor.close()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['email'] = user['email'] 
            return redirect(url_for('home'))
        else:
            return "Invalid credentials. <a href='/login'>Try again</a>"
    
    return render_template('auth.html')

# Google OAuth Login Route
@app.route('/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

# Google OAuth Callback Route
@app.route('/auth/google/callback')
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        
        if user_info:
            email = user_info['email']
            username = user_info.get('name', email.split('@')[0])
            
            # Check if user exists
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            
            if user:
                # User exists, log them in
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['email'] = user['email']
            else:
                # Create new user
                cursor.execute("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)", 
                              (username, email, 'google_oauth'))  # No password for OAuth users
                conn.commit()
                
                # Get the new user's ID
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['email'] = user['email']
            
            cursor.close()
            conn.close()
            
            return redirect(url_for('home'))
        
        return redirect(url_for('login'))
        
    except Exception as e:
        print(f"Error in Google callback: {e}")
        return redirect(url_for('login'))

# Signup route (uses same auth.html page)
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)", 
                          (username, email, hashed_password))
            conn.commit()
            cursor.close()
            conn.close()
            return redirect(url_for('login'))
        except:
            cursor.close()
            conn.close()
            return "Username or email already exists. <a href='/login'>Try again</a>"
    
    return render_template('auth.html')

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)