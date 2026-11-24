# CLAUDE.md

Before starting any work in this repo, read **BRAIN.md**. It holds the plan, architecture,
status of every task (done / in progress / to do) and the decisions log.

After finishing a task, update BRAIN.md: change its status, add new tasks you discovered,
and record any design decision in the decisions log.

Conventions:
- New attacks are plugins in `swarm/attacks/` (subclass `Attack`, register in `__init__.py`).
- Every attack needs a Break → Fix story (a weakness plus its countermeasure).
- Keep the demo runnable offline (vendor JS libraries, no CDNs).
