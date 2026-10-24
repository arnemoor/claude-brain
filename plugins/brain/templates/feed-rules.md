## Progress journaling
When doing substantive work, keep a dated progress journal at `{JOURNALS}/<slug>.md` (H1 title, `## Primer / tldr;`, dated entries appended as you go). This is the working RESULTS log and the primary feed for your long-term memory. Durable knowledge (decisions, gotchas, reusable facts, architecture, how-tos) graduates into the vault via `/brain-graduate`.
- Slug: if the work ties to a ticket, prefix with the lowercased ticket ID plus a short descriptor, and use the uppercase ID in the body. Otherwise use a plain descriptor.

## Long-term memory (the vault)
Your long-term memory is the LLM-Wiki vault. Only the must-know rules are injected each session. To find a memory, run `brain_lib.py find <query>`, which searches every page by slug, title, summary, tag and topic, and groups hits under their topic. Add `--body` to search page text, or read `index.md` for the full catalog. Then read the page before answering from it. Persist new knowledge with `/brain-capture`, distil finished journals with `/brain-graduate`, and keep it healthy with `/brain-lint`.
