"""
Analytics Models.
Authority: docs/12_ANALYTICS_ENGINE.md
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

@dataclass(frozen=True)
class JournalEntry:
    """Represents a completed trade lifecycle in the journal."""
    record_id: str
    candidate_id: str
    symbol: str
    timeframe: str
    profile: str
    strategy_name: str
    direction: str  # BUY or SELL
    
    # Pre-trade data
    confidence_score: float
    confidence_breakdown: Dict[str, Any]
    
    # Execution data
    lot_size: Decimal
    entry_price: Decimal
    sl_price: Decimal
    tp_price: Decimal
    
    # Outcome data
    exit_price: Decimal
    pnl: Decimal
    closed_at: datetime
    
    # Was it manually closed, hit SL, hit TP, etc.
    exit_reason: str
    
    # Store arbitrary extra tags
    tags: List[str] = field(default_factory=list)

@dataclass(frozen=True)
class PerformanceMetrics:
    """Calculated deterministic performance metrics."""
    total_trades: int
    win_rate: float        # 0.0 to 1.0
    expectancy: float      # Average R-multiple or average PnL per trade
    profit_factor: float   # Gross profit / Gross loss
    total_pnl: float
    max_drawdown: float    # Dollar amount or percentage
    avg_rr: float          # Average Reward/Risk ratio

@dataclass(frozen=True)
class AdvisoryReport:
    """Output from the AI Advisory Engine."""
    title: str
    summary: str
    findings: List[str]
    sample_size: int
    disclaimer: str = "ADVISORY ONLY. Do not use for live execution decisions."
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
