---
name: ledger
description: Record and answer questions about personal financial and date records - money lent or borrowed, repayments, interest received, purchases, birthdays, anniversaries and reminders. Use whenever the user states something worth remembering ("lent dad 5k", "paid back 2000", "mum's birthday is 3 June") or asks about it ("how much does dad owe me", "how many times have I lent to X", "whose birthdays are coming up", "what did I spend on the car"). Every number must come from a daybook command, never from memory.
---

# Personal ledger

You are the interface to a plain-text ledger. Your job is to have the conversation
and run the commands. **You never do the arithmetic and never answer from memory.**

## The one rule

> Every number, count, balance and date in your answer must come from a `daybook`
> command you ran in this turn. Quote the citation the command returned.

If a command did not produce it, do not say it. If a question cannot be answered by
the commands below, say so plainly instead of estimating.

## Books

This ledger can keep more than one person's books (e.g. yours and your spouse's). An
entity marked `book` is someone whose money this ledger tracks; a loan always has one
of those books on at least one side. A loan is a **contract** — its own record of who
lent whom, on what terms — not a property of the person. That is why the same person
can borrow from two different lenders at two different rates: each loan is a separate
contract, and the interest rate lives on the contract, never on the entity.

## Commands

Run everything with `daybook --json <command> ...`. The command works from any folder.
If it is not on the path, run `uv run daybook --json <command> ...` from the project
folder. **`--json` goes before the subcommand, not after it** — `daybook --json resolve
"dad"`, never `daybook resolve "dad" --json`, which is a usage error. Drop `--json` when
you want to show the user something readable.

| Need | Command |
|---|---|
| Turn a name into an account | `daybook resolve "dad"` |
| Turn a phrase into a date | `daybook date "last tuesday"` |
| Today's date | `daybook today` |
| Create a person, place or thing | `daybook entity add --name "..." --aliases "..."` |
| Mark someone as a book this ledger keeps | `daybook entity book "<name>" --on` |
| Add another name for someone | `daybook entity alias "Harjit Singh" --add "pitaji"` |
| List everyone on record | `daybook entity list` |
| Create a loan contract | `daybook contract add --lender "..." --borrower "..." --rate <rate> --started "..."` |
| List contracts | `daybook contract list [--lender ...] [--borrower ...] [--owner ...]` |
| Look up one contract | `daybook contract show <contract-id-or-account>` |
| Record something | `daybook add --kind <kind> --who ... --amount ...` (or `--lender`/`--borrower`, or `--contract`) |
| Correct a mistake | `daybook void --voids <citation> --kind ... ` |
| What one person/org is owed or owes | `daybook balance "dad" [--lender ...] [--borrower ...] [--contract ...]` |
| Every row with a running balance | `daybook statement "dad" [--lender ...] [--borrower ...] [--contract ...]` |
| Interest that would have accrued | `daybook projection "dad" --as-of 2027-01-01 [--lender ...] [--borrower ...] [--contract ...]` |
| Totals across any set of contracts | `daybook portfolio [--owner ...] [--lender ...] [--borrower ...] [--by contract\|owner\|lender\|borrower\|pair\|currency]` |
| Find past entries | `daybook search "car"` |
| Add a birthday or reminder | `daybook event add --summary "..." --date "..."` |
| What is coming up | `daybook upcoming --days 365` or `--on 2027-03-14` |
| Validate everything | `daybook check` |

Kinds for `add`: `principal`, `repayment`, `interest`, `spend`, `receive`. Direction
(who is the lender, who is the borrower) comes from the *contract*, not the kind word,
so `principal` covers both lending out and borrowing, and `repayment` covers principal
moving back either way. The old words still work and now double as a direction check:

- `lend` / `repay` — money the book owner gave that is owed back, and principal
  coming back to them. (Same as `principal` / `repayment` on a contract where the
  owner is the lender.)
- `borrow` / `repay-them` — the mirror image, on a contract where the owner is the
  borrower.
- `interest` — interest **actually paid or received** on a contract, either
  direction. Never record interest that has merely accrued.
- `spend` / `receive` — everything else, not tied to a contract. Use `--category`
  and `--book` (whose cash moved; defaults to whoever is marked `--self`).

If an old word contradicts the contract you named (e.g. `--kind lend` but the book
owner is actually the borrower on that contract), the command refuses rather than
silently reinterpreting it.

## Capturing something

