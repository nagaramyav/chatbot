from transformers import AutoModelForCausalLM, AutoTokenizer

# Step 1: Load the pre-trained GPT-2 model and tokenizer
model_name = "gpt2"  # You can also use "gpt2-medium", "gpt2-large", etc.
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(model_name)

# Step 2: Create a function to generate text
def generate_response(prompt, max_length=50):
    # Add context to the prompt
    structured_prompt = f"User: {prompt}\nChatbot:"
    
    # Tokenize the input prompt and create an attention mask
    inputs = tokenizer(structured_prompt, return_tensors="pt")
    input_ids = inputs["input_ids"]
    attention_mask = inputs["attention_mask"]
    
    # Generate text using the model
    output = model.generate(
        input_ids,
        attention_mask=attention_mask,  # Pass the attention mask
        max_length=max_length,  # Maximum length of the generated text
        num_return_sequences=1,  # Number of responses to generate
        no_repeat_ngram_size=2,  # Avoid repeating phrases
        do_sample=True,  # Enable sampling-based generation
        top_k=50,  # Limit sampling to the top-k tokens
        top_p=0.95,  # Nucleus sampling (controls diversity)
        temperature=0.7,  # Controls randomness (lower = more deterministic)
        pad_token_id=tokenizer.eos_token_id,  # Set pad token ID
    )
    
    # Decode the generated text and extract the chatbot's response
    response = tokenizer.decode(output[0], skip_special_tokens=True)
    response = response[len(structured_prompt):].strip()  # Remove the input prompt
    
    # Clean up the response
    response = response.split("\n")[0]  # Take only the first line
    response = response.split(".")[0] + "." if "." in response else response  # End at the first sentence
    response = response.replace("..", ".").replace("  ", " ")  # Fix double periods and spaces
    return response

# Step 3: Build the chatbot loop
def chatbot():
    print("Chatbot: Hello! I'm your friendly chatbot. Type 'exit' to end the conversation.")
    
    while True:
        # Get user input
        user_input = input("You: ")
        
        # Exit the loop if the user types 'exit'
        if user_input.lower() == "exit":
            print("Chatbot: Goodbye!")
            break
        
        # Generate a response
        response = generate_response(user_input)
        
        # Print the chatbot's response
        print("Chatbot:", response)

# Run the chatbot
chatbot()