AltaStudy — Enhanced Project Documentation
Executive Summary
AltaStudy is an intelligent, context-aware study and research assistant built specifically for academic environments. It bridges the gap between static course materials and dynamic AI-powered interaction, allowing students to query their own uploaded resources — lecture notes, past papers, textbooks — and receive precise, grounded answers.
Unlike general-purpose AI assistants that rely solely on pre-trained knowledge, AltaStudy anchors every response in the student's actual course content. This is achieved through a technique called Retrieval-Augmented Generation (RAG), which significantly reduces hallucinations and ensures answers are always traceable back to real source material.
The project was designed as a flagship workshop demonstration for GDG (Google Developer Groups), showcasing modern AI engineering patterns in a practical, student-facing product.

The Problem It Solves
Students today face a paradox: they have access to more learning material than ever before, yet finding specific answers within hundreds of pages of notes and past papers remains painfully slow. General AI tools can answer broad questions but have no knowledge of a specific university's curriculum, a professor's unique terminology, or a department's past exam patterns.
AltaStudy solves this by creating a personalized knowledge engine — one that knows exactly what you've uploaded and reasons exclusively within that boundary.

System Architecture
The application is built on a five-layer RAG pipeline, where each layer has a distinct and essential responsibility.
Layer 1 — Ingestion
When a user uploads a document (PDF, lecture slides, or text file), the system does not store it as a monolithic file. Instead, it breaks the document down into smaller, semantically meaningful segments called chunks. This chunking strategy is deliberate — smaller segments allow for more precise retrieval later, ensuring the system can pinpoint the exact paragraph relevant to a question rather than returning an entire document.
Layer 2 — Embedding
Each text chunk is then passed through Google's text-embedding-004 model, which transforms raw text into a sequence of 768 numerical values known as a vector. This vector is essentially a mathematical fingerprint of the chunk's meaning. Two chunks that discuss similar concepts will produce vectors that are numerically close to each other, even if they use different words — a property that makes semantic search possible.
Layer 3 — Storage
The generated vectors are stored in a Vector Database (ChromaDB), a specialized data store optimized for high-speed similarity searches across large collections of vectors. Alongside the vectors, standard relational metadata — such as document title, upload date, and file reference — is stored in a conventional SQLite database. Both databases work in tandem.
Layer 4 — Retrieval
When a student submits a question, that question is itself converted into a vector using the same embedding model. The system then performs a similarity search against the vector database, identifying the top matching chunks whose meaning is closest to the question. This step is the core intelligence of the RAG system — it replaces keyword matching with genuine semantic understanding.
Layer 5 — Generation
The retrieved chunks are assembled into a structured context block and combined with the student's original question into a carefully engineered prompt. This augmented prompt is then sent to Gemini 1.5 Flash, which generates a fluent, accurate, and contextually grounded answer. Because the model is explicitly instructed to reason from the provided context, its responses are constrained to what exists in the uploaded materials.

Technical Stack
ComponentTechnologyPurposeBackend FrameworkDjango (Python)Application logic, routing, file handlingAI EngineGoogle Gemini APIEmbedding generation and response synthesisEmbedding Modeltext-embedding-004Converts text to 768-dimensional vectorsGeneration ModelGemini 1.5 FlashProduces final natural language answersVector DatabaseChromaDBStores and queries semantic vectors locallyRelational DatabaseSQLiteStores file metadata and document recordsPDF ProcessingPyPDF2 / LangChainExtracts and chunks raw text from documents

Data Architecture
The system maintains two parallel data models that together represent the full lifecycle of an uploaded document.
Academic Document records capture the high-level metadata of each upload — the file itself, its title, and when it was added to the system. This is what the user sees in their document library.
Document Chunk records are the granular units created during ingestion. Each chunk belongs to a parent document and stores both the raw text segment and its corresponding vector representation. In a development environment, vectors are stored as JSON arrays. In a production environment, a dedicated PostgreSQL extension such as pgvector would replace this for significantly better query performance at scale.

