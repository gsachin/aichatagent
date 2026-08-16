"""
Tests for the machine-adaptive deployment config
(app.hardware_profile + scripts/predeploy.py).

Tier selection and merge logic are pure functions — no real hardware
is probed. Drift tests monkeypatch detect_hardware.
"""

import json

import pytest

from app import hardware_profile as hp

# ── Fixtures ──────────────────────────────────────────────────────────

M3_MAX = dict(platform="apple_silicon", ram_gb=36.0, vram_gb=36.0, cpu_cores=14,
              gpu_name="Apple M3 Max", device="mps", os="darwin", arch="arm64",
              hostname="macbook", chip_tier="Apple M3 Max", is_container=False, cloud=False)

NVIDIA_6GB = dict(platform="nvidia", ram_gb=32.0, vram_gb=6.0, cpu_cores=8,
                  gpu_name="RTX 3060", device="cuda", os="windows", arch="AMD64",
                  hostname="pc", chip_tier="RTX 3060", is_container=False, cloud=False)

APPLE_8GB = dict(platform="apple_silicon", ram_gb=8.0, vram_gb=8.0, cpu_cores=8,
                 gpu_name="Apple M1", device="mps", os="darwin", arch="arm64",
                 hostname="mini", chip_tier="Apple M1", is_container=False, cloud=False)

CPU_ONLY = dict(platform="cpu", ram_gb=16.0, vram_gb=None, cpu_cores=4,
                gpu_name="", device="cpu", os="linux", arch="x86_64",
                hostname="box", chip_tier="", is_container=False, cloud=False)

CLOUD = dict(platform="cloud_linux", ram_gb=8.0, vram_gb=None, cpu_cores=2,
             gpu_name="", device="cpu", os="linux", arch="x86_64",
             hostname="pod", chip_tier="", is_container=True, cloud=True)

SAMPLE_ENV = """\
# Twilio (voice + WhatsApp)
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=secret123
TWILIO_PHONE_NUMBER=+19788198953

DATABASE_URL=postgresql://elearning:elearning_secret@localhost:5432/admissions

OLLAMA_MODEL=qwen2.5:7b-instruct-q3_K_M
OLLAMA_NUM_CTX=2048
"""


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text(SAMPLE_ENV, encoding="utf-8")
    return p


# ── Tier selection ────────────────────────────────────────────────────

def test_tier_m3max_36gb():
    t = hp.select_tier(M3_MAX)
    assert t["tier_id"] == "apple_high"
    assert t["values"]["LLM_PROVIDER"] == "mlx"
    assert "14B" in t["values"]["MLX_MODEL"]
    assert t["values"]["OLLAMA_NUM_CTX"] == "8192"
    assert t["values"]["WHISPER_NUM_THREADS"] == "8"   # min(8, 14 cores)
    assert t["values"]["FASTAPI_WORKERS"] == "4"
    assert t["warn"] is None


def test_tier_nvidia_6gb():
    t = hp.select_tier(NVIDIA_6GB)
    assert t["tier_id"] == "nvidia_mid"
    assert t["values"]["LLM_PROVIDER"] == "ollama"
    assert t["values"]["OLLAMA_MODEL"] == "qwen2.5:7b-instruct-q3_K_M"
    assert t["values"]["OLLAMA_NUM_CTX"] == "4096"
    assert t["values"]["FASTAPI_WORKERS"] == "2"


def test_tier_apple_8gb():
    t = hp.select_tier(APPLE_8GB)
    assert t["tier_id"] == "apple_low"
    assert "7B" in t["values"]["MLX_MODEL"]
    assert t["values"]["OLLAMA_NUM_CTX"] == "4096"
    assert t["values"]["WHISPER_MODEL"] == "base.en"
    assert t["warn"]


def test_tier_cpu_only():
    t = hp.select_tier(CPU_ONLY)
    assert t["tier_id"] == "cpu_local"
    assert t["values"]["LLM_PROVIDER"] == "ollama"
    assert t["values"]["WHISPER_NUM_THREADS"] == "2"   # cores-2 → max(2, 4-2)=2
    assert t["warn"]


def test_tier_cloud_container():
    t = hp.select_tier(CLOUD)
    assert t["tier_id"] == "cloud_linux"
    assert t["values"]["FASTAPI_WORKERS"] == "2"
    assert t["warn"]


def test_tier_covers_all_managed_keys():
    for hw in (M3_MAX, NVIDIA_6GB, APPLE_8GB, CPU_ONLY, CLOUD):
        t = hp.select_tier(hw)
        assert set(hp.MANAGED_KEYS) == set(t["values"]), hw["platform"]
        assert all(isinstance(v, str) for v in t["values"].values())


# ── Merge ─────────────────────────────────────────────────────────────

def test_merge_preserves_twilio_db(env_file):
    tier = hp.select_tier(NVIDIA_6GB)
    before = env_file.read_text(encoding="utf-8")
    result = hp.apply_profile(env_file, tier)
    after = env_file.read_text(encoding="utf-8")
    for needle in ("TWILIO_ACCOUNT_SID=ACxxxxxxxx", "TWILIO_AUTH_TOKEN=secret123",
                   "DATABASE_URL=postgresql://elearning"):
        assert needle in after


