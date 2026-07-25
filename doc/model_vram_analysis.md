# LLM Model VRAM Analysis — 6 GB NVIDIA GPU

**Date:** 2026-07-25
**Pipeline:** VAD → Whisper STT → ChromaDB RAG → Qwen LLM → Kokoro TTS
**GPU Budget:** 6.00 GB

---

## Fixed VRAM Costs (no LLM)

| Component | VRAM |
|-----------|------|
| CUDA Base Overhead | 0.50 GB |
| Faster-Whisper small.en INT8 | 0.80 GB |
| Kokoro-82M ONNX | 0.35 GB |
| **Fixed overhead** | **1.65 GB** |

**Remaining for LLM:** 4.35 GB (with no headroom: 6.00 - 1.65)

---

## Qwen 2.5 7B Quantization Options

All variants are 7.6B parameters, instruction-tuned. Disk size = compressed GGUF file. Runtime VRAM = weights decompressed + KV cache + Ollama overhead.

| Tag | Ollama Size | Weights (VRAM) | KV Cache (ctx=2048) | Ollama OH | **Total LLM VRAM** | **Peak (all 3 loaded)** | Verdict |
|-----|------------|----------------|---------------------|-----------|-------------------|------------------------|---------|
| `qwen2.5:7b-instruct-q2_K` | 3.0 GB | ~2.38 GB | 0.12 GB | 0.30 GB | **2.80 GB** | **4.45 GB (74%)** | ✅ Safe |
| `qwen2.5:7b-instruct-q3_K_S` | 3.5 GB | ~2.68 GB | 0.12 GB | 0.30 GB | **3.10 GB** | **4.75 GB (79%)** | ✅ Safe |
| `qwen2.5:7b-instruct-q3_K_M` | 3.8 GB | ~3.04 GB | 0.12 GB | 0.30 GB | **3.46 GB** | **5.11 GB (85%)** | ✅ Recommended |
| `qwen2.5:7b-instruct-q3_K_L` | 4.1 GB | ~3.40 GB | 0.12 GB | 0.30 GB | **3.82 GB** | **5.47 GB (91%)** | ⚠️ Tight |
| `qwen2.5:7b-instruct-q4_0` | 4.4 GB | ~3.61 GB | 0.12 GB | 0.30 GB | **4.03 GB** | **5.68 GB (95%)** | ⚠️ Tight |
| `qwen2.5:7b-instruct-q4_K_S` | 4.5 GB | ~3.80 GB | 0.12 GB | 0.30 GB | **4.22 GB** | **5.87 GB (98%)** | ⚠️ Very tight |
| `qwen2.5:7b-instruct` (q4_K_M) | 4.7 GB | ~4.28 GB | 0.12 GB | 0.30 GB | **4.70 GB** | **6.34 GB (106%)** | ❌ Overbudget |

---

## Sequential Pipeline Mitigation

If Whisper is unloaded before TTS loads (sequential pipeline), peak VRAM drops:

| Phase | Components | q3_K_M | q4_K_M (current) |
|-------|-----------|--------|-------------------|
| STT phase | CUDA + Whisper + LLM | 4.31 GB (72%) | 5.50 GB (92%) |
| TTS phase | CUDA + Kokoro + LLM | 3.86 GB (64%) | 5.05 GB (84%) |

Even with sequential loading, the current q4_K_M is very tight at 92%.

---

## Model Quality vs Size Tradeoff

| Quant | Bits/Param | Quality | Best For |
|-------|-----------|---------|----------|
| Q2_K | ~2.5 | Noticeable loss, acceptable for simple Q&A | Tightest budget |
| Q3_K_S | ~2.8 | Minor loss | Budget-constrained |
| **Q3_K_M** | **~3.2** | **Good — sweet spot** | **6 GB GPU + Whisper + Kokoro** |
| Q3_K_L | ~3.6 | Very good | When you can spare 5.5 GB |
| Q4_K_S | ~4.0 | Excellent | Standalone LLM (no other models) |
| Q4_K_M | ~4.5 | Best 4-bit quality | 8+ GB GPU |

---

## KV Cache Warning

Ollama's default context window is **32768 tokens**. At this length, the KV cache grows from **0.12 GB → 1.88 GB**, making ALL variants crash with OOM on a 6 GB GPU.

**Must set `num_ctx: 2048`** in the pipeline configuration. This is already configured in `app/pipeline.py`:

```python
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "2048"))
```

---

## Recommendation

**Primary:** `qwen2.5:7b-instruct-q3_K_M` (3.8 GB disk, ~3.46 GB VRAM)
- Fits all 3 models at 85% peak utilization
- Good quality for university admissions Q&A
- Safe headroom for OS/driver CUDA allocations

**Fallback:** `qwen2.5:7b-instruct-q3_K_S` (3.5 GB disk, ~3.10 GB VRAM)
- 79% peak — very safe, slightly lower quality

**Pull command:**
```bash
ollama pull qwen2.5:7b-instruct-q3_K_M
```

**Update pipeline default:**
```python
# In app/pipeline.py
DEFAULT_LLM_MODEL = "qwen2.5:7b-instruct-q3_K_M"
```

---

## Installed Models (as of 2026-07-25)

| Model | Size | Quant | VRAM Fit |
|-------|------|-------|----------|
| `qwen2.5:7b` | 4.7 GB | Q4_K_M | ❌ |
| `qwen2.5:7b-instruct` | 4.7 GB | Q4_K_M | ❌ |
| `phi3:mini` | 2.2 GB | — | ✅ (but not Qwen) |
| `nomic-embed-text:latest` | 0.3 GB | — | ✅ (embeddings only) |