User Experience Flow
From a student's perspective, the interaction is seamless and intuitive, but the underlying journey through the system is rich.
Uploading a Document — A student visits the upload interface and submits a PDF. In the background, the system immediately begins the ingestion pipeline: extracting text, chunking it, generating embeddings, and persisting everything to the appropriate databases. By the time the upload confirmation appears, the document is fully indexed and ready to be queried.
Asking a Question — The student types a question in plain, natural language — such as "Explain the difference between supervised and unsupervised learning as covered in Week 4." The system converts this into a query vector, retrieves the three most semantically relevant chunks from the student's library, and constructs an augmented prompt. Gemini then synthesizes an answer that reflects exactly what was taught in that student's course materials.
Receiving an Answer — The response is presented in the chat interface, grounded in the student's own uploaded content, not in generic internet knowledge.

UI & Design System — Midnight Aurora Theme
AltaStudy's interface follows a premium design language called Midnight Aurora, built for focus, clarity, and visual sophistication.
Visual Identity — The application uses deep Obsidian backgrounds as its canvas, overlaid with rich Purple-to-Indigo gradients that give the interface a distinctive, high-contrast aesthetic. This palette was chosen deliberately to reduce eye strain during extended study sessions while maintaining a sense of energy and modernity.
Hero Section — The landing page opens with a bold "Collaborative Library" headline, immediately communicating the platform's core value proposition. An animated "Community Sharing" badge draws attention and signals to new users that this is a living, social platform — not just a personal tool.
Global Library Section — A dedicated section of the landing page introduces the Global Resource Sharing feature, explaining how students can discover and contribute academic papers from a worldwide community. This section uses feature cards with iconography to make the capability feel approachable and tangible.
Search & Filter Highlights — The landing page actively promotes the precision discovery tools available within the app, specifically the ability to filter shared resources by Field of Study. This is framed as a core differentiator, helping new users immediately understand that the platform is built for focused, intentional research — not general browsing.

Implementation Roadmap
The build session is structured as four progressive phases, each delivering a functional milestone.
Phase 1 — Document Ingestion (1 hour)
Configure the Django file upload form, define storage paths, and ensure PDFs are reliably saved to the server with proper metadata recorded in the relational database.
Phase 2 — Text Processing (1 hour)
Build the extraction and chunking pipeline. This involves parsing PDF content into clean text, applying a chunking strategy with appropriate overlap between segments to preserve context across boundaries, and preparing the data for embedding.
Phase 3 — Embedding & Vector Storage (1 hour)
Integrate the Gemini Embeddings API to process each chunk, initialize the ChromaDB collection, and build the indexing logic that links vector records back to their source document and chunk records.
Phase 4 — Chat Interface (Final Phase)
Build the conversational frontend where students submit questions and receive answers. This phase connects the retrieval and generation layers into a complete, end-to-end user experience.

Future Feature Roadmap
Multi-modal Input Support
Extend the ingestion pipeline to accept images — photographs of handwritten notes, whiteboard diagrams, or annotated textbook pages. The embedding layer would process these through a vision model before storing them alongside text-based chunks.
Citation & Source Linking
Surface the source chunk alongside every generated answer, with a direct link that highlights the exact paragraph within the original PDF. This gives students confidence in the answer's accuracy and makes it easy to read deeper into the source material.
Collaborative Knowledge Pools
Allow members of the same GDG chapter or university department to contribute documents to a shared, permissioned library. A student querying the pool would benefit from the collective uploads of their entire cohort — past papers, diverse notes, and supplementary readings — rather than only their own materials.
Answer Confidence Indicators
Display a visual signal indicating how closely the retrieved chunks matched the question. Low-confidence retrievals would prompt the system to recommend uploading more relevant material rather than generating a potentially weak answer.
Study Session Analytics
Track which topics a student queries most frequently, identify knowledge gaps based on repeated question patterns, and generate a personalized study priority list ahead of examinations.

