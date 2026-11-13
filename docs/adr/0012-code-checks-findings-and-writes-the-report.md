# The assistant judges contradictions; code checks each finding and writes the report

`/bc:contradiction`'s report was free text the assistant wrote. Nothing checked that a quote came from the requirement or test case it named, and the fix offer rewrote test cases from those findings. Now the assistant saves each finding as data through `scripts/bc_run.py findings`: the items it compares, each with its exact words, how serious it is, why they conflict, and what to change. Code saves a finding only when every ID is in the run and every quote is in its item, and sends back the rest with the closest text it found, so a misquoted finding is corrected or dropped before anyone reads it. `bc_run.py report` writes the report from the saved findings: it names each finding by what it compares, numbers it, links every item, and lists the test case fixes the command may offer.

## Considered options

- **Have the assistant re-read each quote before the report.** More reads, and still a model's word.
- **Let code check the quotes but keep the assistant's report.** The report could still differ from what was checked.

## Consequences

- A board ticket isn't in the run, so code shows its quote as not checked, and never offers a board finding as a test case fix. *(0.34.0: the run now holds each ticket's key, title, link, and subject line, so code can match it with the items on its subject — still never its text, which only the session read. A quote retyped into the run would make the check a model's word again.)*
- The report is a file in the run, in parts of at most 18 KB, and the assistant shows each part as written.
- Several helpers can save findings at once: each save is a file of its own.
