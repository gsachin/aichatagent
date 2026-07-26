# 📋 Pending Improvements

**Last updated:** 2026-07-26

---

## 1. RAG Retrieval: "Compare UMD and FDU" returns no data

**Status:** ⚠️ Needs work

**Problem:** The PDF has Part C with a full side-by-side comparison table (chunk 46 in original indexing), but it ranks low in similarity search for the query "Compare UMD and FDU". The LLM sees only title-page chunks and correctly says "I don't have that information."

**Root cause:** The comparison table chunk doesn't contain the word "compare" — the semantic match is weak despite the content being exactly what the user needs.

**Possible fixes:**
- Hybrid search: combine BM25 keyword matching with semantic embeddings
- Query rewriting: expand "Compare UMD and FDU" → "UMD vs FDU comparison tuition admissions programs"
- Keyword boosting: if query contains "compare" or "vs" or "difference", boost Part C chunks
- Try a different embedding model (e.g., `mxbai-embed-large` via Ollama)
- Increase MMR `fetch_k` and adjust `lambda_mult` for more diversity

**How to test:** Retrieve top-10 chunks for "Compare UMD and FDU" and verify Part C comparison data appears in results.

---

## 2. UMD Tuition: Retrieves FDU data instead of UMD data

**Status:** ⚠️ Partially fixed — returns fee data but sometimes misattributes to FDU

**Problem:** The fee table in the PDF lists rates for both universities but the retrieval sometimes pulls the wrong university's data.

**Possible fixes:**
- Improve chunk metadata with university labels
- Add a metadata filter step post-retrieval

---

## 3. ONNX GPU Optimization

**Status:** ✅ Done — `onnxruntime-gpu` installed, ~2x speedup

**Potential further improvement:**
- Kokoro ONNX currently uses CUDAExecutionProvider, not TensorRT
- TensorRT could give additional 20-30% speedup but requires model conversion
- Low priority — current 2x speedup is adequate

---

## 4. TTS Caching

**Status:** ❌ Not implemented

**Problem:** Same text answer is re-synthesized every time, wasting GPU cycles.

**Fix:** Add `@st.cache_data` decorator to `text_to_audio_bytes()` to cache TTS results by text hash.

---

## 5. Voice UX Improvements

**Status:** ❌ Not implemented

- Add a visual indicator during TTS playback (waveform/progress bar)
- Add a "Stop speaking" button to cancel TTS mid-playback
- Pre-generate TTS for the greeting message on app load
