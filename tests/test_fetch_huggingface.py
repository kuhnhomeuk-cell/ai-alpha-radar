"""TDD for pipeline.fetch.huggingface against a cached HF Hub API fixture."""

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.fetch import huggingface
from pipeline.fetch.huggingface import HFModel

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hf_models_sample.json"


def _models() -> list[HFModel]:
    return huggingface.parse_models_response(json.loads(FIXTURE.read_text()))


def test_parse_models_response_returns_at_least_four_ai_models() -> None:
    """The fixture has 4 AI-relevant models + 1 non-AI (tabular-classification)."""
    models = _models()
    assert len(models) == 4
    ids = {m.id for m in models}
    assert "some-org/non-ai-thing" not in ids, "non-AI pipeline_tag should be filtered"


def test_models_have_required_fields() -> None:
    for m in _models():
        assert m.id
        assert m.downloads >= 0
        assert m.likes >= 0
        assert m.url.startswith("https://huggingface.co/")
        assert m.id in m.url


def test_models_default_to_warming_up() -> None:
    """Without a prior snapshot, every model ships warming_up=True."""
    for m in _models():
        assert m.warming_up is True
        assert m.downloads_7d_delta is None


def test_compute_download_velocity_annotates_known_models() -> None:
    today = _models()
    prior = {
        "meta-llama/Llama-3-70B": 12_000_000,  # +500K delta
        "Qwen/Qwen3-72B-Instruct": 3_000_000,   # +200K delta
    }
    annotated = huggingface.compute_download_velocity(today, prior_downloads=prior)
    by_id = {m.id: m for m in annotated}

    llama = by_id["meta-llama/Llama-3-70B"]
    assert llama.downloads_7d_delta == 500_000
    assert llama.warming_up is False

    qwen = by_id["Qwen/Qwen3-72B-Instruct"]
    assert qwen.downloads_7d_delta == 200_000
    assert qwen.warming_up is False

    # Models not in prior still warming up
    whisper = by_id["openai-community/whisper-large-v3"]
    assert whisper.warming_up is True
    assert whisper.downloads_7d_delta is None


def test_load_prior_download_map_missing_file_returns_empty(tmp_path: Path) -> None:
    assert huggingface.load_prior_download_map(tmp_path / "nonexistent.json") == {}


def test_load_prior_download_map_reads_meta(tmp_path: Path) -> None:
    snap = tmp_path / "2026-05-08.json"
    snap.write_text(json.dumps({
        "meta": {"hf_downloads": {"meta-llama/Llama-3-70B": 11_500_000}}
    }))
    result = huggingface.load_prior_download_map(snap)
    assert result == {"meta-llama/Llama-3-70B": 11_500_000}


def test_last_modified_parsed_when_present() -> None:
    models = _models()
    llama = next(m for m in models if m.id == "meta-llama/Llama-3-70B")
    assert llama.last_modified is not None
    assert llama.last_modified == datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
