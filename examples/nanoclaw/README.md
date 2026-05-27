# Nanoclaw example skills

Two skills, two audiences — not duplicates.

| File                                                 | Audience                       | Purpose                                                            |
| ---------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------ |
| [`use-paws/SKILL.md`](use-paws/SKILL.md)             | **Agent inside the container** | How to run `aws`, pipe output, file upload patterns, error signals |
| [`add-paws4claws/SKILL.md`](add-paws4claws/SKILL.md) | **Operator / integrator**      | Docker network, tokens, Dockerfile, `NO_PROXY`, verification       |

Drop `use-paws/` into the agent image skills directory. Use `add-paws4claws` when wiring PAWS into a new claw deployment.
