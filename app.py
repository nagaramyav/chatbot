from flask import Flask, request, jsonify, render_template, send_from_directory
from openai import OpenAI
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import os
import sqlite3
from PyPDF2 import PdfReader
import docx

load_dotenv()
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

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

# Database functions
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS documents
        (id INTEGER PRIMARY KEY AUTOINCREMENT,
         title TEXT NOT NULL,
         content TEXT NOT NULL,
         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
    ''')
    conn.commit()
    conn.close()

def add_document(title, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO documents (title, content) VALUES (?, ?)', (title, content))
    conn.commit()
    conn.close()

def get_all_documents():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, title, content, created_at FROM documents')
    documents = c.fetchall()
    conn.close()
    return documents

def get_document(doc_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, title, content FROM documents WHERE id = ?', (doc_id,))
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
init_db()

@app.route('/')
def home():
    documents = get_all_documents()
    return render_template('index.html', documents=documents)

@app.route('/upload', methods=['POST'])
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
            add_document(filename, text_content)
            
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)