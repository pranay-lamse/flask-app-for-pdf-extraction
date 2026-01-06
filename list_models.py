import requests

API_KEY = "AIzaSyAtNxYl85HnOTHNuzki3P7cx5t9eKZxXas"

# List available models
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"

response = requests.get(url)

if response.status_code == 200:
    models = response.json()
    print("Available models with generateContent support:\n")
    for model in models.get('models', []):
        model_name = model.get('name', '')
        supported_methods = model.get('supportedGenerationMethods', [])
        if 'generateContent' in supported_methods:
            print(f"  - {model_name}")
            print(f"    Display Name: {model.get('displayName', 'N/A')}")
            print(f"    Supported Methods: {', '.join(supported_methods)}")
            print()
else:
    print(f"Error: {response.status_code}")
    print(response.json())
