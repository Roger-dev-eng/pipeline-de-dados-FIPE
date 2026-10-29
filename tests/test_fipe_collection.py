import importlib
import sys
import types
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import Mock

import pytest
import requests


@pytest.fixture
def collection(monkeypatch):
    # Collection tests must never connect to PostgreSQL or read the real cache.
    monkeypatch.setenv("FIPE_CACHE_PATH", "unused-test-cache.json")
    database = types.ModuleType("app.db.engine")
    database.engine = Mock()
    # Patch only the database entry so pandas/NumPy stay loaded between tests.
    monkeypatch.setitem(sys.modules, "app.db.engine", database)
    module = importlib.import_module("app.pipeline.fipe_import")
    monkeypatch.setattr(module, "_save_cache", Mock())
    monkeypatch.setattr(module, "_cache", {})
    monkeypatch.setattr(module, "_MAX_WORKERS", 2)
    return module


def configure_api(module, monkeypatch, count):
    monkeypatch.setattr(module, "obter_marcas", lambda: [{"codigo": 1, "nome": "Toyota"}])
    monkeypatch.setattr(module, "obter_modelos", lambda code: [{"codigo": 2, "nome": "Corolla"}])
    monkeypatch.setattr(module, "obter_anos", lambda *args: list(range(count)))


def test_collects_past_400_and_stops_at_1000(collection, monkeypatch):
    configure_api(collection, monkeypatch, 1100)
    monkeypatch.setattr(collection, "_coletar_detalhe", lambda *args: {"codigo": args[-1]})
    events = []
    result = collection.coletar_dados_fipe(1000, events.append)
    assert len(result) == 1000
    assert events[-1]["event"] == "collect_limit"
    assert events[-1]["current"] == 1000
    collection._save_cache.assert_called_once()


def test_rate_limit_is_reported_without_retry_after_sleep(collection, monkeypatch):
    response = Mock(status_code=429, headers={"Retry-After": "3600"})
    session = Mock()
    session.get.return_value = response
    monkeypatch.setattr(collection, "_get_session", lambda: session)
    assert not collection.retry_strategy.is_retry("GET", 429, has_retry_after=True)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        collection.obter_anos(1, 2)
    session.get.assert_called_once()


def test_failure_after_400_propagates_instead_of_silent_stall(collection, monkeypatch):
    configure_api(collection, monkeypatch, 1000)
    def detail(*args):
        if args[-1] >= 400:
            raise RuntimeError("HTTP 429")
        return {"codigo": args[-1]}
    monkeypatch.setattr(collection, "_coletar_detalhe", detail)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        collection.coletar_dados_fipe(1000)
    collection._save_cache.assert_called_once()


def test_waiting_for_details_updates_panel(collection, monkeypatch):
    pending = Future()
    events = []
    monkeypatch.setattr(collection, "wait", Mock(return_value=(set(), {pending})))
    assert not collection._drain_futures([pending], [{}] * 400, 1000, events.append)
    assert events[0]["event"] == "api_wait"
    assert events[0]["current"] == 400


def test_waiting_for_metadata_updates_panel(collection, monkeypatch):
    events = []
    real_wait = collection.wait
    calls = 0
    def wait_once(futures, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return set(), set(futures)
        return real_wait(futures, **kwargs)
    monkeypatch.setattr(collection, "wait", wait_once)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = collection._await_api(executor, lambda: [1], (), [{}] * 400, 1000, events.append)
    assert result == [1]
    assert events[0]["event"] == "api_wait"
    assert events[0]["current"] == 400


def test_exhausted_source_reports_actual_count(collection, monkeypatch):
    configure_api(collection, monkeypatch, 5)
    monkeypatch.setattr(collection, "_coletar_detalhe", lambda *args: {"codigo": args[-1]})
    events = []
    assert len(collection.coletar_dados_fipe(1000, events.append)) == 5
    assert events[-1]["event"] == "collect_done"
    assert events[-1]["current"] == 5


def test_timeout_is_not_converted_to_empty_response(collection, monkeypatch):
    monkeypatch.setattr(collection, "_request_json", Mock(side_effect=requests.Timeout("timeout")))
    with pytest.raises(RuntimeError, match="timeout"):
        collection.obter_modelos(1)
