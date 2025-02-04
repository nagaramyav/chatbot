from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
from dotenv import load_dotenv
from openai import OpenAI
from werkzeug.utils import secure_filename
import tempfile
import traceback  # Add this for better error tracking

load_dotenv()
app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configure database path for Render
DB_DIR = '/tmp'  # Use /tmp directory in Render
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

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

# User class definition
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
        return None
    except Exception as e:
        print(f"Error loading user: {e}")
        return None

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Create users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             username TEXT UNIQUE NOT NULL,
             password TEXT NOT NULL)
        ''')
        
        # Create documents table
        c.execute('''
            CREATE TABLE IF NOT EXISTS documents
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             title TEXT NOT NULL,
             content TEXT NOT NULL,
             user_id INTEGER NOT NULL)
        ''')
        
        conn.commit()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")
        print(traceback.format_exc())
    finally:
        if conn:
            conn.close()

# Initialize database
init_db()

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
                user_obj = User(user[0], user[1])
                login_user(user_obj)
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
        c.execute('SELECT id, title FROM documents WHERE user_id = ?', (current_user.id,))
        documents = c.fetchall()
        conn.close()
        return render_template('index.html', documents=documents)
    except Exception as e:
        print(f"Home error: {e}")
        print(traceback.format_exc())
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

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    print("Upload request received")  # Debug log
    try:
        if 'file' not in request.files:
            print("No file in request")  # Debug log
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        if file.filename == '':
            print("No filename")  # Debug log
            return jsonify({'error': 'No selected file'}), 400
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            print(f"Saving file to: {filepath}")  # Debug log
            
            # Save the file temporarily
            file.save(filepath)
            print("File saved temporarily")  # Debug log
            
            # Read the content
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                print("File content read successfully")  # Debug log
            except Exception as e:
                print(f"Error reading file: {e}")  # Debug log
                return jsonify({'error': f'Error reading file: {str(e)}'}), 500
            finally:
                # Clean up the temporary file
                if os.path.exists(filepath):
                    os.remove(filepath)
            
            # Save to database
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('INSERT INTO documents (title, content, user_id) VALUES (?, ?, ?)',
                         (filename, content, current_user.id))
                conn.commit()
                print("Document saved to database")  # Debug log
            except Exception as e:
                print(f"Database error: {e}")  # Debug log
                return jsonify({'error': f'Database error: {str(e)}'}), 500
            finally:
                if conn:
                    conn.close()
            
            return jsonify({
                'success': True,
                'message': 'File uploaded successfully'
            })
        
        print("Invalid file type")  # Debug log
        return jsonify({'error': 'Invalid file type'}), 400
    
    except Exception as e:
        print(f"Upload error: {e}")  # Debug log
        print(traceback.format_exc())  # Print full traceback
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)