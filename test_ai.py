import os
import django
import sys

# Set up Django environment
sys.path.append(r'C:\Users\hp\OneDrive\Desktop\HOME\GDG_projects\Academic_copilot')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'academic_copilot.settings')
django.setup()

from core.ai_service import AIService

def test_ai():
    print("Testing AI Service...")
    try:
        service = AIService()
        print(f"API Key found: {'Yes' if service.api_key else 'No'}")
        
        test_text = "What is Hybrid Inheritance?"
        print(f"Testing embedding for: {test_text}")
        embedding = service.embed_text(test_text)
        print("Embedding successful!")
        
        print("Testing vector store query...")
        results = service.query_vector_store(test_text)
        print("Vector store query successful!")
        
        # Test chat response
        print("Testing chat response...")
        response = service.get_chat_response(test_text, [])
        print(f"AI Response success! (Length: {len(response)})")
        print(f"AI Response snippet: {response[:100]}...")
        
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    test_ai()
