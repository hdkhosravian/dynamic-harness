# Owner guide

This page is for the person who asks the questions and relies on the answers. You do not need to read code.

## What you see in a task

1. **An opening line.** For a task that gets checked, Claude first prints a line the harness wrote, for
   example: "I'll prepare: March sales by branch. I'll check it before I call it done." For larger tasks it
   may add how long similar tasks took, and roughly what they cost.
2. **A few questions, at most.** See [Questions](#questions).
3. **The deliverable**, in `.claude/loops/<job>/work/` in your folder: a report, a document, a forecast
   workbook, the rows behind a figure.
4. **A state line**, written by the harness from the signed receipt. It is the last thing you see.

A quick question ("what does EBITDA mean?") gets a direct answer, with none of this.

## The three states

Every deliverable ends with exactly one of these:

| State | Meaning |
| --- | --- |
| **Checked** | Every part passed its registered check, and the gate replayed those checks on what was delivered. |
| **Done, not fully checked: \<what\>** | Something was not checked, and the line names it: a failed check, a number the gate could not trace, a part with no executable check. |
| **Partly done: N of M** | Some parts are blocked or not done. A stop for budget, cost or lack of progress also reaches you this way, with the cost of continuing. |

Words such as "checked", "verified", "confirmed" and "matches" may appear only in that state line. If Claude
uses them anywhere else, the harness blocks the message.

A forecast that is "Checked" means the arithmetic is right. It does not mean the assumptions are right. The
report always says "Arithmetic checked; assumptions not."

## A money figure

A figure you rely on comes with a three-line header, for example:

```text
1,250,000,000 Toman: sales (definition version 2), Esfand 1402 (20 Feb to 19 Mar 2024).
Based on sales.csv, with data up to 1402/12/29. Excludes: returns; 3 transfers between your own accounts. Rows used: 412.
Checked: matches bank.csv.
```

The first time a metric is used, you are asked to confirm its definition in one sentence ("I'll count sales
as ..., excluding ..., in Toman. Is that right?"). The rows behind the figure are saved as
`work/rows-<unit>.xlsx`.

Notes about the data (rows left out, data that ends before the period does, two sources that disagree) appear
next to the figure, in your language. When two sources disagree, you see both; the harness never picks one.

## Questions

The harness limits how often Claude may interrupt you:

- At most 3 questions at the start, in one message, each with a suggested answer.
- At most 5 blocking questions per task.
- No new questions once half the work is done. Claude records an assumption instead, and the report says so.
- Claude is told not to ask about technical choices, or about things it can find out itself.

Some contacts do not count against the cap: a cost question after a stop, the one acknowledgement a financial
decision gets, safety approvals, and one handoff per task ("I've paused this task so you can answer when
ready").

## When a task stops

| You see | Why | What to do |
| --- | --- | --- |
| "I did 3 of 8 and stopped at your limit. ... Continue?" | The task used its call or time budget, or your spending limit. | Reply "yes" or "continue" to grant one extra increment, or tell Claude what to change. |
| "Stuck: I stopped before finishing ..." | No progress for 40 steps, or the same problem three times. | Reply "continue" to try again, or say what to change. |
| "I can't help with this kind of decision. Please ask a qualified professional." | Medical and safety decisions are refused by default. | Ask a professional. |
| A task stopped for tampering | A protected record changed outside the harness. | Run `/dynamic-harness:status --repair <job>` to close it. It is not trusted or re-signed. |

Tax, legal and employment work is always "prepared, not final": a professional must sign off.

## Your commands

These run in a hook. Claude never sees them and cannot run them. Anything that changes something shows the
change in plain words with a one-time 6-digit code first; type `confirm <code>` in the same session within 10
minutes to apply it.

### `/dynamic-harness:business`

Your business settings.

```text
/dynamic-harness:business show
/dynamic-harness:business set currency IRT
/dynamic-harness:business set calendar jalali
/dynamic-harness:business set caps.task 500000
/dynamic-harness:business add own_accounts <account>
/dynamic-harness:business confirm <code>
```

| Field | Meaning |
| --- | --- |
| `currency` | A 3-letter code: `IRT` (Toman), `IRR` (Rial), `USD`, `EUR`, ... |
| `calendar` | `jalali` or `gregorian` |
| `fiscal_year_start` | The first day of your fiscal year |
| `language` | The language the harness writes to you in |
| `caps.task`, `caps.month` | Spending limits per task and per month |
| `fx.USD` | The US dollar rate in your currency. Without it, spending limits in another currency stay off. |
| `footer` | The footer your documents must carry |
| `work_week`, `jurisdiction`, `owner_name`, `retention_years`, `thresholds.*` | As named |

Lists you can `add` to and `remove` from: `own_accounts` (transfers between them are not revenue),
`do_not_contact`, `protected_terms` (names that never go into a web search). `metric remove <id>` deletes a
metric definition.

### `/dynamic-harness:memory`

What the harness remembers about how you like your work: `tone` (formal, neutral, warm), `format` (short,
detailed), `report_style` (summary first, table first) and `rounding` (none, thousands, millions).

```text
/dynamic-harness:memory show
/dynamic-harness:memory set tone formal
/dynamic-harness:memory forget all
```

Memory never holds recipients, permissions, budgets or approvals.

### `/dynamic-harness:status`

Your tasks and their states.

```text
/dynamic-harness:status
/dynamic-harness:status --abandon <job>
/dynamic-harness:status --repair <job>
```

### `/dynamic-harness:export`

Decision records as a CSV file in this folder. Every finished or stopped task has one, with personal data
redacted.

```text
/dynamic-harness:export --task <job>
/dynamic-harness:export --period 2024-03
/dynamic-harness:export --period 2024-01:2024-03
```

Periods are Gregorian months (`YYYY-MM`), matched against the date each record was written. Only this folder's
tasks are exported.

### Operator commands

```text
/dynamic-harness:config show <file>
/dynamic-harness:config set <file> <key> <value>
/dynamic-harness:off
/dynamic-harness:off on
```

`config` changes budgets, gate profiles, the egress allowlist and prices. `off` puts the question cap and the
tripwire in shadow mode: they log what they would have blocked and block nothing. The safety hooks stay on.

## Persian support

Persian is supported natively, not through translation:

- The harness's own text is written in Persian in `templates/fa.json`, not translated from English. The
  opening line, stop messages, the research plan, the answer header, check notes and the state line follow
  your language, with Persian digits.
- Dates use the Jalali calendar, including leap Esfand and your fiscal year, shown next to the Gregorian
  dates.
- Amounts in Toman and Rial are told apart from column headers and labels. 1 Toman is 10 Rial, and a total
  that jumps ten times against the prior period is flagged as a possible Toman/Rial mix-up.
- Names are matched across Persian and Arabic letter forms (ی/ي, ک/ك), ZWNJ and Persian, Arabic or Latin
  digits.

The language comes from your profile's `language`, else from your latest message. If you write in Persian,
Claude writes the report in Persian too.
