"""
Deterministic Performance Calculators.
Authority: docs/12_ANALYTICS_ENGINE.md
"""
from typing import List
from decimal import Decimal

from core.models.analytics import JournalEntry, PerformanceMetrics


class MetricsCalculator:
    """Calculates deterministic statistics from a collection of JournalEntry records."""
    
    @staticmethod
    def calculate(entries: List[JournalEntry]) -> PerformanceMetrics:
        if not entries:
            return PerformanceMetrics(
                total_trades=0,
                win_rate=0.0,
                expectancy=0.0,
                profit_factor=0.0,
                total_pnl=0.0,
                max_drawdown=0.0,
                avg_rr=0.0
            )
            
        total_trades = len(entries)
        wins = 0
        gross_profit = Decimal("0")
        gross_loss = Decimal("0")
        
        # For average RR (Reward / Risk)
        total_rr = Decimal("0")
        rr_count = 0
        
        # For max drawdown
        peak = Decimal("0")
        current_cumulative = Decimal("0")
        max_dd = Decimal("0")
        
        for entry in entries:
            # PnL math
            current_cumulative += entry.pnl
            if current_cumulative > peak:
                peak = current_cumulative
            
            dd = peak - current_cumulative
            if dd > max_dd:
                max_dd = dd
            
            if entry.pnl > 0:
                wins += 1
                gross_profit += entry.pnl
            else:
                gross_loss += abs(entry.pnl)
                
            # RR math (Distance to TP / Distance to SL)
            # This is an approximation since we don't store the exact executed distance
            # in JournalEntry, but we can infer it from entry, sl, tp prices.
            entry_p = entry.entry_price
            sl_p = entry.sl_price
            tp_p = entry.tp_price
            if sl_p != entry_p:
                risk_dist = abs(entry_p - sl_p)
                reward_dist = abs(tp_p - entry_p)
                if risk_dist > 0:
                    rr = reward_dist / risk_dist
                    total_rr += rr
                    rr_count += 1
                    
        win_rate = float(wins / total_trades)
        total_pnl = float(gross_profit - gross_loss)
        expectancy = total_pnl / total_trades
        
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float(gross_profit)
        avg_rr = float(total_rr / rr_count) if rr_count > 0 else 0.0
        
        return PerformanceMetrics(
            total_trades=total_trades,
            win_rate=win_rate,
            expectancy=expectancy,
            profit_factor=profit_factor,
            total_pnl=total_pnl,
            max_drawdown=float(max_dd),
            avg_rr=avg_rr
        )
