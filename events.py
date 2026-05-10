"""
Canonical list of the 6 seeded anomaly windows.

Single source of truth imported by run.py and api/main.py.
Dates and products only — no causes (the LLM figures those out).
"""

from datetime import date

EVENTS = [
    {
        "id":       1,
        "label":    "2023-03-10 to 2023-03-17",
        "start":    date(2023, 3, 10),
        "end":      date(2023, 3, 17),
        "products": ["regular_ach", "check"],
    },
    {
        "id":       2,
        "label":    "2023-09-15 to 2023-09-19",
        "start":    date(2023, 9, 15),
        "end":      date(2023, 9, 19),
        "products": ["regular_ach", "two_day_ach"],
    },
    {
        "id":       3,
        "label":    "2024-02-21 to 2024-02-25",
        "start":    date(2024, 2, 21),
        "end":      date(2024, 2, 25),
        "products": ["one_day_ach"],
    },
    {
        "id":       4,
        "label":    "2024-11-25 to 2024-11-29",
        "start":    date(2024, 11, 25),
        "end":      date(2024, 11, 29),
        "products": ["regular_ach", "two_day_ach", "one_day_ach"],
    },
    {
        "id":       5,
        "label":    "2025-04-02 to 2025-04-03",
        "start":    date(2025, 4, 2),
        "end":      date(2025, 4, 3),
        "products": ["one_day_ach"],
    },
    {
        "id":       6,
        "label":    "2025-12-22 to 2025-12-31",
        "start":    date(2025, 12, 22),
        "end":      date(2025, 12, 31),
        "products": ["regular_ach", "check", "two_day_ach"],
    },
]
