# How do Appium projects link Automated Tests to TestRail cases?

Research for issue #24. It feeds the decision in #30, "How is a Test Case linked to its Automated Test?".

Checked on 2026-11-08 against TestRail's documentation, the TestRail CLI (trcli) 1.15.3, the reporters' own code
and docs, and this plugin at `main` (commit 3d980ff). Numbers in brackets point to the **Sources** list at the
end. `[P1]` and the like are files in this repo.

Words: a **Test Case** is a written test in TestRail. An **Automated Test** is code in a Test Project that carries
out one Test Case. TestRail shows a case's ID as `C1234`.

## 1. Answer

- **Appium has no TestRail link of its own.** The link lives in the test framework (TestNG, JUnit, WebdriverIO,
  pytest) and in the tool that sends results to TestRail. There are two families [6][7][13]:
  1. **The case ID is written in the test code.** It sits in the test's name, in an annotation, marker or tag, or
     in a property of the JUnit report. TestRail calls this *specification-first*.
  2. **The test's ID is written in TestRail.** trcli's `automation_id` case field holds the test's
     `classname.name` from the JUnit report. TestRail calls this *code-first*. It is trcli's default [19].
- **When the cases exist first, TestRail recommends the first family** [6]. An ID in code survives moving the
  test, renaming it as long as the ID stays [7], and moving the case (section 4). `automation_id` breaks when a
  test is renamed or moved, and trcli with `-y` then creates a duplicate case [6][13]. With WebdriverIO's default
  JUnit report it even changes with the phone, the OS version, and the app build's file name (surprise 2).
- **TestRail takes results only by case ID**, or by the ID of that case's test inside a run [4]. Any other link is
  turned into case IDs by the tool before it posts [14].
- **No field on a new TestRail site holds a test's ID.** An admin must add `automation_id` on each site [6][18].
  The "Automation Type" dropdown exists by default since TestRail 5.5, but it only marks a case. It names no
  test [9][10].
- **The plugin can read every way.** IDs in code: by searching the Test Project for the project's own pattern,
  and exactly from the JUnit report of one run. TestRail fields: with the connector's read tools [P1].
- **Writing is easy in code, and guarded in TestRail.** The plugin can put the case ID in any new test it writes,
  with no TestRail change. It can also write a case field such as `automation_id` today (`custom_fields` on
  `testrail_update_case`) [P1]. But that must go through `howtos/update-test-cases.md`, the one updater, and none
  of its four paths covers it yet [P2].
- **Write IDs in the `C1234` form.** trcli and the WebdriverIO TestRail reporter read `C1234`; trcli also reads a
  bare `1234` in a `test_id` property. Neither reads the plugin's own `TC-1234` form. This was checked by running
  trcli's own parsers [15][16][27]. pytest-testrail takes any trailing digits, so it reads both [31].

## 2. The ways