def test_merge_first_run_adopts_existing_keys(env_file):
    tier = hp.select_tier(NVIDIA_6GB)
    result = hp.apply_profile(env_file, tier, snapshot=None)
    # Template-copied keys exist outside the block → adopted, not manual-skip
    assert result["actions"]["OLLAMA_MODEL"] == "adopted"
    assert result["actions"]["OLLAMA_NUM_CTX"] == "adopted"
    after = env_file.read_text(encoding="utf-8")
    assert hp.PROFILE_MARKER_OPEN in after
    # OLLAMA_NUM_CTX sized 2048 → 4096 inside the block
    assert "OLLAMA_NUM_CTX=4096" in after
    # No duplicate definitions remain outside the block
    body = after.split(hp.PROFILE_MARKER_OPEN)[0]
    assert "OLLAMA_NUM_CTX=2048" not in body
    assert body.count("OLLAMA_MODEL=") == 0


def test_merge_preserves_provider_auto(env_file):
    env_file.write_text("LLM_PROVIDER=auto\nTWILIO_ACCOUNT_SID=ACx\n", encoding="utf-8")
    tier = hp.select_tier(M3_MAX)
    result = hp.apply_profile(env_file, tier, snapshot=None)
    assert result["applied"]["LLM_PROVIDER"] == "auto"


def test_merge_manual_override_wins(env_file):
    tier = hp.select_tier(NVIDIA_6GB)
    # First run creates the block; snapshot exists from here on.
    hp.apply_profile(env_file, tier, snapshot=None)
    snapshot = {"detected": {"platform": "nvidia", "ram_gb": 32.0}}
    # User overrides OLLAMA_NUM_CTX outside the block.
    text = env_file.read_text(encoding="utf-8")
    text = text.replace("OLLAMA_NUM_CTX=4096\n", "")
    env_file.write_text("OLLAMA_NUM_CTX=6144\n" + text, encoding="utf-8")
    result = hp.apply_profile(env_file, tier, snapshot=snapshot)
    assert result["actions"]["OLLAMA_NUM_CTX"] == "manual-skip"
    after = env_file.read_text(encoding="utf-8")
    assert "OLLAMA_NUM_CTX=6144" in after
    assert hp.PROFILE_MARKER_OPEN in after
    # Single definition: block copy removed
    assert after.count("OLLAMA_NUM_CTX=") == 1


def test_merge_idempotent(env_file):
    tier = hp.select_tier(NVIDIA_6GB)
    hp.apply_profile(env_file, tier, snapshot=None)
    after_first = env_file.read_text(encoding="utf-8")
    result2 = hp.apply_profile(env_file, tier, snapshot={"detected": {}})
    assert result2["changed"] is False
    assert env_file.read_text(encoding="utf-8") == after_first


def test_merge_dry_run_no_write(env_file):
    tier = hp.select_tier(NVIDIA_6GB)
    before = env_file.read_text(encoding="utf-8")
    result = hp.apply_profile(env_file, tier, dry_run=True, snapshot=None)
    assert result["changed"] is True
    assert env_file.read_text(encoding="utf-8") == before


def test_merge_appends_block_when_absent(env_file):
    env_file.write_text("TWILIO_ACCOUNT_SID=ACx\n", encoding="utf-8")
    tier = hp.select_tier(CPU_ONLY)
    hp.apply_profile(env_file, tier, snapshot=None)
    after = env_file.read_text(encoding="utf-8")
    assert after.splitlines()[0] == "TWILIO_ACCOUNT_SID=ACx"
    assert hp.PROFILE_MARKER_CLOSE in after
    assert "RAG_TOP_K=4" in after  # cpu_local tier value


# ── Snapshot / drift ──────────────────────────────────────────────────

def _write_snapshot(env_file, detected, tier_id="apple_high"):
    snap = {"tier_id": tier_id, "detected": detected, "applied": {}}
    (env_file.parent / hp.SNAPSHOT_FILENAME).write_text(json.dumps(snap), encoding="utf-8")


def test_drift_ok(env_file, monkeypatch):
    _write_snapshot(env_file, M3_MAX)
    monkeypatch.setattr(hp, "detect_hardware", lambda: dict(M3_MAX))
    drift = hp.check_drift(env_file)
    assert drift["state"] == "ok"


def test_drift_detected(env_file, monkeypatch):
    _write_snapshot(env_file, M3_MAX)
    live = dict(M3_MAX, ram_gb=16.0, cpu_cores=4)
    monkeypatch.setattr(hp, "detect_hardware", lambda: live)
    drift = hp.check_drift(env_file)
    assert drift["state"] == "drifted"
    keys = {d["key"] for d in drift["diffs"]}
    assert "ram_gb" in keys and "cpu_cores" in keys


def test_drift_within_tolerance(env_file, monkeypatch):
    _write_snapshot(env_file, M3_MAX)
    live = dict(M3_MAX, ram_gb=36.0 + 1.9)  # within ±2 GB
    monkeypatch.setattr(hp, "detect_hardware", lambda: live)
    assert hp.check_drift(env_file)["state"] == "ok"


def test_drift_no_snapshot(env_file):
    assert hp.check_drift(env_file)["state"] == "no_profile"


def test_drift_skipped_container(env_file, monkeypatch):
    _write_snapshot(env_file, M3_MAX)
    live = dict(CLOUD, is_container=True)
    monkeypatch.setattr(hp, "detect_hardware", lambda: live)
    assert hp.check_drift(env_file)["state"] == "skipped_container"


def test_drift_disabled(env_file, monkeypatch):
    monkeypatch.setenv("MACHINE_PROFILE_CHECK", "0")
    assert hp.check_drift(env_file)["state"] == "disabled"
