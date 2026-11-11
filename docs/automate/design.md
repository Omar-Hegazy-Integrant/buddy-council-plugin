# Planning `/bc:automate` and the name check

What we've decided so far, in plain words. We decide with the team one round of questions at a time, and this
file is updated after each round. What's still open is on the [map][20].

- **Last updated:** after Round 5, 11 Nov 2026. Next: Round 6, questions Q21 to Q28.
- **Q numbers** match the questions in the team's rounds.
- **Words** like *Walk*, *Test Case* and *Automated Test* are defined in the [glossary](../../GLOSSARY.md).

## What we're building

- **`/bc:automate`.** You give it a TestRail folder. For each Test Case in it, the plugin follows the steps on a
  phone (the Walk), notes how to find each element it touches, and writes page objects and one Automated Test in
  the project's own style. Then it checks the new test: it must compile, pass lint, and run once on the phone.
- **The name check.** Before a full run of a project's tests, it checks that every Test Case's title in TestRail
  matches its Automated Test's name, and lists every mismatch ([#55][55]).
- **Both run in CI too**, with no one there to answer questions ([#52][52], [#53][53], [#54][54]).
- **It only automates.** Anything wrong is reported, never fixed: a case that doesn't match the app, a title that
  drifted, an app bug. No edits to TestRail, no renamed tests, no changes to the app.

## The example used below

A made-up Test Case, **C1234 "Check the Welcome screen"**, for iOS and Android:

1. The Welcome screen shows the title "Welcome", a "Get Started" button, and "Version x.x.x".
2. Tap "Get Started". The Terms screen opens.
3. Tap Back. The Welcome screen shows again.

## Starting a run ([#37][37])

- **What you give it** (Q1): a TestRail link to a folder or to one case, case IDs (`C1234 C1235`), or a folder's
  path typed by name. A folder includes its subfolders. Before anything happens, the plan shows how many cases it
  found.
- **Which suite** (Q2): always the suite in the project's settings. A link to another suite stops the run and
  says how to switch the project to it.

## Linking a test to its case ([#30][30])

- **The case ID is the link** (Q3). A title is only a hint, because titles change. Some projects' own reporting
  matches results to cases by exact title. That doesn't matter here: the plugin posts its own results by case ID
  ([ADR 0005](../adr/0005-plugin-posts-its-own-test-run-results.md)).
- **Where the ID goes** (Q4): the project's own way, else its tools' usual way, else a one-line comment. Example:
  with Allure, `@TmsLink("C1234")` next to the test's title. Setup learns which way the project uses.
- **Tests people already wrote** (Q5, Q7):
  - A test whose name matches the case's title exactly is that case's test, so the case counts as automated.
  - Near matches are listed in the plan, to confirm all at once, with the likely pairs already ticked. Example:
    the case "Check the Welcome screen" and the test "Check Welcome screen". Nothing is linked by guessing.
  - Linking only adds the case ID to the test, and the plan shows that change first.
  - In CI, with no one to confirm, near matches are only reported.
- **Titles that drifted** (Q8): a run renames nothing. Each drifted title is reported with a suggested fix, for a
  person to make.
- **TestRail stays as it is** (Q6): for now the plugin writes nothing to TestRail; the case ID lives in the test's
  code. Once `/bc:vnv-sprint-prep` checks a case's content instead of its last-updated time ([#56][56]), the plugin
  can also mark cases as automated in TestRail.

## Platforms ([#27][27])

- **One platform per run** (Q9): the connected phone's, or the one you name. CI always names it.
- **What the Walk does** (Q9): it follows the case's steps on the phone, notes how to find each element (its
  locator), taps or types to reach the next screen, and checks each expected result.
- **One test for both platforms** (Q10): each case gets one test, shared by iOS and Android. What differs between
  them goes in the page objects.
- **Until the other platform is walked** (Q11): the test is marked for iOS only, and Android's locators say
  `"TODO"`. The report says "iOS only so far". A later Android run fills in the `TODO`s, removes the mark, and
  checks the test on Android. It adds only Android's locators and code, so the iOS test keeps working.
- **Cases for one platform** (Q12): the plugin reads the platform from TestRail's platform field. If that's empty,
  it looks for a tag in the title, like "(iOS)"; if there's none, it assumes both. An empty field is always
  reported. The plugin's own case writer will fill the field in ([#57][57]).
- **Order** (Q13): the whole folder on one platform first, then the other, starting with the platform that's ready.

## Proving a new test ([#38][38])

- **Done means** (Q14): it compiles, passes the project's lint, and runs once on the phone. Once is enough while
  we're mapping cases to tests. What the run finds is reported.
- **The Walk is the reference** (Q15). It ran every step on the same app build minutes before. So when the new
  test fails at a step the Walk passed, the fault is in the test's code, such as a locator or a wait. The plugin
  fixes it and runs it once more. If it still fails, the test isn't added, and the report says why.
- **Every failure gets a root cause** (Q15): the test's code; the app not matching the case (an app bug or an
  outdated case); or the phone (an unexpected system popup, Bluetooth off, a changed setting). A phone problem is
  never counted as an app or test problem.
