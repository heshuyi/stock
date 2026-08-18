"""Financial input validation and legacy settings compatibility."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models import Holding, Portfolio, UserSettings


VALID_WEIGHTS = {
    "HS300": 0.35,
    "ZZ500": 0.25,
    "CYB200": 0.15,
    "KCB50": 0.10,
    "SZ50": 0.15,
}


def test_financial_values_must_be_non_negative():
    with pytest.raises(ValidationError):
        UserSettings(base_amount=-1)
    with pytest.raises(ValidationError):
        Portfolio(cash=-1)
    with pytest.raises(ValidationError):
        Holding(symbol="HS300", shares=-1)
    with pytest.raises(ValidationError):
        Holding(symbol="HS300", cost_price=-1)


def test_settings_combination_errors_are_readable_chinese():
    with pytest.raises(ValidationError, match="短均线周期必须小于长均线周期"):
        UserSettings(ma_short=120, ma_long=60)
    with pytest.raises(ValidationError, match="估值武装线必须低于估值清仓线"):
        UserSettings(
            valuation_reduce_percentile=0.9,
            valuation_exit_percentile=0.8,
        )


def test_target_weights_require_known_complete_sum():
    UserSettings(target_weights=VALID_WEIGHTS)
    with pytest.raises(ValidationError, match="未知标的"):
        UserSettings(target_weights={**VALID_WEIGHTS, "UNKNOWN": 0})
    with pytest.raises(ValidationError, match="目标权重合计必须为 1"):
        UserSettings(target_weights={**VALID_WEIGHTS, "HS300": 0.34})


def test_legacy_profit_take_return_is_ignored():
    settings = UserSettings.model_validate(
        {"profit_take_return": 0.99, "target_weights": VALID_WEIGHTS}
    )
    assert "profit_take_return" not in settings.model_dump()


def test_growth_bear_policy_and_mult_bounds():
    settings = UserSettings()
    assert settings.growth_bear_policy == "hard_veto"
    assert settings.growth_bear_mult == 0.2
    UserSettings(growth_bear_policy="soft", growth_bear_mult=0.3)
    with pytest.raises(ValidationError):
        UserSettings(growth_bear_policy="soft", growth_bear_mult=0)
    with pytest.raises(ValidationError):
        UserSettings(growth_bear_policy="soft", growth_bear_mult=1)
    with pytest.raises(ValidationError):
        UserSettings(growth_bear_policy="aggressive")


def test_settings_api_returns_readable_422_detail():
    response = TestClient(app).put("/api/settings", json={"base_amount": -1})
    assert response.status_code == 422
    assert "月定投预算必须在" in response.text
