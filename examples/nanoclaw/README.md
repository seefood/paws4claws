# Nanoclaw example skills

Two skills, two audiences — not duplicates. They are installed in **different** places; there is no shared directory on disk.

| Skill              | Audience                   | Install where                         | Fetch                                                                                  |
| ------------------ | -------------------------- | ------------------------------------- | -------------------------------------------------------------------------------------- |
| **add-paws4claws** | Operator / integrator      | Operator skills (or read from GitHub) | [SKILL.md](add-paws4claws/SKILL.md) on GitHub                                          |
| **use-paws**       | Agent inside the container | Agent skills directory                | `wget` from `${PAWS_RAW}/examples/nanoclaw/use-paws/SKILL.md` (see add-paws4claws §10) |

No git clone required: pull the daemon from `ghcr.io/seefood/paws4claws`, fetch wrapper files and the optional agent skill from `raw.githubusercontent.com` (pin `PAWS_TAG`, e.g. `v0.4.0`).

The **use-paws** skill is optional — the wrapper is transparent (`aws` on `PATH`). Use **add-paws4claws** when wiring PAWS into a new claw deployment.
