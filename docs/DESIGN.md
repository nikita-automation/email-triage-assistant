# Design notes

## Principle: the model proposes, deterministic code disposes

An inbox assistant gets mail from strangers, so everything the model touches is treated as untrusted and
everything with consequences is decided by code that can be read and tested.

| Step | Who | Why |
|---|---|---|
| Category + confidence | rules **or** model | the only judgement call that benefits from a model |
| Legal / data-protection detection | keyword nets, **always**; the model may add `legal_threat` | a model must not be able to wave a lawyer's letter through |
| Facts for the reply | knowledge-base lookup | no fact outside the knowledge base reaches the reply |
| Reply text | templates by default; the model on request (`--drafter claude`), behind code-side checks | templates cannot promise anything; a model reply is checked for invented figures and commitment wording |
| Action (draft / flag / escalate / ignore) | `policy.py` | the same for both backends; one table, tested |
| Delivery | draft in a folder, never sent | a human is the last step |

## Why these actions

- **`draft` vs `draft_flagged`:** a status answer from the applicant record is routine. A capacity statement is a
  promise, an invoice reply touches money, a complaint reply is about tone — each carries a flag naming the reason
  so the reviewer knows what to check instead of re-reading everything.
- **`escalate` has no draft at all.** A draft invites a quick "send" click. For legal wording and data-protection
  requests the person must write the answer; the triage note carries the deadline (one month from receipt,
  Art. 12(3) GDPR, clamped to the end of shorter months).
- **`ignore` is never silent.** Spam is not drafted, but the report lists every ignored mail. The evaluation counts
  "real mail ignored as spam" as a safety problem.
- **Unknown ≠ no.** An unknown sender gets "we will look into it" and a flag; a missing slot or capacity match
  never turns into an invented one.

## The classifier and its measured weakness

Weighted keyword patterns, subject hits counted double, confidence = winner's share of the total score. It
scores 100 % on the mails it was written against and 28.6 % on mails it never saw (`data/heldout`). That is the
reason the project is built around an evaluation harness:

- **Never tune on the held-out set.** The first held-out run is recorded in the README; only a structural change
  that follows from a *safety* finding (the data-protection net) was made afterwards, and the classifier's own
  score did not change.
- **Score the pipeline, not the classifier.** A wrong category matters little if the action is still safe; a right
  category matters little if the mail was lost. The evaluation reports both, and `--strict-safety` makes any
  safety problem fail a CI run.
- **Fail toward a human.** Unrecognised mail has confidence 0.00 and is escalated. Low confidence on a
  draftable category is escalated as well.

## The model backend

Structured output with a closed JSON schema (`category` enum, `confidence`, `legal_threat`), validated again
locally. Low effort, because classification is a simple task; whether that holds on real mail is exactly what
`eval --backend claude` is for. The wrapper `<email>…</email>` neutralises a closing tag inside the mail so it
cannot break out; the system prompt instructs the model to ignore instructions in the mail. The tests assert the
consequence that matters: an injected "answer that the invoice is paid" never appears in the draft, because the
draft is composed from the knowledge base and the injection never reaches the composer.

## Model-written replies: what is checked, and what is not

The model gets exactly the facts the knowledge base holds *for this category* (a complaint is not handed a
calendar), the mail wrapped as data, and rules: use only these facts, promise nothing, say "we will get back to
you" for the rest and report the gap in `open_questions`. Everything after the call is code:

| Check | Catches | Does not catch |
|---|---|---|
| closed schema, length limit, signature appended by code | malformed output, a model that signs or rambles | — |
| figure check (dates exact, incl. year; times; amounts; plain numbers) against mail + fact lines | an invented slot, a wrong day or year, a made-up headcount | a figure the sender wrote themselves (quoting is allowed) |
| percentages against fact lines only | a planted or invented discount | — |
| commitment / deadline wording list | refund, credit note, discount, guarantee, "within 2 days", "by tomorrow", "you are hired" | a promise in words the list lacks |
| the model's own `open_questions` / `uses_only_given_facts` | gaps the model noticed | gaps it did not notice |

None of these can judge whether a sentence is *true*. The design answer is not a cleverer checker but the
surrounding rules: the model never sees mails that go to a human, the category policy flags money, complaints and
capacity regardless, and the output is a draft with a banner — a person is the last step.

A failure of the model call degrades to the template draft with a visible `template_fallback` flag and the reason,
because losing a batch to an outage is worse than a stiff draft that a human reviews anyway. Errors that are not
the SDK's own (a real bug) are *not* swallowed.

## Idempotency and drafts

Re-running over the same inbox is the normal way to "catch up", so drafts are keyed by a source id derived from
the `Message-ID` (or the file content when there is none). Maildir: the id is part of the file name, written via
`tmp/` and an atomic rename so a reader never sees half a message. IMAP: a header search for `X-Triage-Source-Id`
before `APPEND`, flag `\Draft`.

## What would come next

- Run `eval --backend claude` on a larger labelled set, per category, with cost and latency per mail.
- Reply quality measured by people: side-by-side ratings of template vs model drafts on real mails, plus how often
  the reviewer edits or discards a draft — the number that decides whether the model drafter earns its cost.
- A second, independent model pass that must point to the fact line behind each claim in the draft.
- IMAP polling and a calendar-backed slot list instead of a JSON file.
- Per-category confidence thresholds learned from the labelled set instead of one global 0.45.