| Way | What ties the test to the case | What reads it back | What breaks it | How common |
|---|---|---|---|---|
| **A. Case ID in the test's name**: `C1234_signsIn()`, `it('[C1234] signs in')`, `test_c1234_signs_in` | The case's number, written in the test's name or title | trcli `--case-matcher name` reads it from the JUnit report [13 L402-420][15]. The WebdriverIO TestRail reporter reads every `C\d+` in the `it()` title [27]. A team's own listener | The ID left out after a rename. The case deleted: trcli reports "Nonexistent case IDs" [14 L145-236]. The case copied, for example into a new baseline: the ID still names the original [11]. An ID only in JUnit 5's `@DisplayName`, which Surefire leaves out of the report by default [32][22]. The plugin's `TC-1234` form. It survives moving the test and moving the case | Common. TestRail's spec-first guide shows it first [7]. TestRail's own Cypress and TestNG samples use it [22][23]. The WebdriverIO TestRail reporter reads titles this way (33,933 npm downloads a month) [27][36] |
| **B. Case ID in an annotation, marker, or tag**: `@TestRail(id = "C1234")`, `@pytestrail.case('C1234')`, a Cucumber tag, a team's own annotation | An annotation on the test that holds the ID | The annotation's own reporter or listener. TestRail's JUnit 5 extension writes it as a `test_id` report property for trcli [23]. pytest-testrail posts by marker when run with `--testrail` [31]. A team's own TestNG listener posts with the API by case ID [4][34]. The WebdriverIO TestRail reporter reads Cucumber tags with `caseIdTagPrefix` [27] | The case copied or deleted. An annotation nothing reads: TestRail's JUnit 5 extension works for TestNG only on the JUnit Platform engine, which drops TestNG's own HTML reports [23]. It survives renaming and moving the test | Common in Python: pytest-testrail has 103,365 PyPI downloads a month [35]. In Java the annotation is often the team's own; no public number counts those |
| **C. Case ID as a report property**: `<property name="test_id" value="C1234"/>` | A property the test adds to its JUnit report entry | trcli `--case-matcher property` [13 L422-445][16 L100-176] | As B. pytest still writes the property under its default `xunit2` report family, but warns [30]. WebdriverIO's own example, `addProperty('test_case', 'TC-1234')`, uses a name and a form trcli doesn't read [24][16] | Less common: each test, or a hook, must add it [29][24] |
| **D. Reporter settings**: trcli's `--case-matcher`, `-y` or `-n`; `reporters` in `wdio.conf`; pytest's `--testrail`; a JUnit listener file | Nothing by itself. It says which of A, B, C, or E the project uses, and whether a test run posts to TestRail | — | Changing the matcher changes which way applies. trcli's default is `auto`, meaning E [19] | Every project that reports to TestRail has some |
| **E. trcli's `automation_id`** (code-first) | A case field `automation_id` that holds the test's `classname.name` from the JUnit report [13 L366-391][16 L267-286] | trcli `--case-matcher auto`, the default. It reads every case in the suite and compares the strings itself [14 L84-142]. TestRail does no matching on it [4] | Renaming the test, or moving it to another class, package, or file: a new `classname.name`, and with `-y` a duplicate case [6][13 L391]. In WebdriverIO, a new phone, OS version, or app build file (surprise 2) [24][26]. A copied case in the same suite carries the same value, and trcli keeps only the last case it reads [14 L116][11]. A value wrapped in `<p>` tags (fixed in trcli 1.13.2) [21]. It survives moving the case and editing its title | Common where tests came before cases. trcli has 178,590 PyPI downloads a month [35] and uses E by default [19]. TestRail itself says to use A, B, or C when the cases exist first [6] |
| **F. Feature name equals case title** (trcli's Cucumber mode) | A Cucumber feature's name equals the title of a TestRail case that uses the BDD template [13 L975, L1129] | trcli `parse_cucumber` | Renaming either side. Two cases with the same title (a warning only) | Only for BDD-template cases. The plugin writes steps-template cases [P3] |
| **G. A TestRail field that marks the case**: "Automation Type", the case type "Automated", labels, a team's own field | Nothing. It says a case is automated, not by which test | People and filters in TestRail. The plugin's canonical reader shows such fields by option name [P1 L782-840][P5] | Nothing links, so nothing breaks. The mark goes stale when a test is deleted | TestRail adds "Automation Type" by default since 5.5 [9][10]. How many teams fill it in is unknown |

Download counts include CI installs. They show reach, not how many teams use a way. No source counts how private
Test Projects link their tests.

### By stack

- **Java with TestNG.** Maven Surefire writes the class's full name as `classname` and the method name as `name`
  [22]. So `C1234_signsIn()` works with trcli's name matcher, as in TestRail's own TestNG sample [23]. TestRail's
  `@TestRail(id = "C1234")` annotation works with TestNG only on the JUnit Platform engine [23]. Java teams also
  write their own annotation plus a TestNG listener that posts results with TestRail's API, by case ID [4][34].
- **Java with JUnit 5.** The same, but a `C1234` written only in `@DisplayName` does not reach the report. Surefire's
  "phrased name" options default to false [32]. TestRail's own JUnit 5 sample report shows the method name, even
  though the test has a `@DisplayName` [22].
- **WebdriverIO.** The TestRail reporter reads every `C\d+` in an `it()` title, and Cucumber tags with
  `caseIdTagPrefix` [27]. The JUnit reporter turns punctuation in a title into spaces: `[C1234] signs in` becomes
  `C1234 signs in`, which trcli still reads [25]. Its class name starts with the device's capabilities (surprise
  2) [24][26]. `addProperty('test_id', 'C1234')` adds a report property [24].
