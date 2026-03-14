-- DuckDB query for DevOps-Gym Leaderboard
-- Reads from result JSON files submitted via submit_to_agentbeats.py
-- Each result file contains: participants, score, accuracy, timestamp, task_type, tasks_total, tasks_passed

SELECT
    results.participants.purple_agent AS id,
    ROUND(MAX(res.pass_rate) * 100, 1)  AS "Pass Rate",
    MAX(res.tasks_passed)               AS "Tasks Passed",
    MAX(res.tasks_total)                AS "Tasks Total",
    MAX(res.task_type)                  AS "Task Type"
FROM results
CROSS JOIN UNNEST(results.results) AS r(res)
GROUP BY results.participants.purple_agent
ORDER BY "Pass Rate" DESC;
