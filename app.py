from flask import Flask, request, jsonify, render_template, send_from_directory
from openai import OpenAI
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import os
import database
from PyPDF2 import PdfReader
import docx

load_dotenv()
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create uploads directory if it doesn't exist
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Initialize database
database.init_db()

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

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

@app.route('/')
def home():
    documents = database.get_all_documents()
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
            database.add_document(filename, text_content)
            
            # Clean up uploaded file
            os.remove(filepath)
            
            return jsonify({'success': True, 'message': 'File uploaded and processed successfully'})
        except Exception as e:
            return jsonify({'error': f'Error processing file: {str(e)}'}), 500
    
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        question = data.get('message', '')
        doc_id = data.get('documentId')

        if not question or not doc_id:
            return jsonify({"error": "Both question and document ID are required"}), 400

        # Get document from database
        document = database.get_document(doc_id)
        if not document:
            return jsonify({"error": "Document not found"}), 404

        # Create prompt with document content
        prompt = f"""Here is a document titled "{document[1]}":
---
{document[2]}
---

Please answer the following question about the document:
{question}

If the answer cannot be found in the document, please respond with "I cannot find the answer to this question in the provided document."
"""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo-16k",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that answers questions based on the provided document. Only use information from the document to answer questions."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        
        return jsonify({"response": response.choices[0].message.content})
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)