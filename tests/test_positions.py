"""Tests for scanner.positions — uses tmp_path to avoid real file writes."""
import json
import os
import pytest

import scanner.config as cfg
import scanner.state as st
from scanner.positions import load_positions, save_positions, add_positions


@pytest.fixture(autouse=True)
def patch_file_paths(tmp_path, monkeypatch):
    """Redirect positions file + filelock to a temp directory."""
    pos_file  = str(tmp_path / "positions.json")
    lock_file = pos_file + ".lock"

    monkeypatch.setattr(cfg, "POSITIONS_FILE", pos_file)

    from filelock import FileLock
    new_flock = FileLock(lock_file, timeout=5)
    monkeypatch.setattr(st, "_POSITIONS_FLOCK", new_flock)

    # Also patch the attribute used inside positions module
    import scanner.positions as pm
    monkeypatch.setattr(pm, "POSITIONS_FILE", pos_file)
    monkeypatch.setattr(pm, "_POSITIONS_FLOCK", new_flock)
    yield pos_file


class TestLoadPositions:
    def test_returns_empty_list_when_no_file(self, patch_file_paths):
        assert load_positions() == []

    def test_returns_empty_list_on_corrupt_json(self, patch_file_paths):
        with open(patch_file_paths, "w") as f:
            f.write("{{invalid")
        result = load_positions()
        assert result == []


class TestSaveLoad:
    def test_round_trip(self, patch_file_paths):
        data = [{"ticker": "005930", "name": "삼성전자", "entry": 70000}]
        save_positions(data)
        loaded = load_positions()
        assert loaded == data

    def test_saves_multiple_positions(self, patch_file_paths):
        data = [
            {"ticker": "005930", "name": "삼성전자", "entry": 70000},
            {"ticker": "000660", "name": "SK하이닉스", "entry": 150000},
        ]
        save_positions(data)
        assert len(load_positions()) == 2


class TestAddPositions:
    def _make_stock(self, ticker="005930", name="삼성전자", score=50):
        return {
            "ticker": ticker, "name": name, "entry": 70000,
            "tp": 74900, "sl": 68000, "sector": "IT",
            "signal_score": score, "bo_lookback": 1, "pullback_depth": 2.0,
            "quantity": 10, "auto_traded": False,
        }

    def test_adds_new_stock(self, patch_file_paths):
        add_positions([self._make_stock()])
        pos = load_positions()
        assert len(pos) == 1
        assert pos[0]["ticker"] == "005930"

    def test_no_duplicate_tickers(self, patch_file_paths):
        add_positions([self._make_stock()])
        add_positions([self._make_stock()])   # same ticker again
        assert len(load_positions()) == 1

    def test_respects_max_positions(self, patch_file_paths, monkeypatch):
        import scanner.positions as pm
        monkeypatch.setitem(pm.STRATEGY, "max_positions", 2)
        stocks = [
            self._make_stock(ticker=f"00593{i}", name=f"Stock{i}")
            for i in range(3)
        ]
        add_positions(stocks)
        assert len(load_positions()) == 2

    def test_higher_score_added_first(self, patch_file_paths, monkeypatch):
        import scanner.positions as pm
        monkeypatch.setitem(pm.STRATEGY, "max_positions", 1)
        stocks = [
            self._make_stock(ticker="AAA", name="LowScore",  score=40),
            self._make_stock(ticker="BBB", name="HighScore", score=90),
        ]
        add_positions(stocks)
        pos = load_positions()
        assert pos[0]["ticker"] == "BBB"
