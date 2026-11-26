import pytest

from core.models.analytics import AdvisoryReport, PerformanceMetrics
from core.settings.analytics_config import AnalyticsConfig
from core.analytics.ai_advisory import AdvisoryEngine


@pytest.fixture
def advisory_config():
    return AnalyticsConfig(
        enabled=True,
        db_path=":memory:",
        ai_advisory_enabled=True,
        ai_provider="openai",
        ai_model="gpt-4o"
    )

@pytest.fixture
def disabled_config():
    return AnalyticsConfig(
        enabled=True,
        db_path=":memory:",
        ai_advisory_enabled=False,
        ai_provider="openai",
        ai_model="gpt-4o"
    )

class TestAdvisoryEngine:
    def test_disabled_engine_returns_disclaimer(self, disabled_config):
        engine = AdvisoryEngine(disabled_config)
        report = engine.analyze_performance([], PerformanceMetrics(0, 0, 0, 0, 0, 0, 0))
        assert "disabled" in report.summary.lower()

    def test_insufficient_data_returns_disclaimer(self, advisory_config):
        engine = AdvisoryEngine(advisory_config)
        # Mock 5 entries
        report = engine.analyze_performance(["entry"] * 5, PerformanceMetrics(5, 0, 0, 0, 0, 0, 0))
        assert "Insufficient Data" in report.title
        
    def test_mock_advisory_report_format(self, advisory_config):
        engine = AdvisoryEngine(advisory_config)
        metrics = PerformanceMetrics(
            total_trades=10,
            win_rate=0.6,
            expectancy=1.5,
            profit_factor=2.0,
            total_pnl=15.0,
            max_drawdown=5.0,
            avg_rr=2.0
        )
        report = engine.analyze_performance(["entry"] * 10, metrics)
        
        assert "Performance Review" in report.title
        assert "ADVISORY ONLY" in report.disclaimer
        assert len(report.findings) >= 3
        # Ensure it notes the positive edge
        assert any("edge" in f.lower() for f in report.findings)

    def test_mock_advisory_report_negative_expectancy_warning(self, advisory_config):
        engine = AdvisoryEngine(advisory_config)
        metrics = PerformanceMetrics(
            total_trades=10,
            win_rate=0.2,
            expectancy=-1.5,  # Negative expectancy
            profit_factor=0.5,
            total_pnl=-15.0,
            max_drawdown=20.0,
            avg_rr=1.0
        )
        report = engine.analyze_performance(["entry"] * 10, metrics)
        
        # Ensure the negative expectancy warning is present
        assert any("negative" in f.lower() for f in report.findings)
