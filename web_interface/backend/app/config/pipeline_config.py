"""
Pipeline Configuration
Manages feature flags for pipeline execution strategy.

Phase 1: USE_LEGACY_PIPELINE = True (production, proven code)
Phase 2: ENABLE_PARALLEL_VALIDATION = True (both pipelines run, compare outputs)
Phase 3: Gradual migration (increase UNIFIED_PIPELINE_PERCENTAGE)
"""

class PipelineConfig:
    """
    Feature flags for pipeline execution.

    Change these flags to control which pipeline system is used.
    """

    # ==========================================================================
    # PHASE 1: LEGACY PIPELINE (Immediate Fix)
    # ==========================================================================

    # Use legacy production scripts (77 extractors + enrichment + populate_cv.py)
    # Set to True to use proven pre-Oct-31 system
    USE_LEGACY_PIPELINE = True

    # ==========================================================================
    # PHASE 2: PARALLEL VALIDATION (Future - Don't Enable Yet)
    # ==========================================================================

    # Run BOTH pipelines on every CV and compare outputs
    # Enables quality validation before migration
    # WARNING: Doubles processing time and cost!
    ENABLE_PARALLEL_VALIDATION = False

    # Save comparison reports to filesystem
    SAVE_COMPARISON_REPORTS = False

    # ==========================================================================
    # PHASE 3: GRADUAL MIGRATION (Future - After Validation)
    # ==========================================================================

    # Percentage of CVs to process with unified pipeline (0-100)
    # Only used when USE_LEGACY_PIPELINE = False
    # 0 = All legacy, 100 = All unified
    UNIFIED_PIPELINE_PERCENTAGE = 0

    # ==========================================================================
    # EMERGENCY CONTROLS
    # ==========================================================================

    # Force legacy pipeline regardless of other settings
    # Use this for instant rollback if unified pipeline has issues
    FORCE_LEGACY_PIPELINE = False

    # Disable all LLM calls (use cached/mock data only)
    # For testing/development without API costs
    DISABLE_LLM_CALLS = False

    # ==========================================================================
    # LEGACY PIPELINE SETTINGS
    # ==========================================================================

    # Run PubMed enrichment (critical for publication subsection accuracy)
    ENABLE_PUBMED_ENRICHMENT = True

    # Legacy flag, unused: institution enrichment is LLM-based now, not ROR
    ENABLE_ROR_ENRICHMENT = True

    # Maximum number of sections to extract in parallel
    # Higher = faster but more memory usage
    MAX_PARALLEL_EXTRACTORS = 10

    # Timeout per extractor (seconds)
    EXTRACTOR_TIMEOUT = 300  # 5 minutes

    # ==========================================================================
    # COMPARISON SETTINGS (for parallel validation)
    # ==========================================================================

    # Acceptable difference percentage for validation
    # If unified output differs by > this amount, flag for review
    ACCEPTABLE_VARIANCE_PERCENT = 5.0

    # Sections that must match exactly (no variance allowed)
    EXACT_MATCH_SECTIONS = [
        "personal_data",
        "contact_information"
    ]

    @classmethod
    def get_active_pipeline(cls) -> str:
        """
        Determine which pipeline should be used.

        Returns:
            "legacy", "unified", or "parallel"
        """
        if cls.FORCE_LEGACY_PIPELINE:
            return "legacy"

        if cls.ENABLE_PARALLEL_VALIDATION:
            return "parallel"

        if cls.USE_LEGACY_PIPELINE:
            return "legacy"

        return "unified"

    @classmethod
    def should_use_legacy_for_run(cls, run_id: str) -> bool:
        """
        Determine if a specific run should use legacy pipeline.

        For gradual rollout, uses hash of run_id to consistently assign
        same CV to same pipeline across retries.

        Args:
            run_id: Unique identifier for this run

        Returns:
            True if should use legacy, False if should use unified
        """
        if cls.FORCE_LEGACY_PIPELINE or cls.USE_LEGACY_PIPELINE:
            return True

        if cls.ENABLE_PARALLEL_VALIDATION:
            return True  # Parallel mode runs both

        # Gradual rollout: use hash to deterministically assign pipeline
        if cls.UNIFIED_PIPELINE_PERCENTAGE == 0:
            return True
        elif cls.UNIFIED_PIPELINE_PERCENTAGE == 100:
            return False
        else:
            # Use hash of run_id for consistent assignment
            import hashlib
            hash_val = int(hashlib.md5(run_id.encode()).hexdigest(), 16)
            threshold = cls.UNIFIED_PIPELINE_PERCENTAGE / 100.0
            return (hash_val % 100) / 100.0 >= threshold


# Export singleton instance
config = PipelineConfig()
