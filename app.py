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
from docx import Document  # Make sure to install python-docx
import csv
from plaid.api import plaid_api
from plaid.configuration import Configuration
import PyPDF2

load_dotenv()
app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configure database path for Render
DB_DIR = '/tmp'  # Use /tmp directory in Render
DB_PATH = os.path.join(os.path.dirname(__file__), 'documents.db')  # Store in the project directory

# Use temporary directory for uploads
UPLOAD_FOLDER = tempfile.gettempdir()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Plaid configuration
PLAID_CLIENT_ID = os.getenv('PLAID_CLIENT_ID')
PLAID_SECRET = os.getenv('PLAID_SECRET')
PLAID_ENV = os.getenv('PLAID_ENV', 'sandbox')  # Default to 'sandbox' if not set

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'csv'}

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
        
        # Drop existing tables if they exist
        c.execute('DROP TABLE IF EXISTS documents')
        c.execute('DROP TABLE IF EXISTS users')
        
        # Create users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             username TEXT UNIQUE NOT NULL,
             password TEXT NOT NULL)
        ''')
        
        # Create documents table with user_id
        c.execute('''
            CREATE TABLE IF NOT EXISTS documents
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             title TEXT NOT NULL,
             content TEXT NOT NULL,
             user_id INTEGER NOT NULL,
             FOREIGN KEY (user_id) REFERENCES users (id))
        ''')
        
        conn.commit()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")
    finally:
        conn.close()

# Initialize database
init_db()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Username and password are required')
            return render_template('register.html')
        
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Check if username exists
            c.execute('SELECT id FROM users WHERE username = ?', (username,))
            if c.fetchone():
                flash('Username already exists')
                return render_template('register.html')
            
            # Create new user
            hashed_password = generate_password_hash(password)
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                     (username, hashed_password))
            conn.commit()
            conn.close()
            
            print("Redirecting to login page after successful registration")
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))  # Redirect to the login page
            
        except Exception as e:
            print(f"Registration error: {e}")
            flash('An error occurred during registration')
            return render_template('register.html')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember')  # Get the value of the remember checkbox
        
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT id, username, password FROM users WHERE username = ?', (username,))
            user = c.fetchone()
            conn.close()
            
            if user and check_password_hash(user[2], password):
                user_obj = User(user[0], user[1])
                login_user(user_obj, remember=remember)  # Pass remember parameter
                return redirect(url_for('home'))  # Redirect to the home page
            
            flash('Invalid username or password')
            return render_template('login.html')
            
        except Exception as e:
            print(f"Login error: {e}")
            flash('An error occurred during login')
            return render_template('login.html')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.')
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

        # Prepare the prompt for OpenAI
        if documents:
            combined_context = ""
            for doc in documents:
                combined_context += f"\nDocument: {doc[0]}\n"
                combined_context += f"Content: {doc[1]}\n"
                combined_context += "-" * 50 + "\n"

            # Include document context in the prompt
            prompt = f"""Here are all the available documents:
{combined_context}

Question: {question}
"""
        else:
            # If no documents, just ask the question
            prompt = f"Question: {question}"

        # Call OpenAI API for general questions
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that answers questions based on the provided documents and general knowledge."},
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
            
            # Read the content based on file type
            content = ""
            try:
                if filename.endswith('.pdf'):
                    with open(filepath, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        content = "\n".join(page.extract_text() for page in reader.pages)
                elif filename.endswith('.docx'):
                    doc = Document(filepath)
                    content = "\n".join(paragraph.text for paragraph in doc.paragraphs)
                elif filename.endswith('.txt'):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                elif filename.endswith('.csv'):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        reader = csv.reader(f)
                        content = "\n".join([", ".join(row) for row in reader])
                else:
                    return jsonify({'error': 'Unsupported file type'}), 400
                
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
        return jsonify({'error': str(e)}), 500

@app.route('/link', methods=['GET'])
def link():
    return render_template('link.html')  # Create a link.html file for Plaid Link

@app.route('/exchange_public_token', methods=['POST'])
def exchange_public_token():
    data = request.json
    public_token = data['public_token']
    
    # Initialize the Plaid client
    configuration = Configuration(
        host=PLAID_ENV,
        api_key={
            'clientId': PLAID_CLIENT_ID,
            'secret': PLAID_SECRET,
        }
    )

    client = plaid_api.PlaidApi(configuration)

    # Exchange the public token for an access token
    exchange_response = client.item.public_token.exchange(public_token)
    access_token = exchange_response['access_token']
    item_id = exchange_response['item_id']
    
    # Store access_token and item_id securely (e.g., in a database)
    
    return jsonify({'access_token': access_token, 'item_id': item_id})

@app.route('/get_balance', methods=['POST'])
def get_balance():
    access_token = request.json['access_token']
    
    # Retrieve account balance
    response = client.accounts.balance.get(access_token)
    accounts = response['accounts']
    
    # Extract balance information
    balances = {account['name']: account['balances'] for account in accounts}
    
    return jsonify(balances)

def read_pdf(file_path):
    with open(file_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        text = ''
        for page in reader.pages:
            text += page.extract_text() + '\n'
        return text

@app.route('/upload_resume', methods=['POST'])
def upload_resume():
    resume_file = request.files['resume']
    resume_path = f'uploads/{resume_file.filename}'
    resume_file.save(resume_path)

    # Read the resume text
    resume_text = read_pdf(resume_path)

    # Here you can call your resume matching logic
    # For example: match_resume_with_jd(resume_text, jd_text)

    return jsonify({'message': 'Resume uploaded successfully!'})

@app.route('/upload_jd', methods=['POST'])
def upload_jd():
    jd_file = request.files['jd']
    jd_path = f'uploads/{jd_file.filename}'
    jd_file.save(jd_path)

    # Read the JD text
    jd_text = read_pdf(jd_path)

    # Here you can call your resume matching logic
    # For example: match_resume_with_jd(resume_text, jd_text)

    return jsonify({'message': 'Job description uploaded successfully!'})

def match_resume_with_jd(resume_text, jd_text):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "Act as an HR assistant comparing a given job description against a candidate's resume."
            },
            {
                "role": "user",
                "content": f"Evaluate the following candidate's resume for the following job description:\n\nJob Description: {jd_text}\n\nResume: {resume_text}"
            }
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "improvement_schema",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "match_score": {
                            "type": "integer",
                            "description": "An integer score representing the match, on a scale from 1 to 10."
                        },
                        "scope_of_improvement": {
                            "type": "string",
                            "description": "A description of the potential areas for improvement."
                        }
                    },
                    "required": [
                        "match_score",
                        "scope_of_improvement"
                    ],
                    "additionalProperties": False
                }
            }
        },
        temperature=1,
        max_completion_tokens=2048,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0
    )
    
    return response['choices'][0]['message']['content']

if __name__ == '__main__':
    with app.app_context():
        init_db()  # Initialize the database
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)