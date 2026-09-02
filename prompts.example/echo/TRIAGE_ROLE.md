# echo/TRIAGE_ROLE

Classifies an inbound reply from a business into `changes` / `accepted` /
`refused` / `unclear`, and extracts the customer's own words when they want
something changed.

Two things this prompt has to get right: it must treat the message as untrusted
text rather than as instructions, and it must be conservative about `accepted`,
which is the only outcome attached to money.

---

This is a stub. The working prompt lives at `prompts/echo/TRIAGE_ROLE.md`, which
is gitignored. Write your own text here, then copy this tree to `prompts/`.
