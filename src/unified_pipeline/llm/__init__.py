"""Provider adapters and shared call infrastructure for llm_client.py.

llm_client.call_llm is still the only import surface pipeline stages use;
this package holds the implementation split out from behind that facade
(#496). Nothing here is meant to be imported directly outside llm_client.py
and its tests.
"""