- **When the app doesn't match the case** (Q16):
  - *The Walk can go on.* Example: step 1 expects the title "Welcome", but the app shows "Hello". The Walk notes the
    failed check and carries on with steps 2 and 3. The test still checks for "Welcome", as the case says, so it
    fails. It's added marked "known to fail on iOS", and the report asks: app bug, or outdated case?
  - *The Walk can't go on.* Example: tapping "Get Started" shows an error instead of the Terms screen. No test is
    written for this case. It's reported as blocked at step 2, with a screenshot.
  - Checks of what a screen shows don't stop the test, so the later steps still run. Checks that the next screen
    opened do stop it. Every failed check is reported. A later run removes the "known to fail" mark once the test
    passes.
- **No results posted** (Q17): while it proves a test, the plugin turns off the project's own TestRail reporting.

## When a case fails ([#62][62])

- **An element can't be found** (Q19), even after waiting, scrolling, or closing a popup that changes nothing:
  - If the next steps need it, the case is blocked there. Example: the "Get Started" button is missing, so the
    Terms screen can't be reached. The case is reported with its root cause and a screenshot, and the run moves on
    to the next case from a fresh start.
  - If the step only checks it, it's a failed check and the Walk goes on, as in Q16. Example: "Version x.x.x" is
    missing, and steps 2 and 3 still run.
- **Stopping early** (Q19): only when going on would repeat the same failure. Either the same screen blocks two
  cases in a row (like a sign-in screen many cases share), or the phone or the app can't be brought back (the phone
  is unplugged, or the app won't open). The report then lists what was automated, what wasn't, and why. CI does
  the same.
- **When the phone is the cause** (Q20): it's reported as "the phone", with a screenshot. The plugin doesn't change
  phone settings or answer permission prompts on its own; it only closes a system popup that changes nothing, and
  says so. It retries the case once from a fresh start. If the phone still blocks it, the case is reported
  "blocked: the phone". If every case would be blocked, the run stops.
- **Putting the phone back** (Q20): a test whose steps change the phone puts it back in its teardown. Example: a
  test turns Bluetooth off, so its teardown turns it on again, and the next test starts from the same phone.

## Tests people already wrote ([#37][37])

- **A test that covers only part of its case** (Q18): the plan lists the case's steps that have no code. Example:
  the existing test for C1234 only checks step 2. The plugin walks the case and adds steps 1 and 3 to that test, on
  a separate branch with its own pull request, apart from the new tests. That way the people who wrote the test
  review the change.

## Still open: Round 6

| Q | Question | Ticket |
|---|---|---|
| Q21 | If a step checks for something the plugin can't find on the screen, what should the new test do? | [#62][62] |
| Q22 | When a test changes a phone setting, what should it put back at the end? | [#62][62] |
| Q23 | When the plugin finishes a test a person wrote, what goes in the separate pull request? | [#28][28] |
| Q24 | How does the plugin know a person edited a file it wrote? | [#28][28] |
| Q25 | Where should the values a step types come from? | [#31][31] |
| Q26 | How should passwords and other secrets be kept safe? | [#31][31] |
| Q27 | How should a test find an element that has no test ID? | [#32][32] |
| Q28 | How should the app team learn which elements need a test ID? | [#32][32] |

Everything else that's not decided yet is on the [map][20].

## Research

- [What a hook can allow for the phone's actions](../research/issue-22-hook-approvals.md) ([#22][22])
- [How Appium projects usually link tests to TestRail cases](../research/issue-24-testrail-test-links.md)
  ([#24][24])
- [Whether the plugin can read an app build's string files](../research/issue-25-app-string-files.md) ([#25][25])
- Research on the pilot Test Project stays private, with that project.

Related decisions: [Test Projects own their test environment](../adr/0003-test-projects-own-their-test-environment.md),
[the Screen Map is shared in git, and the plugin offers the pull request](../adr/0004-screen-map-in-git-pull-request-offered.md),
and [the plugin posts its own test run results](../adr/0005-plugin-posts-its-own-test-run-results.md).

## How we decide

1. Each round, the team gets a set of questions in its chat, each with a recommended answer and an example.
2. The team answers by number: agree, disagree, or a question.
3. The answers go on each question's GitHub ticket, and this file is updated after the round.

[20]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/20
[22]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/22
[24]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/24
[25]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/25
[27]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/27
[28]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/28
[30]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/30
[31]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/31
[32]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/32
[37]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/37
[38]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/38
[52]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/52
[53]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/53
[54]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/54
[55]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/55
[56]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/56
[57]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/57
[62]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/62
