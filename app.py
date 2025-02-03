from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, session, flash
from openai import OpenAI
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import os
import sqlite3
from PyPDF2 import PdfReader
import docx
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.secret_key = os.urandom(24)  # Generate a random secret key
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Configure database path for Render deployment
if os.environ.get('RENDER'):
    DB_DIR = '/opt/render/project/src/'
else:
    DB_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(DB_DIR, 'documents.db')

# Create necessary directories
os.makedirs(DB_DIR, exist_ok=True)
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

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

# Database functions
def init_db():
    try:
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
        
        conn.commit()
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise
    finally:
        conn.close()

def add_document(title, content, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO documents (title, content, user_id) VALUES (?, ?, ?)', 
              (title, content, user_id))
    conn.commit()
    conn.close()

def get_all_documents():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, title, content, created_at FROM documents')
    documents = c.fetchall()
    conn.close()
    return documents

def get_document(doc_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, title, content FROM documents WHERE id = ? AND user_id = ?', 
              (doc_id, user_id))
    document = c.fetchone()
    conn.close()
    return document

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_file(file_path):
    file_extension = file_path.split('.')[-1].lower()
    
    if file_extension == 'txt':
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    
    elif file_extension == 'pdf':
        text = ""
        with open(file_path, 'rb') as file:
            pdf_reader = PdfReader(file)
            for page in pdf_reader.pages:
                text += page.extract_text()
        return text
    
    elif file_extension in ['doc', 'docx']:
        doc = docx.Document(file_path)
        return ' '.join([paragraph.text for paragraph in doc.paragraphs])

# Initialize database
with app.app_context():
    init_db()

@app.route('/')
@login_required
def home():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, title FROM documents WHERE user_id = ?', (current_user.id,))
    documents = c.fetchall()
    conn.close()
    return render_template('index.html', documents=documents)

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Extract text from file
        try:
            text_content = extract_text_from_file(filepath)
            # Save to database
            add_document(filename, text_content, current_user.id)
            
            # Clean up uploaded file
            os.remove(filepath)
            
            return jsonify({'success': True, 'message': 'File uploaded and processed successfully'})
        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': f'Error processing file: {str(e)}'}), 500
    
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        question = data.get('message', '')

        if not question:
            return jsonify({"error": "Question is required"}), 400

        # Get all documents from database
        documents = get_all_documents()
        if not documents:
            return jsonify({"error": "No documents found in the system"}), 404

        # Create prompt with all documents
        combined_context = ""
        for doc in documents:
            combined_context += f"\nDocument: {doc[1]}\n"
            combined_context += f"Content: {doc[2]}\n"
            combined_context += "-" * 50 + "\n"

        prompt = f"""Here are all the available documents:
---
{combined_context}
---

Please answer the following question using information from any of the above documents:
{question}

If providing information from multiple documents, please specify which document(s) you're referencing.
If the answer cannot be found in any of the documents, please respond with "I cannot find the answer to this question in any of the provided documents."
"""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo-16k",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that answers questions based on the provided documents. Only use information from the documents to answer questions. When answering, cite which document(s) you're using for the information."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        
        return jsonify({"response": response.choices[0].message.content})
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/delete_document/<int:doc_id>', methods=['DELETE'])
def delete_document(doc_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Check if document exists
        c.execute('SELECT title FROM documents WHERE id = ?', (doc_id,))
        document = c.fetchone()
        
        if document is None:
            conn.close()
            return jsonify({'error': 'Document not found'}), 404
            
        # Delete the document
        c.execute('DELETE FROM documents WHERE id = ?', (doc_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'Document deleted successfully'})
    except Exception as e:
        print(f"Error deleting document: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File is too large. Maximum size is 16MB"}), 413

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            password = request.form.get('password')
            
            if not username or not password:
                flash('Username and password are required')
                return render_template('register.html', error='Username and password are required')
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Check if username already exists
            c.execute('SELECT id FROM users WHERE username = ?', (username,))
            if c.fetchone() is not None:
                conn.close()
                flash('Username already exists')
                return render_template('register.html', error='Username already exists')
            
            # Create new user
            hashed_password = generate_password_hash(password)
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                     (username, hashed_password))
            conn.commit()
            conn.close()
            
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))
            
        except Exception as e:
            print(f"Registration error: {e}")
            flash('An error occurred during registration')
            return render_template('register.html', error='Registration failed')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            password = request.form.get('password')
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT id, username, password FROM users WHERE username = ?', (username,))
            user = c.fetchone()
            conn.close()
            
            if user and check_password_hash(user[2], password):
                login_user(User(user[0], user[1]))
                flash('Logged in successfully!')
                return redirect(url_for('home'))
            
            flash('Invalid username or password')
            return render_template('login.html', error='Invalid username or password')
            
        except Exception as e:
            print(f"Login error: {e}")
            flash('An error occurred during login')
            return render_template('login.html', error='Login failed')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)