import pytest
from ai_coding_assistant.tests.test_api import test_health, test_llm_health, test_chat_returns_model_reply, test_chat_passes_model_override, test_chat_rejects_invalid_requests, test_chat_maps_llm_errors_to_http_status

def test_health():
    """Test health endpoint."""
    assert test_health(client) == 200

def test_llm_health():
    """Test LLM health endpoint."""
    assert test_llm_health(client) == 200

def test_chat_returns_model_reply():
    """Test chat endpoint returns model reply."""
    assert test_chat_returns_model_reply(client, fake_llm) == 200

def test_chat_passes_model_override():
    """Test chat endpoint passes model override."""
    assert test_chat_passes_model_override(client, fake_llm) == 200

def test_chat_rejects_invalid_requests():
    """Test chat endpoint rejects invalid requests."""
    assert test_chat_rejects_invalid_requests(client, payload) == 422

def test_chat_maps_llm_errors_to_http_status():
    """Test chat endpoint maps LLM errors to HTTP status."""
    assert test_chat_maps_llm_errors_to_http_status(client, fake_llm, error, status_code) == status_code
