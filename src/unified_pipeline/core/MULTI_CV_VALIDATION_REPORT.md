# Multi-CV Validation Report

**Date**: 2025-11-07
**Test CVs**: 5
**Successful**: 5
**Failed**: 0

---

## Executive Summary

**Aggregate Statistics**:
- Total groups processed: 71
- Total entries classified: 1,329
- Average confidence: 0.942
- Total API calls: 164
- Total tokens: 239,597

---

## Results by CV

| CV | Size | Groups | Entries | Confidence | API Calls | Tokens | Time |
|----|------|--------|---------|------------|-----------|--------|------|
| CV_2018_Cook_Dane_PhD_segmented_gold.jso | 33KB | 5 | 71 | 0.950 | 10 | 16,022 | 58.7s |
| CV_2002_Holtz_Heidi_PhD_segmented_gold.j | 47KB | 15 | 88 | 0.944 | 29 | 39,106 | 128.2s |
| CV_2007_Lau_Frank_MD_segmented_gold.json | 88KB | 13 | 276 | 0.942 | 22 | 32,656 | 121.0s |
| CV_2003_Albrecht_Jennifer_PhD_segmented_ | 134KB | 17 | 325 | 0.937 | 47 | 67,092 | 218.2s |
| CV_2009_Mucci_Lorelei_ScD_segmented_gold | 209KB | 19 | 569 | 0.936 | 56 | 84,721 | 315.2s |

---

## Detailed Results

### CV_2018_Cook_Dane_PhD_segmented_gold.json

**File Size**: 34,272 bytes (33.5 KB)

**Preprocessing**:
- Original groups: 5
- Content groups: 5
- Preserved headers: 0
- Total entries: 71

**Mapping**:
- Total sections classified: 6
- Skipped headers: 0
- High confidence (≥0.85): 6 (100.0%)
- Average confidence: 0.950

**API Usage**:
- Pass 1 calls: 6
- Pass 2 calls: 4
- Total API calls: 10
- API calls saved: 0

**Token Usage**:
- Prompt tokens: 14,102
- Completion tokens: 1,920
- Total tokens: 16,022

**Performance**:
- Elapsed time: 58.7s

---

### CV_2002_Holtz_Heidi_PhD_segmented_gold.json

**File Size**: 47,670 bytes (46.6 KB)

**Preprocessing**:
- Original groups: 15
- Content groups: 15
- Preserved headers: 0
- Total entries: 88

**Mapping**:
- Total sections classified: 18
- Skipped headers: 0
- High confidence (≥0.85): 18 (100.0%)
- Average confidence: 0.944

**API Usage**:
- Pass 1 calls: 18
- Pass 2 calls: 11
- Total API calls: 29
- API calls saved: 0

**Token Usage**:
- Prompt tokens: 34,342
- Completion tokens: 4,764
- Total tokens: 39,106

**Performance**:
- Elapsed time: 128.2s

---

### CV_2007_Lau_Frank_MD_segmented_gold.json

**File Size**: 90,139 bytes (88.0 KB)

**Preprocessing**:
- Original groups: 13
- Content groups: 13
- Preserved headers: 0
- Total entries: 276

**Mapping**:
- Total sections classified: 13
- Skipped headers: 0
- High confidence (≥0.85): 13 (100.0%)
- Average confidence: 0.942

**API Usage**:
- Pass 1 calls: 13
- Pass 2 calls: 9
- Total API calls: 22
- API calls saved: 0

**Token Usage**:
- Prompt tokens: 27,664
- Completion tokens: 4,992
- Total tokens: 32,656

**Performance**:
- Elapsed time: 121.0s

---

### CV_2003_Albrecht_Jennifer_PhD_segmented_gold.json

**File Size**: 137,232 bytes (134.0 KB)

**Preprocessing**:
- Original groups: 18
- Content groups: 17
- Preserved headers: 1
- Total entries: 325

**Mapping**:
- Total sections classified: 30
- Skipped headers: 1
- High confidence (≥0.85): 30 (100.0%)
- Average confidence: 0.937

**API Usage**:
- Pass 1 calls: 30
- Pass 2 calls: 17
- Total API calls: 47
- API calls saved: 1

**Token Usage**:
- Prompt tokens: 58,827
- Completion tokens: 8,265
- Total tokens: 67,092

**Performance**:
- Elapsed time: 218.2s

---

### CV_2009_Mucci_Lorelei_ScD_segmented_gold.json

**File Size**: 214,436 bytes (209.4 KB)

**Preprocessing**:
- Original groups: 20
- Content groups: 19
- Preserved headers: 1
- Total entries: 569

**Mapping**:
- Total sections classified: 33
- Skipped headers: 3
- High confidence (≥0.85): 33 (100.0%)
- Average confidence: 0.936

**API Usage**:
- Pass 1 calls: 33
- Pass 2 calls: 23
- Total API calls: 56
- API calls saved: 3

**Token Usage**:
- Prompt tokens: 73,344
- Completion tokens: 11,377
- Total tokens: 84,721

**Performance**:
- Elapsed time: 315.2s

---


## Analysis

### Token Efficiency by CV Size

| CV | Size (KB) | Total Tokens | Tokens/KB |
|----|-----------|--------------|----------|
| CV_2018_Cook_Dane_PhD_segmented_gold.jso | 33 | 16,022 | 479 |
| CV_2002_Holtz_Heidi_PhD_segmented_gold.j | 47 | 39,106 | 840 |
| CV_2007_Lau_Frank_MD_segmented_gold.json | 88 | 32,656 | 371 |
| CV_2003_Albrecht_Jennifer_PhD_segmented_ | 134 | 67,092 | 501 |
| CV_2009_Mucci_Lorelei_ScD_segmented_gold | 209 | 84,721 | 405 |

### Confidence Distribution

- Minimum: 0.936
- Maximum: 0.950
- Average: 0.942
- Range: 0.014

### Pass 2 Utilization

- Average Pass 2 rate: 64.7% of sections
- This indicates how many sections required child classification
