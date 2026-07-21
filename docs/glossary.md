# ATLAX Glossary

Version: 1.0  
Status: Active

## ATLAX

A deterministic trading operating system for market intelligence, alerting, execution, and analytics.

## CRT

Candle Range Theory. The first supported ATLAX strategy. Rules are defined only in `docs/rulebooks/CRT_RULEBOOK.md`.

## Detector

A module that identifies a documented market pattern and returns structured data. Detectors do not make trade decisions.

## Strategy Engine

The module that consumes detector outputs and produces `Trade Candidate`, `No Trade`, or `UNKNOWN`.

## Confidence Engine

The module that produces explainable quality scores from documented and configurable inputs.

## Risk Engine

The deterministic module that enforces risk limits and rejects invalid exposure.

## Execution Engine

The MT5-only module responsible for execution and trade management.

## MCP

Model Context Protocol. In ATLAX, a controlled gateway pattern for audited, least-privilege AI data access.

## UNKNOWN

The required result when behavior is not documented clearly enough to implement.
