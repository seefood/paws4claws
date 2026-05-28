# Nanoclaw example skills

Two skills, two audiences — not duplicates.

| File                                                 | Audience                       | Purpose                                                                                   |
| ---------------------------------------------------- | ------------------------------ | ----------------------------------------------------------------------------------------- |
| [`use-paws/SKILL.md`](use-paws/SKILL.md)             | **Agent inside the container** | How to run `aws`, pipe output, file upload patterns, error signals                        |
| [`add-paws4claws/SKILL.md`](add-paws4claws/SKILL.md) | **Operator / integrator**      | Docker network, tokens, three wrapper install modes (default: host `~/bin`), verification |

Optionally drop `use-paws/` into the agent skills directory — not required, because the
wrapper is transparent (`aws` on `PATH`). Use `add-paws4claws` when wiring PAWS into a new claw deployment.
