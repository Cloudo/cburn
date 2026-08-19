"""Advisor (SPEC §6, M3).

Step 1 - a digest without an LLM (aggregates, up to 20k tokens, no conversation text);
step 2 - `claude -p` on haiku, the answer is a JSON array of tips; a tip without evidence is cut.
"""
