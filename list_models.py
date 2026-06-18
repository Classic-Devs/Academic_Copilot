import os
import django
import sys
import google.generativeai as genai

# Set up Django environment
sys.path.append(r'C:\Users\hp\OneDrive\Desktop\HOME\GDG_projects\Academic_copilot')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'academic_copilot.settings')
django.setup()

from core.ai_service import AIService

def list_models():
    service = AIService()
    print("Available generation models:")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f"  - {m.name}")
    except Exception as e:
        print(f"ERROR listing models: {e}")

if __name__ == "__main__":
    list_models()