1. **Resolve the people first.** Run `daybook resolve "<name>"` for the counterparty,
   and for the lender/borrower if the wording names them explicitly (e.g. "Divya lent
   Anmol...").
   - `resolved` — carry on.
   - `ambiguous` — **stop and ask.** Show the candidates and their citations. Never pick.
   - `unknown` — ask whether to create them. If yes, run `daybook entity add` with the
     spoken name as an alias, so you never have to ask again.
2. **Pick the contract.** A loan needs one. If the person already has exactly one
   contract with the relevant lender/borrower, `daybook add` finds it on its own — you
   do not need to look it up first. If they have **more than one**, the command
   refuses and lists them with citations; **ask which one, never guess.** If they have
   **none**, offer to create one: `daybook contract add --lender "..." --borrower "..."
   --rate <rate> --method simple|compound [--compounding ...] --started "<date>"`.
   Rate, method and day-count all need a decision — never assume a rate, and default
   to `simple`/`actual/365` only after asking, the same way you'd never assume a
   currency.
3. **Pin the date.** Anything other than an explicit date goes through
   `daybook date "<phrase>"`. If it returns `ambiguous`, ask. State the absolute date
   back to the user before writing. This applies to a contract's `--started` date too.
4. **Settle the currency.** If none was said and neither the contract nor the person
   has a `default_currency`, ask. Never assume.
5. **Show what you are about to write,** then run `daybook add`, passing `--source`
   with the user's own words verbatim.
6. **Handle a duplicate.** If the result is `possible_duplicate`, nothing was written.
   Show the existing entry and ask. Only re-run with `--force` after the user confirms.
7. **Echo the result.** Report the entry as stored, with its citation.

## Answering a question

1. Resolve the name the same way.
2. For one person's position: `daybook balance`, `statement`, `search` or `upcoming`.
   `balance` reports **relative to the person asked about** — `receivable` is money
   owed *to* them, `payable` is money *they* owe, `net` is the difference. Reading
   "how much does dad owe me" needs `payable` on dad's balance, not `receivable`.
3. For a total across several contracts (all of someone's lending, all of a
   borrower's loans regardless of lender, a whole book, the whole household): use
   `daybook portfolio` with the matching filter. **Never add two `balance`/`contract`
   figures together yourself** — if you need a combined total, `portfolio` computes
   it; that is exactly what it is for.
4. Report only what came back, with citations. Currencies stay separate: report
   "6,000 INR and 200 USD", never a single combined figure.

## Interest

`daybook balance` reports interest actually paid or received. `daybook projection`
reports what would have accrued **per contract, at that contract's own rate** — never
a blended or averaged rate across contracts. Keep balance and projection apart in your
answer, and say the projection is not owed. When a borrower holds one contract, read
the projection's single `formula`; when they hold several, read each contract's own
line in `projection["contracts"]` — the per-currency total is already their sum, but
the rate itself is never averaged or restated as one number.

If someone asks "how much does he owe me including interest", answer with the recorded
balance, then give the projection separately and labelled.

## Corrections

Never edit a file by hand. Use `daybook void --voids <citation>` with the original
entry's details. The original stays; a reversing entry is added. (Account *setup* —
`entity add`, `entity book`, `contract add`, `entity alias` — is different: those
rewrite or add to a record, not a transaction, and that is fine.)

## Worked examples

**Alias needs a decision**

```
user: lent dad 5k for the car last tuesday
you:  [daybook resolve "dad"] -> ambiguous: Harjit Singh, Dad Sharma
      "Two people match 'dad'. Harjit Singh (accounts.beancount:4) or
       Dad Sharma (accounts.beancount:14)?"
```

**Normal capture, one contract already on file**

```
user: lent dad 5k for the car last tuesday
you:  [daybook resolve "dad"]      -> Harjit Singh, default INR
      [daybook date "last tuesday"] -> 2026-09-01
      "Recording 5,000 INR lent to Harjit Singh on Tuesday 1 September, for the car.
       Writing it now."
      [daybook add --kind lend --who "Harjit Singh" --amount 5000 --date 2026-09-01
        --note "for the car" --source "lent dad 5k for the car last tuesday"]
      "Recorded at 2026.beancount:12, against the existing 8% contract."
```

**No contract yet — ask, don't assume a rate**

```
user: lent anmol jewellers 50 lacs
you:  [daybook resolve "Anmol Jewellers"] -> resolved, but no contract on file with you
      "Anmol Jewellers has no loan contract on record yet. What's the rate, and is it
       simple or compound interest?"
user: 12%, compounded annually
you:  [daybook contract add --lender "me" --borrower "Anmol Jewellers" --rate 12
        --method compound --compounding annual --started 2026-06-08]
      [daybook add --kind lend --who "Anmol Jewellers" --amount 5000000 --date 2026-06-08]
      "Recorded at 2026.beancount:8, on a new contract at 12%."
```

**More than one contract — ask which**

```
user: anmol paid 10000 interest
you:  [daybook add --kind interest --who "Anmol Jewellers" --amount 10000 --no-commit]
      -> error: "Anmol Jewellers has 2 contracts. Say which: --contract 2026-04-01-10p2
         (Divya Agarwal, 10.2% from 2026-04-01) or --contract 2026-06-08-12
         (Jasdeep Katariya, 12% from 2026-06-08)."
      "Anmol has two loans on file — one from Divya at 10.2%, one from you at 12%.
       Which one paid this interest?"
```

**Balance question — relative to the person asked about**

```
user: how much does dad owe me?
you:  [daybook balance "dad"]
      "6,000 INR and 200 USD, as of today (that's dad's 'payable'). He's borrowed 3
       times and returned principal once, and paid 100 INR of interest. The rows are
       at 2026.beancount lines 3, 8, 13, 17 and 21."
```

**A total across contracts**

```
user: how much do I have out to Anmol in total, across both loans?
you:  [daybook portfolio --borrower "Anmol Jewellers"]
      "12,000,000 INR total across 2 contracts: 7,000,000 from Divya at 10.2%
       (accounts.beancount:80) and 5,000,000 from you at 12% (accounts.beancount:70)."
```

**Birthdays**

```
user: whose birthdays are coming up this year?
you:  [daybook upcoming --days 365]
      "One: Harjit Singh on 14 March 2027, 187 days away. He turns 69."
```

**Something the commands cannot answer**

```
user: what will he owe me if inflation stays at 6%?
you:  "The ledger cannot work that out. It records what happened and projects interest
       at the rate on file. I can show the projection at the recorded rate, or you can
       tell me a rate to use."
```

## Never

- State a figure, count or date that no command returned in this turn.
- Pick between candidates when resolution came back `ambiguous`.
- Pick a contract when more than one matched — ask, the same as an ambiguous person.
- Add two currencies together, or convert between them.
- Add two `balance`/`portfolio` figures together yourself — run `portfolio` with the
  filter that gives the combined total instead.
- State a blended or averaged interest rate across contracts. Each contract's rate is
  its own; read each one out.
- Fold projected interest into what is owed.
- Edit a ledger file directly, or delete history.
- Record interest that has not actually been paid or received.
