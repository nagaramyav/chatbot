from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
from dotenv import load_dotenv
from openai import OpenAI
from werkzeug.utils import secure_filename
import tempfile

load_dotenv()
app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Replace with a real secret key
app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutes session lifetime

# Configure database path for Render
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, 'documents.db')

# Use temporary directory for uploads
UPLOAD_FOLDER = tempfile.gettempdir()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Add this near the top of your file
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT id, username FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()
        conn.close()
        if user:
            return User(user[0], user[1])
    except Exception as e:
        print(f"Error loading user: {e}")
    return None

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Drop existing tables if they exist
        c.execute('DROP TABLE IF EXISTS documents')
        c.execute('DROP TABLE IF EXISTS users')
        
        # Create users table
        c.execute('''
            CREATE TABLE users
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             username TEXT UNIQUE NOT NULL,
             password TEXT NOT NULL,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
        ''')
        
        # Create documents table with user_id
        c.execute('''
            CREATE TABLE documents
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             title TEXT NOT NULL,
             content TEXT NOT NULL,
             user_id INTEGER NOT NULL,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
             FOREIGN KEY (user_id) REFERENCES users (id))
        ''')
        
        conn.commit()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise
    finally:
        conn.close()

# Initialize database when the application starts
with app.app_context():
    # Remove the existing database file if it exists
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("Removed existing database")
    
    # Create new database with updated schema
    init_db()
    print("Created new database with updated schema")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            password = request.form.get('password')
            
            if not username or not password:
                flash('Username and password are required', 'error')
                return render_template('register.html')
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Check if username already exists
            c.execute('SELECT id FROM users WHERE username = ?', (username,))
            if c.fetchone() is not None:
                conn.close()
                flash('Username already exists. Please choose another.', 'error')
                return render_template('register.html')
            
            # Create new user
            hashed_password = generate_password_hash(password)
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                     (username, hashed_password))
            conn.commit()
            conn.close()
            
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            print(f"Registration error: {e}")
            flash('An error occurred during registration', 'error')
            return render_template('register.html')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            password = request.form.get('password')
            
            if not username or not password:
                flash('Please enter both username and password', 'error')
                return render_template('login.html')
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT id, username, password FROM users WHERE username = ?', (username,))
            user = c.fetchone()
            conn.close()
            
            if user is None:
                flash('Username not found. Please register first.', 'error')
                return render_template('login.html')
            
            if check_password_hash(user[2], password):
                login_user(User(user[0], user[1]))
                flash('Logged in successfully!', 'success')
                return redirect(url_for('home'))
            else:
                flash('Invalid password. Please try again.', 'error')
                return render_template('login.html')
            
        except Exception as e:
            print(f"Login error: {e}")
            flash('An error occurred during login. Please try again.', 'error')
            return render_template('login.html')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    try:
        logout_user()
        session.clear()  # Clear all session data
        return redirect(url_for('login'))
    except Exception as e:
        print(f"Logout error: {e}")
        return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Debug: Print current user ID
        print(f"Current user ID: {current_user.id}")
        
        # Debug: Print table schema
        c.execute("PRAGMA table_info(documents);")
        columns = c.fetchall()
        print(f"Documents table columns: {columns}")
        
        # Get documents for current user
        c.execute('SELECT id, title FROM documents WHERE user_id = ?', (current_user.id,))
        documents = c.fetchall()
        print(f"Found {len(documents)} documents for user")
        
        conn.close()
        return render_template('index.html', documents=documents)
    except Exception as e:
        print(f"Error in home route: {e}")
        return render_template('error.html', error=str(e))

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    try:
        data = request.json
        question = data.get('message', '')

        if not question:
            return jsonify({"error": "Question is required"}), 400

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT title, content FROM documents WHERE user_id = ?', (current_user.id,))
        documents = c.fetchall()
        conn.close()

        if not documents:
            return jsonify({"error": "No documents found"}), 404

        combined_context = ""
        for doc in documents:
            combined_context += f"\nDocument: {doc[0]}\n"
            combined_context += f"Content: {doc[1]}\n"
            combined_context += "-" * 50 + "\n"

        prompt = f"""Here are all the available documents:
{combined_context}

Question: {question}
"""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that answers questions based on the provided documents."},
                {"role": "user", "content": prompt}
            ]
        )
        
        return jsonify({"response": response.choices[0].message.content})
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({"error": str(e)}), 500

def get_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        raise

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            # Save the file temporarily
            file.save(filepath)
            
            # Read the content
            content = extract_text_from_file(filepath)
            
            # Save to database
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('INSERT INTO documents (title, content, user_id) VALUES (?, ?, ?)',
                     (filename, content, current_user.id))
            conn.commit()
            conn.close()
            
            # Clean up the file
            os.remove(filepath)
            
            return jsonify({
                'success': True,
                'message': 'File uploaded successfully'
            })
        
        return jsonify({'error': 'Invalid file type'}), 400
    
    except Exception as e:
        print(f"Upload error: {e}")
        return jsonify({'error': str(e)}), 500

def extract_text_from_file(filepath):
    """Extract text content from different file types."""
    try:
        filename = os.path.basename(filepath)
        if filename.endswith('.txt'):
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        elif filename.endswith('.pdf'):
            # Add PDF handling here
            return "PDF content extraction not implemented yet"
        elif filename.endswith(('.doc', '.docx')):
            # Add Word document handling here
            return "Word document extraction not implemented yet"
        else:
            return "Unsupported file type"
    except Exception as e:
        print(f"Text extraction error: {e}")
        return f"Error extracting text: {str(e)}"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)