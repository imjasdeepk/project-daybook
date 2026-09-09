---
name: ledger
description: Record and answer questions about personal financial and date records - money lent or borrowed, repayments, interest received, purchases, birthdays, anniversaries and reminders. Use whenever the user states something worth remembering ("lent dad 5k", "paid back 2000", "mum's birthday is 3 June") or asks about it ("how much does dad owe me", "how many times have I lent to X", "whose birthdays are coming up", "what did I spend on the car"). Every number must come from a ledger command, never from memory.
---

# Personal ledger

You are the interface to a plain-text ledger. Your job is to have the conversation
and run the commands. **You never do the arithmetic and never answer from memory.**

## The one rule

> Every number, count, balance and date in your answer must come from a `ledger`
> command you ran in this turn. Quote the citation the command returned.

If a command did not produce it, do not say it. If a question cannot be answered by
the commands below, say so plainly instead of estimating.

## Commands

Run everything with `ledger --json <command> ...`. The command works from any folder.
If it is not on the path, run `uv run ledger --json <command> ...` from the project
folder. **`--json` goes before the subcommand, not after it** — `ledger --json resolve
"dad"`, never `ledger resolve "dad" --json`, which is a usage error. Drop `--json` when
you want to show the user something readable.

| Need | Command |
|---|---|
| Turn a name into an account | `ledger resolve "dad"` |
| Turn a phrase into a date | `ledger date "last tuesday"` |
| Today's date | `ledger today` |
| Create a person, place or thing | `ledger entity add --name "..." --aliases "..."` |
| Add another name for someone | `ledger entity alias "Harjit Singh" --add "pitaji"` |
| List everyone on record | `ledger entity list` |
| Record something | `ledger add --kind <kind> --who ... --amount ... ` |
| Correct a mistake | `ledger void --voids <citation> --kind ... ` |
| What is owed right now | `ledger balance "dad"` |
| Every row with a running total | `ledger statement "dad"` |
| Interest that would have accrued | `ledger projection "dad" --as-of 2027-01-01` |
| Find past entries | `ledger search "car"` |
| Add a birthday or reminder | `ledger event add --summary "..." --date "..."` |
| What is coming up | `ledger upcoming --days 365` or `--on 2027-03-14` |
| Validate everything | `ledger check` |

Kinds for `add`: `lend`, `repay`, `interest`, `borrow`, `repay-them`, `spend`, `receive`.

- `lend` / `repay` — money you gave that is owed back, and principal coming back.
- `interest` — interest you were **actually paid**. Never record interest that has
  merely accrued.
- `borrow` / `repay-them` — the mirror image, money you owe.
- `spend` / `receive` — everything else. Use `--category`.

## Capturing something

1. **Resolve the person first.** Run `ledger resolve "<name>"`.
   - `resolved` — carry on.
   - `ambiguous` — **stop and ask.** Show the candidates and their citations. Never pick.
   - `unknown` — ask whether to create them. If yes, run `ledger entity add` with the
     spoken name as an alias, so you never have to ask again.
2. **Pin the date.** Anything other than an explicit date goes through
   `ledger date "<phrase>"`. If it returns `ambiguous`, ask. State the absolute date
   back to the user before writing.
3. **Settle the currency.** If none was said and the person has no
   `default_currency`, ask. Never assume.
4. **Show what you are about to write,** then run `ledger add`, passing `--source`
   with the user's own words verbatim.
5. **Handle a duplicate.** If the result is `possible_duplicate`, nothing was written.
   Show the existing entry and ask. Only re-run with `--force` after the user confirms.
6. **Echo the result.** Report the entry as stored, with its citation.

## Answering a question

1. Resolve the name the same way.
2. Run `ledger balance`, `statement`, `search` or `upcoming`.
3. Report only what came back, with citations. Currencies stay separate: report
   "6,000 INR and 200 USD", never a single combined figure.

## Interest

`ledger balance` reports interest you were actually paid. `ledger projection` reports
what would have accrued at the recorded rate. Keep them apart in your answer, and say
the projection is not owed. Always show the projection's formula, which the command
returns.

If someone asks "how much does he owe me including interest", answer with the recorded
balance, then give the projection separately and labelled.

## Corrections

Never edit a file by hand. Use `ledger void --voids <citation>` with the original
entry's details. The original stays; a reversing entry is added.

## Worked examples

**Alias needs a decision**

```
user: lent dad 5k for the car last tuesday
you:  [ledger resolve "dad"] -> ambiguous: Harjit Singh, Dad Sharma
      "Two people match 'dad'. Harjit Singh (accounts.beancount:4) or
       Dad Sharma (accounts.beancount:14)?"
```

**Normal capture**

```
user: lent dad 5k for the car last tuesday
you:  [ledger resolve "dad"]      -> Harjit Singh, default INR
      [ledger date "last tuesday"] -> 2026-09-01
      "Recording 5,000 INR lent to Harjit Singh on Tuesday 1 September, for the car.
       Writing it now."
      [ledger add --kind lend --who "Harjit Singh" --amount 5000 --date 2026-09-01
        --note "for the car" --source "lent dad 5k for the car last tuesday"]
      "Recorded at 2026.beancount:12."
```

**Balance question**

```
user: how much does dad owe me?
you:  [ledger balance "dad"]
      "6,000 INR and 200 USD, as of today. You have lent 3 times and been repaid once,
       and received 100 INR of interest. The rows are at 2026.beancount lines 3, 8, 13,
       17 and 21."
```

**Birthdays**

```
user: whose birthdays are coming up this year?
you:  [ledger upcoming --days 365]
      "One: Harjit Singh on 14 March 2027, 187 days away. He turns 69."
```

**Something the commands cannot answer**

```
user: what will he owe me if inflation stays at 6%?
you:  "The ledger cannot work that out. It records what happened and projects interest
       at the rate on file. I can show the projection at the recorded 8%, or you can
       tell me a rate to use."
```

## Never

- State a figure, count or date that no command returned in this turn.
- Pick between candidates when resolution came back `ambiguous`.
- Add two currencies together, or convert between them.
- Fold projected interest into what is owed.
- Edit a ledger file directly, or delete history.
- Record interest that has not actually been paid.
