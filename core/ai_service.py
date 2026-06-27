import os
import google.generativeai as genai
import chromadb
from django.conf import settings
from .models import DocumentChunk, AcademicDocument

class EmbeddingManager:
    """Handles all vector embedding operations."""
    def __init__(self):
        # Switched to models/gemini-embedding-001 for guaranteed compatibility
        self.model_name = "models/gemini-embedding-001"
        self.api_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)
        if self.api_key:
            genai.configure(api_key=self.api_key)

    def embed_texts(self, texts, task_type="retrieval_document"):
        """Generate embeddings in batch for better performance."""
        try:
            result = genai.embed_content(
                model=self.model_name,
                content=texts,
                task_type=task_type
            )
            return result['embedding']
        except Exception as e:
            print(f"Embedding error: {e}")
            raise e

class ChatManager:
    """Handles AI chat completions and streaming."""
    def __init__(self):
        # Switched to 3.1-flash-lite-preview for potentially better quota availability
        self.model_name = 'models/gemini-3.1-flash-lite-preview'
        self.api_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)
        if self.api_key:
            genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(self.model_name)

    def generate_response(self, prompt):
        """Generates a synchronous response with retry logic for quota limits."""
        import time
        max_retries = 3
        for i in range(max_retries):
            try:
                response = self.model.generate_content(prompt)
                return response.text
            except Exception as e:
                # Handle Quota / Rate Limit (429) errors with backoff
                if "429" in str(e) or "quota" in str(e).lower():
                    if i < max_retries - 1:
                        time.sleep(3 * (i + 1))
                        continue
                print(f"Generation error: {e}")
                return "I'm sorry, I'm experiencing high demand right now. Please wait a few seconds and try asking again!"

    def generate_stream(self, prompt):
        """Generates a streaming response for real-time UI updates."""
        try:
            response = self.model.generate_content(prompt, stream=True)
            for chunk in response:
                # Safely attempt to get text
                try:
                    if chunk.text:
                        yield chunk.text
                except (AttributeError, ValueError):
                    # Handle cases where chunk has no text (e.g. safety block)
                    continue
        except Exception as e:
            print(f"Streaming error: {e}")
            yield " [I encountered a streaming error. Please try again.]"

class AIService:
    """Main service providing an unified interface for AI operations."""
    def __init__(self):
        self.embedding_mgr = EmbeddingManager()
        self.chat_mgr = ChatManager()
        
        # Initialize ChromaDB persistent storage
        db_path = os.path.join(settings.BASE_DIR, "chroma_db")
        self.chroma_client = chromadb.PersistentClient(path=db_path)
        self.collection = self.chroma_client.get_or_create_collection(name="academic_docs")

    def get_context(self, query, user, top_k=3):
        """
        Efficiently retrieves relevant context chunks ONLY for the specified user 
        and formats them for the prompt.
        """
        try:
            query_embedding = self.embedding_mgr.embed_texts([query], task_type="retrieval_query")[0]
            
            # Since ChromaDB filtering can be complex, we fetch more potential matches 
            # and filter by user at the database level.
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=10 
            )
            
            if not results['ids'] or not results['ids'][0]:
                return "", []

            chunk_ids = results['ids'][0]
            
            # Filter by user and keep top K
            db_chunks = DocumentChunk.objects.filter(
                id__in=chunk_ids, 
                document__user=user
            )
            
            # Important: maintain the order from ChromaDB for relevance
            id_to_chunk = {str(c.id): c for c in db_chunks}
            ordered_chunks = [id_to_chunk[cid] for cid in chunk_ids if cid in id_to_chunk][:top_k]

            context_text = "\n\n".join([
                f"--- Source: {c.document.title} (Page {c.page_number}) ---\n{c.content}" 
                for c in ordered_chunks
            ])
            
            return context_text, ordered_chunks
        except Exception as e:
            print(f"Context retrieval error: {e}")
            return "", []

    def get_chat_response(self, query, context_text):
        """Synchronous version of chat response for stability."""
        prompt = self._build_prompt(query, context_text)
        return self.chat_mgr.generate_response(prompt)

    def get_chat_stream(self, query, context_text):
        """Prepares the optimized prompt and returns a streaming response generator."""
        prompt = self._build_prompt(query, context_text)
        return self.chat_mgr.generate_stream(prompt)

    def _build_prompt(self, query, context_text):
        context_status = "The following content is retrieved from the student's private library." if context_text else "No specific documents were found in the student's library for this query. Please provide a helpful answer using your general academic knowledge."
        
        return f"""
        You are "AltaStudy," a high-speed AI study assistant. 
        Your goal is to provide comprehensive, structured, and expert academic answers.

        {context_status}

        RETRIEVED CONTEXT:
        {context_text}

        USER QUESTION: {query}

        INSTRUCTIONS:
        1. Always provide a comprehensive answer with CLEAR visual structure.
        2. Use multiple newlines between paragraphs and double newlines before new topics/headings.
        3. Use Markdown headings (###) for sub-topics.
        4. Integrate RETRIEVED CONTEXT as the primary source.
        5. Add relevant additional insights from your general knowledge.
        6. Use horizontal lines (---) to separate major sections if the answer is long.
        7. Cite: (Source: [Title], Page: [Number]).
        8. Maintain a professional, academic, yet encouraging tone.
        """

    def embed_batch(self, texts):
        return self.embedding_mgr.embed_texts(texts)

    def add_to_vector_store(self, chunk_id, embedding, metadata):
        self.collection.add(
            ids=[str(chunk_id)],
            embeddings=[embedding],
            metadatas=[metadata]
        )

    def query_vector_store(self, query_text, n_results=3):
        # Kept for backward compatibility or direct access
        query_embedding = self.embedding_mgr.embed_texts([query_text], task_type="retrieval_query")[0]
        return self.collection.query(query_embeddings=[query_embedding], n_results=n_results)

    def delete_document_chunks(self, chunk_ids):
        if chunk_ids:
            self.collection.delete(ids=[str(cid) for cid in chunk_ids])
