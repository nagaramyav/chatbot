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
app.secret_key = os.urandom(24)

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
        # First, try to remove the existing database
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
            print("Removed existing database")
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Create users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             username TEXT UNIQUE NOT NULL,
             password TEXT NOT NULL,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
        ''')
        print("Created users table")
        
        # Create documents table with user_id
        c.execute('''
            CREATE TABLE IF NOT EXISTS documents
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             title TEXT NOT NULL,
             content TEXT NOT NULL,
             user_id INTEGER NOT NULL,
             created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
             FOREIGN KEY (user_id) REFERENCES users (id))
        ''')
        print("Created documents table")
        
        # Verify tables were created
        c.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = c.fetchall()
        print(f"Tables in database: {tables}")
        
        # Verify columns in documents table
        c.execute("PRAGMA table_info(documents);")
        columns = c.fetchall()
        print(f"Columns in documents table: {columns}")
        
        conn.commit()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise
    finally:
        if conn:
            conn.close()

# Initialize database when the application starts
with app.app_context():
    try:
        init_db()
        print("Database initialization completed")
    except Exception as e:
        print(f"Failed to initialize database: {e}")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            return render_template('register.html', error='Username and password are required')
        
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Check if username exists
            c.execute('SELECT id FROM users WHERE username = ?', (username,))
            if c.fetchone():
                return render_template('register.html', error='Username already exists')
            
            # Create new user
            hashed_password = generate_password_hash(password)
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                     (username, hashed_password))
            conn.commit()
            conn.close()
            
            return redirect(url_for('login'))
        except Exception as e:
            print(f"Registration error: {e}")
            return render_template('register.html', error='Registration failed')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT id, username, password FROM users WHERE username = ?', (username,))
            user = c.fetchone()
            conn.close()
            
            if user and check_password_hash(user[2], password):
                login_user(User(user[0], user[1]))
                return redirect(url_for('home'))
            
            return render_template('login.html', error='Invalid username or password')
        except Exception as e:
            print(f"Login error: {e}")
            return render_template('login.html', error='Login failed')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
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
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        try:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            text_content = extract_text_from_file(filepath)
            
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('INSERT INTO documents (title, content, user_id) VALUES (?, ?, ?)',
                     (filename, text_content, current_user.id))
            conn.commit()
            conn.close()
            
            os.remove(filepath)  # Clean up the uploaded file
            
            return jsonify({'success': True, 'message': 'File uploaded successfully'})
        except Exception as e:
            print(f"Upload error: {e}")
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': str(e)}), 500
    
    return jsonify({'error': 'Invalid file type'}), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)