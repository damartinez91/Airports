# Draft eval report

Status: **DRAFT**. candidate answers pending Daniel's review

Scored against DRAFT candidate answers where a question is still draft. Grade the spoken answer as well as the rows. An ambiguous question passes only when the answer has the literal figures, a stated reading that begins 'I read this as', and the expected alternative cuts, each with a number and a unit. If one listed item has a number, every listed item has that number, with its unit (percent, minutes, or flights). A follow-up question is an advisory nudge score. It does not change pass or fail. Rankings must state the minimum-flights rule. When BTS cause codes cannot say why, the answer says that plainly.

Provider: `stub` (`stub`)
Run: 2026-10-12T02:45:18+00:00

## Overall

- Questions: 120
- Matching the candidate answer: 116
- Accuracy: 96.7%

- Advisory nudge (a follow-up was offered; this does not change the score): 120 of 120 (100.0%)

## By difficulty

| Difficulty | Correct | N | Accuracy |
| --- | ---: | ---: | ---: |
| easy | 32 | 32 | 100.0% |
| hard | 28 | 32 | 87.5% |
| medium | 56 | 56 | 100.0% |

## By category

| Category | Correct | N | Accuracy |
| --- | ---: | ---: | ---: |
| airline_reliability | 22 | 23 | 95.7% |
| airport_reliability | 17 | 18 | 94.4% |
| ambiguous_or_trick | 18 | 19 | 94.7% |
| delay_causes | 18 | 18 | 100.0% |
| routes | 16 | 16 | 100.0% |
| seasonality | 11 | 12 | 91.7% |
| time_of_day | 14 | 14 | 100.0% |

## Failure categories

- misread_question: 4

Failure labels are a harness heuristic. `wrong_sql` means the statement did not run. `wrong_aggregation` means the entities lined up but a figure did not. `misread_question` means the shape or the entities were different. `refused` means the analyst declined a question that has a candidate answer. `error` means the run broke before a SQL result.

## Misses

### q020 (hard, misread_question)

Which airline has the lowest median arrival delay systemwide?

No result column matched the candidate entities.

Analyst SQL:

```sql
SELECT 'systemwide' AS label, COUNT(*) AS operated_flights FROM flights_v WHERE operated
```

Reference SQL:

```sql
SELECT airline_code AS label,
       MEDIAN(arr_delay) AS median_arrival_delay,
       COUNT(*) AS n
FROM flights_v
WHERE operated
GROUP BY airline_code
HAVING COUNT(*) >= 5000
ORDER BY median_arrival_delay ASC, label ASC
LIMIT 1
```

### q037 (hard, misread_question)

Which airport has the largest morning-minus-evening on-time arrival gap?

No result column matched the candidate entities.

Analyst SQL:

```sql
SELECT 'systemwide' AS label, COUNT(*) AS operated_flights FROM flights_v WHERE operated
```

Reference SQL:

```sql
SELECT origin AS label,
       AVG(CASE WHEN dep_time_bucket = 'morning' THEN CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END END)
       - AVG(CASE WHEN dep_time_bucket = 'evening' THEN CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END END) AS rate_gap,
       COUNT(*) AS n
FROM flights_v
WHERE operated AND dep_time_bucket IN ('morning', 'evening')
GROUP BY origin
HAVING SUM(CASE WHEN dep_time_bucket = 'morning' THEN 1 ELSE 0 END) >= 500
   AND SUM(CASE WHEN dep_time_bucket = 'evening' THEN 1 ELSE 0 END) >= 500
ORDER BY rate_gap DESC, label ASC
LIMIT 1
```

### q079 (hard, misread_question)

Which on-time arrival rate is lower: 2025-11-26, the Wednesday before Thanksgiving 2025, or Wednesdays in October 2025? Return both rates.

Expected 2 rows and got 1.

Analyst SQL:

```sql
SELECT 'systemwide' AS label, COUNT(*) AS operated_flights FROM flights_v WHERE operated
```

Reference SQL:

```sql
SELECT '2025-11-26' AS label, AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate FROM flights_v WHERE operated AND flight_date = DATE '2025-11-26'
UNION ALL
SELECT '2025-10-wednesdays' AS label, AVG(CASE WHEN on_time_arrival THEN 1.0 ELSE 0.0 END) AS on_time_rate FROM flights_v WHERE operated AND year = 2025 AND month = 10 AND day_of_week = 3
```

### q101 (hard, misread_question)

Does weather explain most delays? Return the weather share of late-arrival minutes, the NAS share, and 1 if the weather share is above one half.

not enough numeric columns

Analyst SQL:

```sql
SELECT 'systemwide' AS label, COUNT(*) AS operated_flights FROM flights_v WHERE operated
```

Reference SQL:

```sql
SELECT 'systemwide' AS label,
       SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS weather_share,
       SUM(nas_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) AS nas_share,
       CASE
           WHEN SUM(weather_delay) * 1.0 / NULLIF(SUM(cause_minutes), 0) > 0.5 THEN 1
           ELSE 0
       END AS weather_is_majority
FROM flights_v
WHERE late_arrival
```

## Matches

q001, q002, q003, q004, q005, q006, q007, q008, q009, q010, q011, q012, q013, q014, q015, q016, q017, q018, q019, q021, q022, q023, q024, q025, q026, q027, q028, q029, q030, q031, q032, q033, q034, q035, q036, q038, q039, q040, q041, q042, q043, q044, q045, q046, q047, q048, q049, q050, q051, q052, q053, q054, q055, q056, q057, q058, q059, q060, q061, q062, q063, q064, q065, q066, q067, q068, q069, q070, q071, q072, q073, q074, q075, q076, q077, q078, q080, q081, q082, q083, q084, q085, q086, q087, q088, q089, q090, q091, q092, q093, q094, q095, q096, q097, q098, q099, q100, q102, q103, q104, q105, q106, q107, q108, q109, q110, q111, q112, q113, q114, q115, q116, q117, q118, q119, q120