- **pytest.** pytest-testrail reads `@pytestrail.case('C1234', 'C5678')` and posts when run with `--testrail`
  [31]. `record_property('test_id', 'C1234')`, or a hook that turns a marker into one, adds the property trcli
  reads [29]. TestRail's pytest sample sets `junit_family=legacy` [22].
- **Robot Framework.** trcli reads `- testrail_case_id: C1234` from a test's documentation, or builds
  `automation_id` from the suite path and the test name [20].
- **Allure.** `@TmsLink("C1234")` in Java [33], `addTestId()` in WebdriverIO [28], and `allure.testcase()` in Python
  only draw a link in the Allure report. On their own they send nothing to TestRail.

## 3. What the plugin could read from code, and what it could write

| Way | Read from the Test Project | How reliable | Write | Watch out |
|---|---|---|---|---|
| **A. ID in the name** | Search test files for the project's ID pattern next to each test's declaration. Or read the names in a run's JUnit report | High when names are written out in the code. When names are built at run time (data-driven tests, Cucumber outlines), only the report is exact | Easy for a new test: it is part of writing the test. For an existing test it means renaming hand-written code (#28, ADR 0004) | Match the project's exact form. trcli takes any word that is `c` plus digits: `test_checkout_c2_total` reads as case 2 [15] (checked by running it) |
| **B. Annotation, marker, tag** | Search for the annotation the project's existing tests use (`@pytestrail.case(`, `@TestRail(`, the team's own) | High. The annotation sits on the test, and its ID is almost always written out | Add the same annotation to the new test. For an existing test, an edit of hand-written code | A team's own annotation is only a link if its listener runs. Check the listener is registered (D) |
| **C. Report property** | Readable when the ID is written out (`record_property('test_id', 'C1234')`). When a hook builds it, follow the hook. The report is exact | Medium from code, exact from a report | As B | — |
| **D. Reporter settings** | Readable: `wdio.conf.*`, `pytest.ini`, `conftest.py`, requirements, `pom.xml`, `src/test/resources/META-INF/services/…`, CI scripts, `trcli-config.yml` | High | None. The project owns its settings (ADR 0003 [P8]) | This is how the plugin learns which way a project uses (ADR 0003), and whether the project already posts results (ADR 0005 [P8]) |
| **E. `automation_id`** | The code holds no ID. The value is the report's `classname.name` [13][16]. For Java and pytest it follows from the package, class, and method; pytest adds parameter IDs such as `[3+5-8]` [22]. For WebdriverIO it depends on the capabilities at run time [24][26]. The TestRail side is readable with the connector | Exact only from a run's report | A TestRail write: set the case's `automation_id` before the project's next upload, or trcli `-y` creates a duplicate case [6][13 L390-391]. The value should come from the report of the run that proves the new test (#38) | The field's system name differs per site (section 4) |
| **F. Title match** | The feature name in the code; the title in TestRail | High, but either side can be renamed | None | Not used by the plugin's steps-template cases |
| **G. Marks** | Read with the connector, option names resolved [P1][P5] | High | A TestRail write through `update-test-cases` (`custom_fields`, `type_id`) [P1][P2] | A write changes the case's `updated_on` (surprise 1) |

What this means for the two questions in #30:

- **Finding a case's existing test.** With A, B, or C: search the Test Project for the case's ID in the project's
  form. A hit means the case is automated. With E: read the case's `automation_id`, and look for that
  `classname.name` in a recent report. With no way at all, there is nothing to read. A match on title or steps is
  a guess, so a person confirms it. That is the same rule the orphan repair uses: propose only on real evidence
  [P2].
- **Tying a new test to its case.** With A, B, or C: write the ID into the new test in the project's form. Nothing
  changes in TestRail. With E: write the case's `automation_id` through `update-test-cases`, using the value from
  the proving run's report. A project with no way has no tool that reads a link. An ID in the test's name or
  annotation, in the `C1234` form, is the one that trcli, the WebdriverIO TestRail reporter, and pytest-testrail all
  read without a TestRail change.

## 4. TestRail's side

| Field | On a new site? | What it holds | The connector reads it | The connector writes it today |
|---|---|---|---|---|
| `automation_id`, "Automation ID". System name `custom_automation_id` or `custom_case_automation_id` [17] | **No.** An admin adds a String or Text field with system name `automation_id` and assigns it to the project [6][13 L366-368]. trcli checks it is active and assigned before it creates cases [18] | The test's `classname.name` | Yes: `testrail_get_case` (raw), `testrail_get_canonical_cases` (by label, text cut at 500 characters), `testrail_get_case_fields` (its definition) [P1] | Yes: `custom_fields` on `testrail_update_case` [P1 L444-509]. Pass the full system name read from `get_case_fields`: the tool turns `automation_id` into `custom_automation_id`, which is the wrong field on a site where it is `custom_case_automation_id` [P1 L469-471]. TestRail drops a wrong name without an error; the tool's read-back reports it and stops the batch [P1 L1890-1895, L2021-2027]. Only through `update-test-cases`, which has no path for it yet [P2] |
| `custom_automation_type`, "Automation Type" (dropdown) | **Yes**, since TestRail 5.5, added for the Ranorex integration [9][10]. It is in the API's `get_case` example [1]. Its options are set per site; read them from `get_case_fields` [2] | Whether, or with what tool, a case is automated. A dropdown stores an option number [1] | Yes, with option names resolved [P1 L698-766] | Yes, the same way, as an option number |
| `type_id`, the case type | The list of types is per site. TestRail's API example lists a type named "Automated" [3] | One type per case | Yes | Yes (`type_id`). But it replaces the case's real type, such as Functional or Regression |
| `labels` | In the current API [1] | Free tags | Raw reads only | **No.** The update tool rejects keys it doesn't know [P1 L488] |
| "AI Automation" field (type 15) and `is_ai_automated` | TestRail Cloud only, 10.2 and later; the flag needs 10.9 [1][2][12] | A script TestRail's own generator wrote and attached to the case | Raw reads only | **No.** It is system-controlled: "cannot be created or modified by an admin" [2] |
| `refs`, "References" | Built in [1] | Requirement references | Yes | Yes. But the plugin keeps it for requirement IDs and Jira keys [P4], and coverage reads it. Not a place for test IDs |

More facts about TestRail's side:

- **Results go in by case ID.** `add_result_for_case/{run_id}/{case_id}` and `add_results_for_cases/{run_id}`
  take case IDs. `add_result` and `add_results` take the case's test inside a run [4]. TestRail never matches a
  result by name or by `automation_id`; trcli does that itself before it posts [14].
- **TestRail can't filter cases by a custom field** [1]. To find the case whose `automation_id` is X, the plugin
  reads the whole suite and compares the values itself. The connector already does this for the requirement field,
  with a 10-minute cache [P1 L620-640].
- **Moving a case keeps its ID.** A move is an update of the same case: `update_case` with a new `section_id`, or
  `move_cases_to_section` [1]. So links by case ID survive a move. The plugin's update tool refuses to move cases
  [P1 L456].
- **Copying a case makes a new case with every field value**, results and history included. Copies into a
  baseline work the same way [11]. An ID in code then points at the original only. An `automation_id` now sits on
  two cases. TestRail suggests keeping one case and running it under a configuration per OS, and copying it only
  when the steps differ [11].
- **The system name prefix changed.** Fields created on TestRail 9.0 or later get the `custom_case_` prefix
  [2][5]. trcli looks for both `custom_automation_id` and `custom_case_automation_id` [17]. The plugin resolves
  its requirement field once per site, in setup, for the same reason: a wrong name fails silently [P3 L53-66].
  Nothing in setup looks for an automation field yet [P1 L1121].

## 5. Surprises

1. **Marking a case in TestRail stops V&V refreshes.** Any write to a case changes its `updated_on`.
   `/bc:vnv-sprint-prep` phase 7 refreshes a V&V case only while `updated_on` still equals what the plugin
   recorded. Otherwise it reports "edited since generated" and leaves the case alone [P2 L151-156]. So if
   `/bc:automate` marks a V&V-created case automated, or sets its `automation_id`, that case stops being refreshed.
   Affects #30.
2. **In WebdriverIO, `automation_id` changes with the phone and the app build.** The JUnit reporter's default class
   name starts with the capabilities: the platform, device name, OS version, and app file name [24][25][26]. With
   trcli's code-first way, the same test on iOS and on Android, on a new phone, or on a new build is a different
   test, and `-y` creates a new case for each. A project avoids this by setting `packageName` or
   `classNameFormat` [24], or by giving each test its own `automation_id` property [13 L380-387]. Affects #27
   and #30.
3. **Running tests "the project's own way" can write to TestRail.** The WebdriverIO TestRail reporter posts on
   every run whose `wdio.conf` lists it [27]. pytest-testrail posts when `--testrail` is passed [31]. trcli with
   `-y` in a run script creates cases for tests it can't match [13 L359-363]. The two proving runs must keep this
   off, and so must the sample test's run, whose result ADR 0003 says is never posted [P8]. Affects #38 and #26.
4. **The plugin's own ID forms miss the ecosystem's form.** Tools write `C1234`. The plugin's canonical ID is
   `TC-1234`, which trcli and the WebdriverIO reporter don't read [15][16][27]. Setup's default `id_patterns` is
   `TC-\d+`, which won't find `C1234` in a Test Project's code [P6]. Affects #33 and #36.
5. **One Automated Test may need two case IDs.** The plugin writes V&V cases per platform, with `[iOS]` and
   `[Android]` title prefixes [P7]. A test that runs on both platforms then belongs to two cases. Every format
   above allows several IDs on one test: `[C1, C2]` in a name, `test_id` "C1, C2" [13], "C1 C2" in a WebdriverIO
   title [27], and `case('C1', 'C2')` in pytest-testrail [31]. One trap: WebdriverIO's JUnit reporter strips the
   brackets and commas from a title [25], and trcli's name matcher then reads only the first ID (checked by running
   its parser) [15]. Affects #27 and #30.
