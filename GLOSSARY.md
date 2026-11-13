# Buddy-Council

Buddy-Council checks requirements against test cases and tickets and drafts the fixes. It is growing to automate test cases on a phone.

## Language

### Sources

**Source**:
A kind of data the plugin reads: requirements, test cases, board issues, or design documents.
_Avoid_: data source, input

**Provider**:
The tool a source is read from, such as Jama, TestRail, Jira, or GitHub.
_Avoid_: platform, system, integration

**Site**:
One address of a provider, such as `company.testrail.io`. Keys belong to a site.
_Avoid_: instance, server, host

**Key**:
What a site needs before it lets the plugin in, such as an API key, a personal access token, or a client ID and secret.
_Avoid_: credential, secret

**Link**:
The address of a page in a provider that the user pastes into setup, such as a Jama project, a TestRail suite, or a Jira board. It names the provider and the site.
_Avoid_: URL (alone), board URL

### Running the plugin

**Assistant**:
The AI coding tool the plugin runs in, such as Claude Code, Copilot CLI, Codex, or Cursor.
_Avoid_: runtime, CLI, harness, platform

**Project folder**:
The folder the plugin runs in; it keeps its own links and choices. A project inside a provider is always named with the provider, such as "Jama project".
_Avoid_: project, workspace, repo

**Recap**:
The one summary of everything setup found and chose, shown before it saves.
_Avoid_: summary, save prompt, review

**Plan**:
What a command will do, shown before it changes anything so a person can review and adjust it, such as `/bc:automate`'s list of each Test Case in a folder and what will happen to it.
_Avoid_: preview, mapping outcome

**Command**:
What the user starts by name, such as `/bc:coverage`. Each is a skill only the user can start.
_Avoid_: slash command

**How-to**:
A plain instruction file that a command or agent reads by path, such as `howtos/fetch-requirements.md`. It never loads on its own.
_Avoid_: skill, internal skill

**Finding**:
One problem `/bc:contradiction` reports, such as a test case that expects a different result than its requirement: the exact words from each side, why they conflict, how serious it is, and what to change.
_Avoid_: issue, conflict (alone)

**Slice**:
A share of the requirements and test cases a command checks, small enough for one reader to check at once: a few whole features, or some of the requirements of a big one, each with its test cases — or pairs of items on the same subject from different parts of the Product, or board tickets, each with the requirements and test cases on its subject.
_Avoid_: chunk, batch

**Product**:
Everything the requirements and test cases describe, all features together. A `/bc:contradiction` run on one requirement or feature is compared with the rest of the Product.
_Avoid_: system, project (alone)

**Subject line**:
One line written for each requirement, test case, and board ticket: what it is about, then what it says. *(made-up)* "Session: ends after 5 minutes idle." Code pairs the items whose subject lines share a subject but no Slice, and finds what shares each board ticket's subject.
_Avoid_: summary, gist

**Helper**:
A fresh AI session that checks one Slice for `/bc:contradiction` and saves its Findings.
_Avoid_: worker, agent (alone)

### Stories

**Platform**:
The phone operating system a story targets: iOS or Android.
_Avoid_: OS

### Tests

**Test Case**:
A written test in TestRail: the steps a person follows and the results they expect.
_Avoid_: test (alone), manual test

**Automated Test**:
Code in a Test Project that carries out one Test Case.
_Avoid_: automation test case, automation test

**Automate**:
To give a Test Case an Automated Test, by linking one that already exists or writing a new one.
_Avoid_: map, mapping

**Semi-automated**:
Describes a Test Case whose Automated Test does every step except the ones only a person can do, such as hearing an alert. A person still does those.
_Avoid_: partly automated

**Test Run**:
A set of Test Cases in TestRail, run against one build of the app, with a result for each.
_Avoid_: run (alone), execution

### Test automation

**Test Project**:
A repository of Automated Tests that drive an app on a phone, kept apart from the app's own code.
_Avoid_: automation project, test repo, project

**Test Environment**:
Everything a machine needs before a test project's tests can run: tools, credentials, services, a ready phone, and a build of the app.
_Avoid_: dependencies, prerequisites

**Setup Script**:
A test project's own command that checks its test environment and fixes what it can.
_Avoid_: install script, bootstrap script

**Plugin Tools**:
The programs the plugin installs for itself to drive an app on a phone, kept apart from any test environment.
_Avoid_: automation tools

**Walk**:
Following one Test Case's steps on a phone the way a person would, checking the results it expects and Capturing each screen the steps reach.
_Avoid_: tour, crawl, exploration

**Trial**:
Running a new Automated Test once on a phone, right after it's written, to check that it works. Unlike a Test Run, it records no result in TestRail.
_Avoid_: proof run, proving

**Screen Map**:
What the plugin learns about an app's screens by walking them: how to recognize each screen, how to find its elements, and how to reach it.
_Avoid_: screen files, locator file

**Capture**:
A raw record of one screen as the app showed it, such as its element tree or a screenshot.
_Avoid_: screen file, snapshot
