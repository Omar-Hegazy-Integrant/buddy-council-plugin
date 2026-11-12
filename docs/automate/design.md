# Planning `/bc:automate` and the name check

What we've decided so far, in plain words. We decide with the team one round of questions at a time, one level at
a time, in the order `/bc:automate` works. This file is updated after each round.

- **Last updated:** 12 Nov 2026. Now: Round 6, redone, finishing Level 1 (Q29 to Q35).
- **Q numbers** match the questions in the team's rounds.
- **Words** like *Automate*, *Test Run*, *Walk* and *Trial* are defined in the [glossary](../../GLOSSARY.md).

## Where we are

| Level | What happens | Settled | Still open |
|---|---|---|---|
| 1. Linking cases to tests | Read the cases from TestRail, find the test each one already has, and plan what to automate | Q1–Q9, Q12, Q13, Q18 | Q29–Q35, now in Round 6 |
| 2. The Walk | Follow each case's steps on a phone, and note how to find each element | Q16, Q19, Q20 | Q22, Q25–Q27, approving taps ([#29][29]), the phone work |
| 3. Writing the code | Write page objects and one Automated Test per case | Q10, Q11, Q16, Q18, Q20 | Q21, Q22, Q24 |
| 4. The Trial | The new test compiles, passes lint, and runs once on the phone | Q14, Q15, Q17 | |
| 5. Report and pull requests | What the plugin reports at the end, and the pull requests it offers | | Q23, Q28, the report, the pull-request offer |
| 6. The name check | A separate command, before Test Runs: does each case's title match its test's name? | | [#55][55] |
| 7. CI | Both commands, with no one there to answer | | [#52][52], [#53][53], [#54][54] |

Before Level 1, setup learns how the Test Project works ([#33][33]).

## Two phases, and where a test can fail

- **Automate** (Levels 1 to 5) is everything `/bc:automate` does to give a Test Case its Automated Test: it finds
  the test the case already has, or writes a new one.
- **A Test Run** comes later: the Automated Tests run on an app build, with a result for each case in TestRail.
  `/bc:automate` never starts one.

So "fails" always says where: **during the Walk** (Level 2), **in its Trial** (Level 4), or **in a Test Run**.

## What we're building

- **`/bc:automate`.** You give it a TestRail folder. For each Test Case in it, the plugin follows the steps on a
  phone (the Walk), notes how to find each element it touches, and writes page objects and one Automated Test in
  the project's own style. Then it tries the new test (its Trial): the test must compile, pass lint, and run once on
  the phone.
- **The name check.** Before a Test Run of a project's tests, it checks that every Test Case's title in TestRail
  matches its Automated Test's name, and lists every mismatch ([#55][55]).
- **Both work in CI too**, with no one there to answer questions ([#52][52], [#53][53], [#54][54]).
- **It only automates.** Anything wrong is reported, never fixed: a case that doesn't match the app, a title that
  drifted, an app bug. No edits to TestRail, no renamed tests, no changes to the app.

## The example used below

A made-up Test Case, **C1234 "Check the Welcome screen"**, for iOS and Android:

1. The Welcome screen shows the title "Welcome", a "Get Started" button, and "Version x.x.x".
2. Tap "Get Started". The Terms screen opens.
3. Tap Back. The Welcome screen shows again.

## Level 1 · Linking cases to tests

Nothing touches the phone yet. `/bc:automate` reads the cases, finds each one's test, and shows a plan.

### Starting ([#37][37], [#27][27])

- **What you give it** (Q1): a TestRail link to a folder or to one case, case IDs (`C1234 C1235`), or a folder's
  path typed by name. A folder includes its subfolders. Before anything happens, the plan shows how many cases it
  found.
- **Which suite** (Q2): always the suite in the project's settings. A link to another suite stops it, with how to
  switch the project to that suite.
- **One platform at a time** (Q9): the connected phone's, or the one you name. CI always names it.
- **Which platforms a case needs** (Q12): the plugin reads TestRail's platform field. If that's empty, it looks for
  a tag in the title, like "(iOS)"; if there's none, it assumes both. An empty field is always reported. The
  plugin's own case writer will fill the field in ([#57][57]).
- **Order** (Q13): the whole folder on one platform first, then the other, starting with the platform that's ready.

### Linking a test to its case ([#30][30])

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
- **Titles that drifted** (Q8): `/bc:automate` renames nothing. Each drifted title is reported with a suggested
  fix, for a person to make.
- **TestRail stays as it is** (Q6): for now the plugin writes nothing to TestRail; the case ID lives in the test's
  code. Once `/bc:vnv-sprint-prep` checks a case's content instead of its last-updated time ([#56][56]), the plugin
  can also mark cases as automated in TestRail.

### A test that covers only part of its case (Q18)

The plan compares each linked test with its case's steps, and lists the steps that have no code. Example: the
existing test for C1234 only checks step 2, so the plan lists steps 1 and 3. Adding them happens in Level 3, and
they go in a pull request of their own (Level 5).

### Still open: Round 6, redone

| Q | Question |
|---|---|
| Q29 | A step checks several things at once. How does the plan handle it? |
| Q30 | A step is too vague to follow. What does the plan do? |
| Q31 | A case needs something the phone can't do on its own. What does the plan do? |
| Q32 | Two cases are the same except for the platform. One test, or two? |
| Q33 | A case changed in TestRail after its test was written. How is that noticed? |
| Q34 | What does the plan show, and what can you change before the Walk? |
| Q35 | A folder has more cases than one session can finish. What happens? |

## Level 2 · The Walk: gathering locators

- **What the Walk does** (Q9): it follows the case's steps on the phone, notes how to find each element (its
  locator), taps or types to reach the next screen, and checks each expected result.
- **When a step doesn't match the app** (Q16):
  - *The Walk can go on.* Example: step 1 expects the title "Welcome", but the app shows "Hello". The Walk notes
    the failed check and carries on with steps 2 and 3.
  - *The Walk can't go on.* Example: tapping "Get Started" shows an error instead of the Terms screen. The case
    stops there, and is reported as blocked at step 2, with a screenshot. No test is written for it.
- **An element can't be found** (Q19), even after waiting, scrolling, or closing a popup that changes nothing:
  - If the next steps need it, the case is blocked there. Example: the "Get Started" button is missing, so the
    Terms screen can't be reached. The case is reported with its root cause and a screenshot, and the Walk moves on
    to the next case from a fresh start.
  - If the step only checks it, it's a failed check, and the Walk goes on. Example: "Version x.x.x" is missing,
    and steps 2 and 3 still happen.
- **Stopping early** (Q19): only when going on would repeat the same failure. Either the same screen blocks two
  cases in a row (like a sign-in screen many cases share), or the phone or the app can't be brought back (the phone
  is unplugged, or the app won't open). The report then lists what was automated, what wasn't, and why. CI does
  the same.
- **When the phone is the cause** (Q20): it's reported as "the phone", with a screenshot. The plugin doesn't change
  phone settings or answer permission prompts on its own; it only closes a system popup that changes nothing, and
  says so. It walks the case once more from a fresh start. If the phone still blocks it, the case is reported
  "blocked: the phone". If every case would be blocked, `/bc:automate` stops.

Still open: Q22 (putting the phone back between cases), Q25 (the values a step types), Q26 (passwords and other
secrets), Q27 (finding an element that has no test ID), approving taps ([#29][29]), and the phone work we set aside
for now ([#21][21], [#26][26], [#34][34], [#35][35], [#47][47]).

## Level 3 · Writing the code

- **One test for both platforms** (Q10): each case gets one test, shared by iOS and Android. What differs between
  them goes in the page objects.
- **Until the other platform is walked** (Q11): the test is marked for iOS only, and Android's locators say
  `"TODO"`. The report says "iOS only so far". When `/bc:automate` later works on the folder on Android, it fills
  in the `TODO`s, removes the mark, and tries the test on Android. It adds only Android's locators and code, so the
  iOS test keeps working.
- **The test checks what the case says** (Q16). In the "Hello" example from Level 2, the test still checks for
  "Welcome", so it fails. It's added marked "known to fail on iOS", and the report asks: app bug, or outdated
  case? When `/bc:automate` works on the folder again, it tries each marked test again and removes the mark from
  those that pass, and walks the blocked cases again.
- **Two kinds of checks** (Q16): checks of what a screen shows don't stop the test, so the later steps still run.
  Checks that the next screen opened do stop it. Every failed check is reported.
- **Finishing a test that covers part of its case** (Q18): the plugin walks the case and adds the missing steps to
  that test, so the people who wrote it can review the change in its own pull request.
- **Putting the phone back** (Q20): a test whose steps change the phone puts it back in its teardown. Example: a
  test turns Bluetooth off, so its teardown turns it on again, and the next test starts from the same phone.

Still open: Q21 (checking for something the Walk couldn't find), Q22 (what a test's teardown puts back), Q24
(telling the plugin's files from a person's), and writing the first test ([#36][36]).

## Level 4 · The Trial ([#38][38])

- **Done means** (Q14): the new test compiles, passes the project's lint, and runs once on the phone. Once is enough
  while we're automating cases. What the Trial finds is reported.
- **The Walk is the reference** (Q15). It did every step on the same app build minutes before. So when the test
  fails in its Trial at a step the Walk passed, the fault is in the test's code, such as a locator or a wait. The
  plugin fixes it and tries the test once more. If it still fails, the test isn't added, and the report says why.
- **Every failure gets a root cause** (Q15): the test's code; the app not matching the case (an app bug or an
  outdated case); or the phone (an unexpected system popup, Bluetooth off, a changed setting). A phone problem is
  never counted as an app or test problem.
- **No results posted** (Q17): during a Trial, the plugin turns off the project's own TestRail reporting.

## Level 5 · Report and pull requests

- **Tests people wrote** (Q18): changes to them go in a pull request of their own, apart from the new tests.

Still open: Q23 (what goes in that separate pull request), Q28 (telling the app team which elements need a test ID),
what the report lists and how it's grouped, and the pull-request offer
([ADR 0004](../adr/0004-screen-map-in-git-pull-request-offered.md)).

## 6 · The name check ([#55][55])

Not started.

## 7 · CI

Each level says what it does in CI. The questions only CI raises are open in [#52][52], [#53][53] and [#54][54].

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

1. Each round covers one level, in the order above, and its title names the level.
2. Each question has an example and a recommended answer.
3. The team answers by number: agree, disagree, or a question.
4. The answers go on each question's GitHub ticket, and this file is updated after the round.
5. A question about another level waits for that level's round.

[21]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/21
[22]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/22
[24]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/24
[25]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/25
[26]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/26
[27]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/27
[29]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/29
[30]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/30
[33]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/33
[34]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/34
[35]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/35
[36]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/36
[37]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/37
[38]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/38
[47]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/47
[52]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/52
[53]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/53
[54]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/54
[55]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/55
[56]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/56
[57]: https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/57