6. **TestRail Cloud has its own "automated" flag, and the plugin can't set it.** It belongs to TestRail's built-in
   script generator [2][12]. Affects #30, if it wants to mark cases automated.
7. **TestRail's sample projects and JUnit 5 extension moved** from `gurock` to `sembi-oss` on GitHub. The originals
   are archived. trcli itself is still at `gurock/trcli`.

## Sources

TestRail documentation (support.testrail.com and testrail.com, read 2026-11-08):

1. Cases API: `get_case`, `get_cases`, `add_case` field types, `update_case`, `move_cases_to_section` —
   https://support.testrail.com/hc/en-us/articles/7077292642580-Cases
2. Case Fields API: field type IDs, including 15 "AI Automation"; the `custom_case_` prefix since 9.0 —
   https://support.testrail.com/hc/en-us/articles/7077281158164-Case-Fields
3. Case Types API —
   https://support.testrail.com/hc/en-us/articles/7077295487252-Case-Types
4. Results API: `add_result_for_case`, `add_results_for_cases` —
   https://support.testrail.com/hc/en-us/articles/7077819312404-Results
5. Configuring custom fields —
   https://support.testrail.com/hc/en-us/articles/7373850291220-Configuring-custom-fields
6. TestRail CLI: Code-first workflow —
   https://support.testrail.com/hc/en-us/articles/12609674354068-Code-first-workflow
