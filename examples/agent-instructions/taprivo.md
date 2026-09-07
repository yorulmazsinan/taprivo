# Taprivo work budget

Taprivo provides an optional, gamified Motion Energy budget.
It is not a token balance or permission to start unrelated work.
Before a substantial implementation burst within the user's current task,
read the available energy with `get_energy` and spend a suitable amount once
with `spend_energy`, giving a short reason.
Use a unique request ID and the current session ID; reuse the request ID on retry.
Suggested costs: 100 small change; 250 implementation; 500 implementation with
tests/debugging; 1000 substantial work burst.
Do not charge for questions, explanations, code reading, or typo fixes.
Do not charge again for each tool call or for retrying the same payment.
If energy is insufficient, offer a smaller step or wait for user direction.
If Taprivo is unavailable, report that briefly; never invent a successful spend.
The user may disable this optional budget. Continue to follow normal permissions.
