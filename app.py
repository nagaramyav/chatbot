from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()
app = Flask(__name__)

# Initialize the OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))


# Route to serve the frontend
@app.route('/')
def home():
    return render_template('index.html')

# Route for handling chat requests
# @app.route('/chat', methods=['POST'])
# def chat():
#     user_input = request.json.get('message')
    
#     # Call OpenAI API
#     response = client.chat.completions.create(
#         model="gpt-3.5-turbo",
#         messages=[{"role": "user", "content": user_input}]
#     )
    
#     # Extract the chatbot's response
#     llm_response = response.choices[0].message.content
#     return jsonify({"response": llm_response})

@app.route('/chat', methods=['POST'])
def chat():
    user_input = request.json.get('message')
    
    # Check if the input is a document (e.g., longer than 100 characters)
    if len(user_input) > 100:
        prompt = f"Summarize the following document:\n\n{user_input}"
    else:
        prompt = user_input
    
    # Call OpenAI API
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    
    # Extract the chatbot's response
    llm_response = response.choices[0].message.content
    return jsonify({"response": llm_response})

if __name__ == '__main__':
    app.run(debug=True)