7. TestRail CLI: Specification-first workflow —
   https://support.testrail.com/hc/en-us/articles/12609869124116-Specification-first-workflow
8. TestRail CLI: JUnit to TestRail mapping —
   https://support.testrail.com/hc/en-us/articles/12989737200276-JUnit-to-TestRail-mapping
9. Integrating with Ranorex, step 3 "Setup Automation Type field" —
   https://support.testrail.com/hc/en-us/articles/35657627478036-Integrating-with-Ranorex
10. TestRail 5.5 release announcement —
    https://www.testrail.com/blog/announcing-testrail-5-5-release-with-ranorex-integration-gdpr-admin-ui-and-performance-enhancements/
11. Moving, copying, deleting and restoring test cases —
    https://support.testrail.com/hc/en-us/articles/7101747563028-Moving-copying-deleting-and-restoring-test-cases
12. Getting started with Automate Test Cases with AI (TestRail Cloud) —
    https://support.testrail.com/hc/en-us/articles/43587086897428-Getting-started-with-Automate-Test-Cases-with-AI

trcli 1.15.3 (github.com/gurock/trcli, tag `v1.15.3`):

13. README: L176 `--update-existing-cases`; L351-447 the two ways to match (L359-363: `-y` creates cases it can't
    match); L975 and L1129 Cucumber feature names —
    https://github.com/gurock/trcli/blob/v1.15.3/README.md?plain=1#L351-L447
