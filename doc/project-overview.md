# 🎓 University Admissions Advisor

## What It Is
A **Streamlit-based RAG chatbot** that answers questions about **UMD (University of Maryland)** and **FDU (Fairleigh Dickinson University)** admissions. It runs entirely locally — no internet needed after setup. Located at `D:\university_project_demo`.

## Tech Stack
| Component | Technology |
|-----------|------------|
| UI | Streamlit 1.59 |
| LLM | Qwen 2.5 7B (via Ollama) |
| Embeddings | nomic-embed-text (via Ollama) |
| Vector Store | ChromaDB (local, already built) |
| RAG | LangChain (langchain-classic chains) |
| PDF | PyPDFLoader (15 pages → 50 chunks, chunk_size=800, overlap=150) |
| Tunnel | Cloudflare Tunnel (optional public sharing) |

## Key Files
| File | Purpose |
|------|---------|
| `app.py` | Main Streamlit web UI (chat interface) |
| `admissions_bot.py` | Standalone CLI / interactive bot |
| `University_Projwect_v1.ipynb` | Original Colab notebook, adapted for Windows |
| `launch.bat` | One-click local launcher (health checks + start) |
| `launch_tunnel.bat` | One-click launcher with public Cloudflare tunnel |
| `launch_Guide.txt` | User-facing setup/run instructions |
| `PROJECT_REFERENCE.md` | Developer reference docs |
| `chroma_local_db/` | Pre-built ChromaDB vector store (already populated) |
| `content/sample_data/` | Source PDF + DOCX university profiles |

## How to Run
- **Local only:** Double-click `launch.bat` → opens `http://localhost:8501`
- **Public share:** Double-click `launch_tunnel.bat` → also gets a `trycloudflare.com` URL
- **CLI mode:** `python admissions_bot.py "your question"`
- **Interactive CLI:** `python admissions_bot.py` (type questions, `exit` to quit)

## Prerequisites
- Ollama running with `qwen2.5:7b` and `nomic-embed-text` models pulled
- Python 3.11+ with packages: `streamlit`, `langchain`, `langchain-ollama`, `langchain-text-splitters`, `langchain-community`, `langchain-classic`, `chromadb`, `pypdf`

## Known Issues & Fixes
1. `streamlit.exe` blocked by AppLocker → workaround: `python -m streamlit run app.py --server.headless true`
2. Streamlit email prompt hang → fixed with `--server.headless true` + `~/.streamlit/credentials.toml`
3. Cloudflared path quoting fragile → use bare `cloudflared` (in PATH)
4. Boot wait too short for tunnel → increased to 30s in `launch_tunnel.bat`
