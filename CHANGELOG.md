# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-03-22

### Added
- 12-stage CV processing pipeline (segmentation through Word output generation)
- LLM-powered hierarchical segmentation (Stage 1a) with Word document structure extraction
- Entry extraction with structured output parsing (Stage 2)
- Two-phase taxonomy classification: header mapping (3a) + entry classification (3b)
- 43 post-classification validator modules for taxonomy edge cases
- Structured field extraction with domain-specific parsers (Stage 4)
- Biosketch-style research summary generation (Stage 4.5)
- PubMed enrichment via NCBI E-utilities (Stage 5)
- Institution enrichment via ROR API (Stage 5b)
- Teaching entry reformatter (Stage 5c) and citation formatter (Stage 5d)
- WCM Word template generation with 20-section taxonomy support (Stage 6)
- Web interface with real-time pipeline progress viewer (WebSocket)
- File upload, run history, and output download in web UI
- Session-based authentication with configurable auth modes
- User consent tracking with versioned consent text
- Per-user rate limiting (daily and monthly run caps)
- Admin dashboard for user management, system config, and submission review
- Docker Compose setup for local development (MariaDB + FastAPI + Vite)
- S3 storage backend for production deployment
- CLI entry point (`run_full_pipeline.py`) with per-stage execution support