14. `trcli/api/case_matcher.py`: L84-142 the `automation_id` matcher (L116 keeps the last case per value);
    L145-236 the case ID check; L268 `auto` means `automation_id` —
    https://github.com/gurock/trcli/blob/v1.15.3/trcli/api/case_matcher.py#L84-L142
15. `trcli/data_classes/data_parsers.py` L13-74, the name parser —
    https://github.com/gurock/trcli/blob/v1.15.3/trcli/data_classes/data_parsers.py#L13-L74
16. `trcli/readers/junit_xml.py`: L100-176 name, property, and `test_id` parsing; L267-286 `classname.name` and its
    override —
    https://github.com/gurock/trcli/blob/v1.15.3/trcli/readers/junit_xml.py#L100-L176
17. `trcli/constants.py` L53-58 and L193-195, the two field names —
    https://github.com/gurock/trcli/blob/v1.15.3/trcli/constants.py#L193-L195
18. `trcli/api/api_request_handler.py` L128-160, the field check —
    https://github.com/gurock/trcli/blob/v1.15.3/trcli/api/api_request_handler.py#L128-L160
19. `trcli/commands/results_parser_helpers.py` L41-46, the default matcher —
    https://github.com/gurock/trcli/blob/v1.15.3/trcli/commands/results_parser_helpers.py#L41-L46
20. `trcli/readers/robot_xml.py` L123-133 and L195-202 —
    https://github.com/gurock/trcli/blob/v1.15.3/trcli/readers/robot_xml.py#L123-L133
21. CHANGELOG, 1.13.2 —
    https://github.com/gurock/trcli/blob/v1.15.3/CHANGELOG.MD?plain=1#L118-L123

TestRail's sample projects and JUnit extension (moved to `sembi-oss`; the `gurock` originals are archived):

