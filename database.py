import sqlite3
from datetime import datetime

def init_db():
    conn = sqlite3.connect('documents.db')
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
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute('INSERT INTO documents (title, content) VALUES (?, ?)', (title, content))
    conn.commit()
    conn.close()

def get_all_documents():
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute('SELECT id, title, content, created_at FROM documents')
    documents = c.fetchall()
    conn.close()
    return documents

def get_document(doc_id):
    conn = sqlite3.connect('documents.db')
    c = conn.cursor()
    c.execute('SELECT id, title, content FROM documents WHERE id = ?', (doc_id,))
    document = c.fetchone()
    conn.close()
    return document
