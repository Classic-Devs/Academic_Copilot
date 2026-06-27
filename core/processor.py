import os
import pypdf
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import AcademicDocument, DocumentChunk
from .ai_service import AIService

class DocumentProcessor:
    def __init__(self, document):
        self.document = document
        self.ai_service = AIService()

    def process(self):
        try:
            # 0. Clear old chunks if any (for re-processing)
            DocumentChunk.objects.filter(document=self.document).delete()
            
            # 1. Read PDF and collect all chunks first
            pdf_path = self.document.file.path
            all_chunks_data = [] # List of (page_num, chunk_index, text)
            
            with open(pdf_path, 'rb') as f:
                reader = pypdf.PdfReader(f)
                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if not text:
                        continue
                    
                    # Implementation of recursive chunking (1000 size, 200 overlap)
                    page_chunks = self.recursive_split_text(text, chunk_size=1000, overlap=200)
                    for j, chunk_text in enumerate(page_chunks):
                        all_chunks_data.append((i+1, j, chunk_text))

            # 2. Process in batches (increased to 20 for better speed)
            batch_size = 20
            for i in range(0, len(all_chunks_data), batch_size):
                batch = all_chunks_data[i : i + batch_size]
                batch_texts = [item[2] for item in batch]
                
                # Embed batch
                embeddings = self.ai_service.embed_batch(batch_texts)
                
                for k, (page_num, chunk_idx, text) in enumerate(batch):
                    vector_id = f"doc_{self.document.id}_p{page_num}_c{chunk_idx}"
                    db_chunk = DocumentChunk.objects.create(
                        document=self.document,
                        content=text,
                        page_number=page_num,
                        chunk_index=chunk_idx,
                        vector_id=vector_id
                    )
                    
                    metadata = {
                        "document_id": self.document.id,
                        "title": self.document.title,
                        "page": page_num,
                        "chunk_index": chunk_idx
                    }
                    self.ai_service.add_to_vector_store(db_chunk.id, embeddings[k], metadata)

            self.document.status = 'ready'
            self.document.error_message = None
            self.document.save()
        except Exception as e:
            self.document.status = 'error'
            self.document.error_message = str(e)
            self.document.save()
            print(f"Error processing document {self.document.id}: {e}")

    def recursive_split_text(self, text, chunk_size=1000, overlap=200):
        """
        Implementation of Recursive Character Text Splitting.
        Splits text into chunks of roughly chunk_size, respecting boundaries:
        Paragraphs (\n\n) -> Newlines (\n) -> Sentences (. ) -> Words ( ) -> Characters.
        """
        separators = ["\n\n", "\n", ". ", " ", ""]
        chunks = []
        
        def split_recursive(raw_text, seps):
            if len(raw_text) <= chunk_size:
                return [raw_text]
            
            if not seps:
                return [raw_text[i:i+chunk_size] for i in range(0, len(raw_text), chunk_size)]
            
            sep = seps[0]
            parts = raw_text.split(sep)
            
            final_parts = []
            current_part = ""
            
            for p in parts:
                # Add separator back except for the last part if it was the end
                p_with_sep = p + sep if p != parts[-1] else p
                
                if len(current_part) + len(p_with_sep) <= chunk_size:
                    current_part += p_with_sep
                else:
                    if current_part:
                        final_parts.append(current_part)
                    
                    # If the part itself is larger than chunk_size, recurse with next separator
                    if len(p_with_sep) > chunk_size:
                        final_parts.extend(split_recursive(p_with_sep, seps[1:]))
                        current_part = ""
                    else:
                        current_part = p_with_sep
            
            if current_part:
                final_parts.append(current_part)
                
            return final_parts

        raw_chunks = split_recursive(text, separators)
        
        # Apply overlap logic
        if overlap <= 0:
            return raw_chunks
            
        final_chunks = []
        for i, chunk in enumerate(raw_chunks):
            if i == 0:
                final_chunks.append(chunk)
            else:
                # Add a bit of the previous chunk if possible
                prev_overlap = raw_chunks[i-1][-overlap:] if len(raw_chunks[i-1]) >= overlap else raw_chunks[i-1]
                final_chunks.append(prev_overlap + chunk)
                
        return final_chunks

@receiver(post_save, sender=AcademicDocument)
def trigger_document_processing(sender, instance, created, **kwargs):
    # Only auto-process on creation AND when status is explicitly 'processing'.
    # 'pending' is used by AcquireDocumentView to avoid premature processing
    # before the file is saved.
    if created and instance.status == 'processing':
        import threading
        processor = DocumentProcessor(instance)
        thread = threading.Thread(target=processor.process)
        thread.start()