22. automation-frameworks-integration at 36ef0f3 —
    TestNG report: https://github.com/sembi-oss/automation-frameworks-integration/blob/36ef0f317a2bbc62a2b2bb3b91592e946f3362f5/samples/java/testng/sample_reports/junitreports/TEST-com.idera.testrail.tests.SumTests.xml#L4 ·
    JUnit 5 test and report: https://github.com/sembi-oss/automation-frameworks-integration/blob/36ef0f317a2bbc62a2b2bb3b91592e946f3362f5/samples/java/junit5/src/test/java/com/idera/testrail/tests/SubtractionTests.java#L11 and https://github.com/sembi-oss/automation-frameworks-integration/blob/36ef0f317a2bbc62a2b2bb3b91592e946f3362f5/samples/java/junit5/sample_reports/TEST-junit-jupiter.xml#L65 ·
    pytest report and settings: https://github.com/sembi-oss/automation-frameworks-integration/blob/36ef0f317a2bbc62a2b2bb3b91592e946f3362f5/samples/python/pytest/sample_reports/junit-report.xml and https://github.com/sembi-oss/automation-frameworks-integration/blob/36ef0f317a2bbc62a2b2bb3b91592e946f3362f5/samples/python/pytest/pytest.ini#L2 ·
    Cypress spec-first: https://github.com/sembi-oss/automation-frameworks-integration/blob/36ef0f317a2bbc62a2b2bb3b91592e946f3362f5/samples/javascript/cypress-specfirst/README.md?plain=1#L7-L31 ·
    WebdriverIO code-first: https://github.com/sembi-oss/automation-frameworks-integration/blob/36ef0f317a2bbc62a2b2bb3b91592e946f3362f5/samples/javascript/webdriverIO/README.md
23. testrail-junit-extensions at bb75c28 —
    README L95-125: https://github.com/sembi-oss/testrail-junit-extensions/blob/bb75c280a75090938e7b9cc4343b9c5f0c31a947/README.md?plain=1#L95-L125 ·
    TestNG sample: https://github.com/sembi-oss/testrail-junit-extensions/blob/bb75c280a75090938e7b9cc4343b9c5f0c31a947/samples/testng-sample/README.md ·
    https://github.com/sembi-oss/testrail-junit-extensions/blob/bb75c280a75090938e7b9cc4343b9c5f0c31a947/samples/testng-sample/src/test/java/test/example/MethodNameTest.java ·
    https://github.com/sembi-oss/testrail-junit-extensions/blob/bb75c280a75090938e7b9cc4343b9c5f0c31a947/samples/testng-sample/src/test/java/test/example/PropertyNameTest.java

WebdriverIO (github.com/webdriverio/webdriverio at 57d1fac, and webdriverio-community):

24. `@wdio/junit-reporter` README: L279-357 `classNameFormat` and `packageName`; L421-445 `addProperty` —
    https://github.com/webdriverio/webdriverio/blob/57d1facf3928af17a0828a9aa595689721f5d33b/packages/wdio-junit-reporter/README.md?plain=1#L279-L357
25. `@wdio/junit-reporter` `src/index.ts`: L66-68 and L150-154 test names; L415-451 class and package names —
    https://github.com/webdriverio/webdriverio/blob/57d1facf3928af17a0828a9aa595689721f5d33b/packages/wdio-junit-reporter/src/index.ts#L415-L451
26. `@wdio/reporter` `src/utils.ts` L75-145, `sanitizeCaps` —
    https://github.com/webdriverio/webdriverio/blob/57d1facf3928af17a0828a9aa595689721f5d33b/packages/wdio-reporter/src/utils.ts#L75-L145
27. `@wdio/testrail-reporter` 0.6.0 at 23cb4c7: README L1-12 and L154-158; `src/reporter.ts` L52, L66, L79, L159-168 —
    https://github.com/webdriverio-community/wdio-testrail-reporter/blob/23cb4c7654bc019d534f3df170aaba6764bc90e1/src/reporter.ts#L52
