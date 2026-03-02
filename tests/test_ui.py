"""Tests for ANSI color helpers and emoji constants."""

from __future__ import annotations

import gm.ui as ui


class TestColorDisabled:
    """When _COLOR is False (non-TTY, e.g. pytest), helpers return plain text."""

    def test_color_flag_is_false_in_tests(self) -> None:
        assert ui._COLOR is False

    def test_bold_passthrough(self) -> None:
        assert ui.bold("hello") == "hello"

    def test_dim_passthrough(self) -> None:
        assert ui.dim("hello") == "hello"

    def test_cyan_passthrough(self) -> None:
        assert ui.cyan("hello") == "hello"

    def test_green_passthrough(self) -> None:
        assert ui.green("hello") == "hello"

    def test_yellow_passthrough(self) -> None:
        assert ui.yellow("hello") == "hello"

    def test_red_passthrough(self) -> None:
        assert ui.red("hello") == "hello"

    def test_bold_cyan_passthrough(self) -> None:
        assert ui.bold_cyan("hello") == "hello"

    def test_bold_green_passthrough(self) -> None:
        assert ui.bold_green("hello") == "hello"

    def test_bold_yellow_passthrough(self) -> None:
        assert ui.bold_yellow("hello") == "hello"

    def test_bold_red_passthrough(self) -> None:
        assert ui.bold_red("hello") == "hello"

    def test_emoji_constants_are_empty(self) -> None:
        assert ui.E_MUSIC == ""
        assert ui.E_CHECK == ""
        assert ui.E_DONE == ""
        assert ui.E_SKIP == ""
        assert ui.E_WARN == ""
        assert ui.E_ERROR == ""
        assert ui.E_SEARCH == ""
        assert ui.E_WRITE == ""
        assert ui.E_SEND == ""
        assert ui.E_FOLDER == ""
        assert ui.E_LINK == ""
        assert ui.E_SCISSORS == ""
        assert ui.E_BROOM == ""


class TestColorEnabled:
    """When _COLOR is True (monkeypatched), helpers wrap text in ANSI codes."""

    def test_bold_wraps(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.bold("hi")
            assert result.startswith("\033[1m")
            assert result.endswith("\033[0m")
            assert "hi" in result
        finally:
            mp.undo()

    def test_dim_wraps(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.dim("hi")
            assert "\033[2m" in result
            assert "hi" in result
        finally:
            mp.undo()

    def test_cyan_wraps(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.cyan("hi")
            assert "\033[36m" in result
            assert "hi" in result
        finally:
            mp.undo()

    def test_green_wraps(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.green("hi")
            assert "\033[32m" in result
        finally:
            mp.undo()

    def test_yellow_wraps(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.yellow("hi")
            assert "\033[33m" in result
        finally:
            mp.undo()

    def test_red_wraps(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.red("hi")
            assert "\033[31m" in result
        finally:
            mp.undo()

    def test_bold_cyan_combines(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.bold_cyan("hi")
            assert "\033[1m" in result
            assert "\033[36m" in result
        finally:
            mp.undo()

    def test_bold_green_combines(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.bold_green("hi")
            assert "\033[1m" in result
            assert "\033[32m" in result
        finally:
            mp.undo()

    def test_bold_yellow_combines(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.bold_yellow("hi")
            assert "\033[1m" in result
            assert "\033[33m" in result
        finally:
            mp.undo()

    def test_bold_red_combines(self, monkeypatch: object) -> None:
        import pytest
        mp = pytest.MonkeyPatch()
        mp.setattr(ui, "_COLOR", True)
        try:
            result = ui.bold_red("hi")
            assert "\033[1m" in result
            assert "\033[31m" in result
        finally:
            mp.undo()
