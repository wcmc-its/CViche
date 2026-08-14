"""Registry of all pipeline steps - aligned with run_full_pipeline.py (V15)."""
from typing import List
from dataclasses import dataclass


@dataclass
class StepDefinition:
    """Definition of a pipeline step."""
    number: int
    stage_id: str  # e.g., '1a', '1b', '2', '3a', '3b', '4', '4.5', '5', '5b', '5c', '5d', '6'
    name: str
    description: str
    uses_llm: bool = False
    uses_api: bool = False  # External API like PubMed, ROR
    weight: float = 1.0  # Relative processing time weight for progress calculation
    estimated_seconds: int = 30  # Estimated duration in seconds for time-based progress


# Define all 12 steps - matches run_full_pipeline.py exactly
# Weights and estimated_seconds based on empirical observation (~8 min total processing)
# Total weight = 100, distributed proportionally to typical duration
STEP_REGISTRY: List[StepDefinition] = [
    StepDefinition(
        number=1,
        stage_id='1a',
        name="Hierarchy Extraction",
        description="LLM-powered hierarchical segmentation. Extracts section headers and structure from CV document using GPT. "
                    "Identifies all sections (Education, Publications, Grants, etc.) and their hierarchical relationships.",
        uses_llm=True,
        weight=15.0,  # Major LLM stage - ~15% of total time
        estimated_seconds=70
    ),
    StepDefinition(
        number=2,
        stage_id='1b',
        name="Hierarchy Mapping",
        description="Maps headers to element indices (no LLM). Creates index mappings between extracted headers "
                    "and Word document paragraph indices. Handles synthetic headers and creates 'Personal Data' preamble.",
        uses_llm=False,
        weight=2.0,  # Fast non-LLM stage
        estimated_seconds=10
    ),
    StepDefinition(
        number=3,
        stage_id='2',
        name="Entry Extraction",
        description="Extracts individual entries from each section. Identifies entry boundaries within sections "
                    "(publications, positions, grants, etc.) achieving 100% document coverage. Entry types: paragraph, table, table_row, header, break.",
        uses_llm=False,
        weight=3.0,  # Fast extraction
        estimated_seconds=15
    ),
    StepDefinition(
        number=4,
        stage_id='3a',
        name="Header Taxonomy Mapping",
        description="Maps CV section headers to WCM taxonomy codes using LLM. Assigns codes like S1 (Peer-Reviewed Articles), "
                    "B1 (Education), M2A (Active Grants) with confidence weights. Supports multi-code mappings for ambiguous headers.",
        uses_llm=True,
        weight=10.0,  # LLM taxonomy mapping
        estimated_seconds=50
    ),
    StepDefinition(
        number=5,
        stage_id='3b',
        name="Entry Classification",
        description="Classifies individual entries to taxonomy codes using header context + entry content. "
                    "Includes 10-step post-classification correction pipeline for systematic error correction. "
                    "T-validation gate reclassifies uncertain entries.",
        uses_llm=True,
        weight=12.0,  # LLM classification with corrections
        estimated_seconds=55
    ),
    StepDefinition(
        number=6,
        stage_id='4',
        name="Field Extraction",
        description="Extracts structured fields from classified entries. Publications: authors, titles, journals, DOIs, PMIDs. "
                    "Grants: grant numbers, PI roles, agencies, dates, funding amounts. "
                    "Education: degrees, institutions, years. Also infers CV owner's current location from employment/education history "
                    "for downstream geographic scope classification. Uses two-tier LLM strategy (cheap model + retry).",
        uses_llm=True,
        weight=20.0,  # Largest stage - many entries to extract
        estimated_seconds=100
    ),
    StepDefinition(
        number=7,
        stage_id='4.5',
        name="Research Summary",
        description="Generates biosketch-style M1 research summary statement. Analyzes CV content to produce "
                    "a narrative summarizing research activities and contributions. Uses existing M1 content if quality score >= 0.8.",
        uses_llm=True,
        weight=8.0,  # Single LLM call for summary
        estimated_seconds=40
    ),
    StepDefinition(
        number=8,
        stage_id='5',
        name="PubMed Enrichment",
        description="Enriches publications with PubMed metadata via NCBI API. Adds full author lists, MeSH terms, "
                    "publication types, abstracts, PMCIDs. Handles PMID, PMCID, and DOI lookups.",
        uses_llm=False,
        uses_api=True,
        weight=10.0,  # API calls can be slow
        estimated_seconds=50
    ),
    StepDefinition(
        number=9,
        stage_id='5b',
        name="Institution Enrichment",
        description="Adds institution location data (city, state, country) via batched LLM lookups, "
                    "using CV owner context for disambiguation. "
                    "Applies to education (B1, B2), training (C, C1, C2), and position (D1, D2, D3) entries.",
        uses_llm=True,  # calls llm_client.call_llm; the ROR API is retired (#523)
        uses_api=False,
        weight=5.0,  # Fewer calls than PubMed
        estimated_seconds=25
    ),
    StepDefinition(
        number=10,
        stage_id='5c',
        name="Teaching Formatter",
        description="LLM-reformats K-code (teaching) entries for readability. Standardizes teaching activity descriptions "
                    "into consistent format with course names, roles, dates, and institutions.",
        uses_llm=True,
        weight=5.0,  # Usually few teaching entries
        estimated_seconds=25
    ),
    StepDefinition(
        number=11,
        stage_id='5d',
        name="Citation Formatter",
        description="LLM-reformats non-enriched citations to Vancouver format. Standardizes publication citations "
                    "that couldn't be enriched via PubMed into consistent bibliographic format.",
        uses_llm=True,
        weight=7.0,  # Variable depending on PubMed coverage
        estimated_seconds=35
    ),
    StepDefinition(
        number=12,
        stage_id='6',
        name="WCM Word Template",
        description="Generates final WCM-formatted Word document. Fills all template sections by taxonomy code, "
                    "bolds CV owner name in publications, formats dates, adds Word comments for reformatted fields. "
                    "Routes presentations (R) and service activities (Q2) to Regional/National/International tables "
                    "based on inferred CV owner location. This is the primary output of the entire pipeline.",
        uses_llm=False,
        weight=3.0,  # Fast Word doc generation
        estimated_seconds=15
    ),
]


def get_step_by_stage_id(stage_id: str) -> StepDefinition:
    """Get step definition by stage ID (e.g., '1a', '3b', '4.5')."""
    for step in STEP_REGISTRY:
        if step.stage_id == stage_id:
            return step
    raise ValueError(f"Unknown stage ID: {stage_id}")


def get_stage_order() -> List[str]:
    """Return ordered list of stage IDs."""
    return [step.stage_id for step in STEP_REGISTRY]


def get_step_weights() -> dict:
    """Return step weights and estimated times for progress calculation."""
    total_weight = sum(step.weight for step in STEP_REGISTRY)
    return {
        'total_weight': total_weight,
        'total_estimated_seconds': sum(step.estimated_seconds for step in STEP_REGISTRY),
        'steps': {
            step.stage_id: {
                'weight': step.weight,
                'weight_pct': (step.weight / total_weight) * 100,
                'estimated_seconds': step.estimated_seconds,
                'cumulative_weight_before': sum(s.weight for s in STEP_REGISTRY[:i]),
                'cumulative_weight_pct_before': (sum(s.weight for s in STEP_REGISTRY[:i]) / total_weight) * 100
            }
            for i, step in enumerate(STEP_REGISTRY)
        }
    }