28. `@wdio/allure-reporter` README L163 and L196 —
    https://github.com/webdriverio/webdriverio/blob/57d1facf3928af17a0828a9aa595689721f5d33b/packages/wdio-allure-reporter/README.md?plain=1#L163

pytest:

29. pytest docs, `record_property` —
    https://docs.pytest.org/en/stable/how-to/output.html#record-property
30. `src/_pytest/junitxml.py` at bc14038: L191-197 properties are written; L306-342 the warning; L463-467 the
    default family —
    https://github.com/pytest-dev/pytest/blob/bc14038c406563b18a03e15453a10b9eee969e42/src/_pytest/junitxml.py#L306-L342
31. pytest-testrail at 5801741: README L39-59 and L103-121; `pytest_testrail/plugin.py` L83-98 and L159-174 —
    https://github.com/allankp/pytest-testrail/blob/5801741a1fe9f228b001ef02ad36154ee996eaff/pytest_testrail/plugin.py#L159-L174

Java tooling:

32. Maven Surefire, "Surefire Extensions and Reports Configuration for @DisplayName" —
    https://maven.apache.org/surefire/maven-surefire-plugin/examples/junit-platform.html
33. allure-java at abde55a: `TmsLink.java` L28-45; `ResultsUtils.java` L821-828 —
    https://github.com/allure-framework/allure-java/blob/abde55a3645a55b4f8ef35468c37b8e1ef8f381e/allure-java-commons/src/main/java/io/qameta/allure/TmsLink.java#L28-L45
34. TestRail's API bindings, Java `APIClient` —
    https://github.com/gurock/testrail-api/tree/c020e9c191d4536fcbc5f13e64fc99d09f55b20c/java

Download counts:

35. PyPI, last month as of 2026-11-08: https://pypistats.org/api/packages/trcli/recent and
    https://pypistats.org/api/packages/pytest-testrail/recent
36. npm, 2026-10-09 to 2026-11-07: https://api.npmjs.org/downloads/point/last-month/@wdio/testrail-reporter
    (33,933). For scale: `@wdio/junit-reporter` 1,554,650 and `@wdio/appium-service` 1,572,054 at the same endpoint.

This repo, at 3d980ff:

- **P1** `mcp-servers/testrail-server/server.py`: L444-456 update keys and refused keys; L469-471 `_custom_key`;
  L474-509 the update payload (L488 unknown keys); L620-640 the link index (L623 no custom-field filter);
  L698-766 option names; L782-840 canonical cases (L821 custom fields); L1049-1060 `testrail_get_case`;
  L1063-1077 `testrail_get_case_fields`; L1121 requirement-field hints; L1807-2043 `testrail_update_case(s)`
  (L1890-1895 and L2021-2027 read-back; L1980 conflict check).
- **P2** `howtos/update-test-cases.md`: L10-20 four paths, one updater; L101-122 orphan repair; L138-160 the
  `scenarios` path (L151-156 untouched or edited).
- **P3** `providers/testrail/setup.md` L53-66, authoring and the requirement field.
- **P4** `providers/testrail/fetch.md` L205-215, what `refs` holds.
- **P5** `mcp-servers/testrail-server/tests/test_server.py` L127 and L568, an "Automation Type" dropdown shown by
  option name.
- **P6** `skills/setup/SKILL.md` L156, `config/sources.example.json` L82, `howtos/map-feature-to-code.md` L26 and
  L86: `id_patterns`.
- **P7** `howtos/draft-test-cases.md` L190-197, per-platform title prefixes.
- **P8** `GLOSSARY.md` L59-69; `docs/adr/0003-test-projects-own-their-test-environment.md` L7-8 and L20 (the sample
  test's result is never posted); `docs/adr/0005-plugin-posts-its-own-test-run-results.md` L3 and L13.
