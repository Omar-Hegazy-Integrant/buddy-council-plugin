# Can the plugin read an app build's string files?

Research for [#25](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/25). It feeds [#32, "Which locators may a test use without a test ID?"](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/32).

Checked on 8 and 9 November 2026 on macOS 27.2, with the Mac's own Python (`/usr/bin/python3`, 3.9.6), Xcode 27.2, and the Android SDK's `aapt2` 2.20. The pilot's iOS build was only read, never changed. This page holds its counts and the method, never its text, keys, or file names.

## 1. Answer

- **iOS: yes.** Open the `.ipa` with `zipfile`, without unpacking it, or walk the `.app` folder. Take every `.strings` and `.stringsdict` file. Its folder (`fr.lproj`, `pt-BR.lproj`, …) names the language, and its file name names the table. Read binary and XML files with `plistlib`, and the rare text form with a 75-line parser. Nothing else is needed: no Xcode, no network. String Catalogs (`.xcstrings`) never reach a build; Xcode turns them into `.strings` and `.stringsdict` files. On the pilot's iOS build it takes 0.2 seconds.
- **Android: yes, without the Android SDK.** Open the `.apk` with `zipfile` and decode `resources.arsc` with `struct`: about 130 lines that follow AOSP's `ResourceTypes.h`. For an `.aab`, read `base/resources.pb` with a 100-line protobuf decoder. On 235,512 strings the `.arsc` reader gave the same text as `aapt2 dump resources` for all but one, and that one is a quirk of aapt2's printout. The `.pb` reader matched it on a test bundle. `aapt2` is on this Mac anyway, in `~/Library/Android/sdk/build-tools/36.0.0` and `37.0.0`; `~/.buddy-council/tools/android-sdk` doesn't exist. There is no Android build of the pilot on this Mac, so the Android side rests on docs, source code, and public APKs.
- **The pilot's iOS build gives almost nothing to map.** It has one language and one table of 24 strings, outside any language folder. With so few strings in a table, nearly all its visible text must come from code, compiled screens, or its server, where no key exists. Its own text doesn't change with the phone's language either.
- **One text often has several keys.** In Apple's localized iOS apps, a quarter of the keys share their text with another key, and for 72% of those shared texts the keys get different translations in some other language. About 1 string in 10 is a format string, such as `%@ items` (in the pilot's table, 1 in 3). Text from a server, text built at run time, the user's own data, and text typed straight into code or layouts have no key at all.
- **At run time** a test needs four things: the app's tables for the build under test; the language the app shows (safest: set it with the `appium:language` capability); each platform's rule for a missing translation; and a pattern for each format string. Appium's `mobile: getAppStrings` can fetch the tables, with limits (section 5).

## 2. iOS

### Where translations sit in a build

- Each bundle (the app, each app extension, framework, and resource bundle) has one folder per language, named `<language>.lproj`: `en.lproj`, `pt-BR.lproj`, `zh-Hans.lproj`. `Base.lproj` holds interface files. ([Apple: String Resources][apple-strings], [Apple: Preparing your app's text for translation][apple-prepare], [Apple: Localizing your app][apple-localizing])
- A **table** is one file name across those folders. `Localizable` is the default; code can name others. ([Apple: String Resources][apple-strings], [Apple: Preparing your app's text for translation][apple-prepare])
- A `.strings` file maps key → text. "For the development language, it is common to use the same string for both the key and value, but doing so is not required." ([Apple: String Resources][apple-strings])
- A `.stringsdict` file holds plurals. Each key maps to a format such as `%#@homes@`, and each variable to its `zero`, `one`, `two`, `few`, `many`, and `other` forms; only `other` is required. ([Apple: Localizing strings that contain plurals][apple-plurals]) The lookup searches a `.stringsdict` before the `.strings` file of the same name. ([Apple: Localizing your app][apple-localizing])
- **String Catalogs** (`.xcstrings`) are JSON files in the project. "At build time, these files compile to .strings and .stringsdict files." ([WWDC23, Discover String Catalogs][wwdc23]) Xcode runs `xcstringstool compile` on each catalog ([swift-build `XCStrings.xcspec`, line 26][xcspec]). Its help (Xcode 27.2) lists the outputs `stringsAndStringsdict` (default) and `stringsdictOnly`, written as text (an XML plist, the default) or binary.
  - Some catalog entries never reach the development language's table. "Source strings extracted from code are not included in the final build." ([WWDC23, Discover String Catalogs][wwdc23]) In a compile test here, an entry whose text is its own key showed up only in the other languages' tables, and an entry whose source text was still marked `new` showed up nowhere. Since a missing key shows as the key itself (below), a reader should treat such a key as its own development-language text. In the 226 Apple iOS apps with two or more language folders, this adds 736 keys, 1.9% more than their development-language tables hold.
- **Storyboards and XIBs** keep their development-language text inside themselves. "An app has just one set of .storyboard and .xib files where strings are in the development language." ([Apple: Internationalizing the user interface][apple-base]) Each other language gets a `.strings` file of the same name ([Apple: Localizing your app][apple-localizing]), keyed by object ID and property (such as `abc-12-xyz.text`). In the build they become compiled `.nib` files, a binary "NIBArchive" format that no standard library reads. In the 17 Apple iOS apps that have such keys, only 216 of 459 (47%) also have a development-language entry.
- **Info.plist text**, such as permission purpose strings, is translated in `InfoPlist.strings`. ([Apple: Localizing your app][apple-localizing]) The permission alert's title and buttons come from the system. ([Apple: Requesting access to protected resources][apple-purpose])
- **Form on disk:** for iOS, Xcode 27.2 sets `STRINGS_FILE_OUTPUT_ENCODING` to `binary` (`iPhoneOS.platform/Info.plist`, `DefaultProperties`); the setting otherwise defaults to UTF-16 text. ([Apple: Build settings reference][apple-bsr], "Strings File Output Encoding"; [swift-build `CopyStringsFile.xcspec`, lines 43–46][copystrings]) In Apple's own iOS 26.5 simulator apps, 48,366 of 48,419 `.strings` files and all 6,025 `.stringsdict` files are binary plists; 53 are UTF-8 text. None of those apps holds an `.xcstrings` or `.loctable` file.

### Which language shows, and what a missing key shows

- iOS walks the user's preferred languages in order and picks the first one the app has an `.lproj` folder for. It falls back from a regional variant to the plain language (`en-GB` to `en`). If nothing matches, it uses the app's development region, `CFBundleDevelopmentRegion`. ([Apple QA1828][qa1828], [Apple: CFBundleDevelopmentRegion][apple-devregion])
- A key missing from the chosen language's table comes back as **the key itself**, or as the default value the code passed in. ([Apple: `localizedString(forKey:value:table:)`][apple-lsfk]) A small test app on this Mac showed the same: run in French, a key missing from `fr.lproj` showed the key, not the English text.
- The app's language can differ from the phone's. Xcode's scheme has an "App Language" option ([Apple: Testing localizations][apple-testing]), and Appium's `appium:language` capability passes `-AppleLanguages` to the app ([XCUITest driver `startup.ts`, lines 364–368][xcui-startup]).

### Reading each form with Python 3.9's standard library

| Form | How |
|---|---|
| `.ipa` | `zipfile.ZipFile(path).read(name)` reads one file out of the archive, with no unpacking. ([Python: zipfile][py-zipfile]) |
| `.app` folder | `os.walk` |
| binary plist (starts with `bplist00`) | `plistlib.loads(data)`, which reads binary and XML plists. ([Python: plistlib][py-plistlib]) |
| XML plist | `plistlib.loads(data)`. Some are UTF-16 with a header that says UTF-8. `plistlib`'s XML parser (expat) refuses those, so re-encode them to UTF-8 first. |
| text form (`"key" = "value";`) | `plistlib` can't read it. A 75-line parser (Appendix A) handles comments, escapes, `\U` escapes, and UTF-8 or UTF-16. `/usr/bin/plutil -convert json` reads it too, but that is a macOS tool, not Python. |
| `.xcstrings` | `json`, but only a project holds them, never a build. |
| compiled `.nib` | No standard-library reader. |

The reader in Appendix A gave the same result as `plutil -convert json` on every `.strings` and `.stringsdict` file in this Mac's apps: 22,602 files (17,762 binary, 3,265 UTF-16 XML, 1,575 text). Both refused the same 19 broken files.

### Measured on the pilot's iOS build

Run with `/usr/bin/python3` (3.9.6), straight from the 170 MB `.ipa`, in 0.2 seconds.

| | Count |
|---|---|
| Language folders (`.lproj`) | 0 |
| Languages listed in `Info.plist` (`CFBundleLocalizations`) | 0; only a development region |
| String tables | 1, outside any language folder, a binary plist |
| Strings | 24 |
| Plural entries (`.stringsdict`) / String Catalogs | 0 / 0 |
| Keys that look like text (contain a space) | 0 of 24; all are dotted or underscored names |
| Distinct texts | 22 |
| Texts with more than one key | 2, with 2 keys each (4 keys, 17%); each pair in the same table |
| Format strings | 8 of 24 (33%): eight `%@` and two `%lu`; 2 strings have two placeholders; none is numbered (`%1$@`) |
| Compiled screens | 7 compiled views and 1 compiled storyboard (9 `.nib` files), none in a language folder; their text is inside them |
| Permission purpose strings in `Info.plist` | 3, with no `InfoPlist.strings` |

So the pilot's iOS app is a one-language app. With only 24 strings in a table, nearly all its visible text must come from code, compiled screens, or its server, and has no key in the build. With no `.lproj` folder to match, iOS always uses the development region ([Apple QA1828][qa1828]), so the app's own text stays the same whatever the phone's language.

For comparison, Apple's own iOS 26.5 simulator apps that are localized (107 apps with 2 or more languages and 20 or more strings) have a median of 53 languages and 92 development-language strings each, 37,519 in all.

## 3. Android

### Where translations sit in a build

- In the project, strings live in `res/values/strings.xml` (the default) and in `res/values-<qualifier>/` folders: `values-fr`, `values-pt-rBR`, and, since Android 7.0, BCP 47 tags such as `values-b+sr+Latn`. ([Android: App resources overview][and-providing]) The kinds are `<string>`, `<string-array>`, and `<plurals>`. ([Android: String resources][and-strings])
- `aapt2` compiles them and links one binary **resource table** into the APK, `resources.arsc` at its root. ([Android: AAPT2][and-aapt2]) In an App Bundle (`.aab`), each module holds `resources.pb`, the same table as protobuf. Google Play builds per-language "configuration APKs" from it. ([Android: App Bundle format][and-aab]; `--proto-format` in [Android: AAPT2][and-aapt2])
- The layout of `resources.arsc` is in [AOSP `ResourceTypes.h`][rt-h]:
  - a table header (line 895), then one **value string pool** that holds every string value, in UTF-8 or UTF-16 (line 461);
  - each package (line 908), with its own pools of type names and **key names**;
  - for each type (`string`, `plurals`), one `ResTable_type` chunk per configuration (line 1451). Its `ResTable_config` holds the language, region, script, and variant, plus any other qualifier (lines 956–1210);
  - in each entry, the key and a `Res_value` (line 287). A string is `TYPE_STRING`, an index into the value pool. A plural is a "complex" entry whose items are named `ATTR_OTHER` through `ATTR_MANY` (lines 1644–1673).
- Newer builds pack entries tighter: **sparse** chunks (`FLAG_SPARSE`), **16-bit offsets** (`FLAG_OFFSET16`), and **compact** entries (`FLAG_COMPACT`, key and value in 8 bytes) (lines 1451–1600). Three-letter language codes are packed into 15 bits ([`ResourceTypes.cpp`, `unpackLanguageOrRegion`, line 2044][rt-cpp]). The SDK's platform 37 `android.jar` uses all three: 4,030 sparse chunks, 240 offset16 chunks, and 251,632 compact entries.

### Which text shows

- For each string, Android picks the best-matching `values-<locale>` folder. "When the app runs in any locale that you haven't provided locale-specific text for, Android loads the default strings from `res/values/strings.xml`"; if the default lacks the string too, the app doesn't run. ([Android: Localize your app][and-l10n]) So a missing translation shows the default text, where iOS shows the key.
- Since Android 7.0 it also tries the parent language and its other regions (`fr_CH`, then `fr`, then `fr_FR`). ([Android: Language and locale resolution][and-resolution])
- Since Android 13, a user can pick a language per app, and an app can set its own. ([Android: Per-app language preferences][and-applang])

### Reading it with Python 3.9's standard library

- `zipfile` reads `resources.arsc` out of the APK, and `struct` decodes it. Appendix B is the whole reader, about 130 lines: UTF-8 and UTF-16 pools; sparse, offset16, and compact entries; packed language codes; and plurals. It skips `<string-array>`, a complex entry whose items are numbered (`Res_MAKEARRAY`, line 278), and strings that only point at another string (`TYPE_REFERENCE`, line 302).
- For an `.aab`, Appendix C reads `base/resources.pb` with a 100-line protobuf decoder. The field numbers come from AOSP's [`Resources.proto`][res-proto] (`Entry` line 216, `Item` line 276, `Plural` line 550) and [`Configuration.proto`][cfg-proto] (`locale`, line 141).
- Both were checked against `aapt2 dump resources` (aapt2 2.20, build-tools 37.0.0):

| Input | Strings | Same as aapt2 |
|---|---|---|
| The SDK's platform 37 `android.jar` (102 locales; sparse, offset16, and compact) | 233,924 | 233,923 * |
| The Appium Settings APK (85 locales) | 1,550 | 1,550 |
| Three test APKs built here with aapt2: plain; sparse; names collapsed | 13, 13, 12 | all |
| The test resources as a protobuf table (`resources.pb`), read by Appendix C | 15, with plurals | all, against Appendix B |

\* The one difference is in the comparison, not the reader: that value has a quote mark just before a line break, which aapt2's printout doesn't escape.

- `android.jar`'s 19 MB table reads in about half a second.
- `aapt2` isn't needed. Appium's own reader calls `aapt`, then `aapt2`, on the Appium machine (section 5).

### What can't be read back

- **Collapsed names.** `aapt2 optimize --collapse-resource-names` gives every resource the same name. The test APK came back with every string named `0_resource_name_obfuscated`. A team has to choose this: the Android Gradle plugin's own optimize step leaves names alone ([`OptimizeResourcesTask.kt`, lines 161 and 177–188][agp-optimize]).
- **Text that isn't a resource:** literal text in a layout or in code.
- **Languages that aren't in the file:** a build can leave languages out, and an app installed from Google Play holds only the language splits the phone needs ([Android: App Bundle format][and-aab]).

### Measured (no pilot Android build on this Mac)

Counting only configurations that set nothing but a locale:

| | `android.jar` | Appium Settings APK |
|---|---|---|
| Locales besides the default | 102 | 85 |
| Default strings | 2,908 | 25 |
| Default texts with more than one name | 148 of 2,400 (6.2%), covering 656 names (22.6%) | 0 |
| …that translate differently in some locale | 129 (87%) | none |
| Format strings among the default strings | 286 (9.8%) | 6 (24%) |
| Share of the default strings the median locale translates | 85% | 76% |

## 4. Reliability

### One text, several keys

| Build | Keys whose text another key also has | Texts whose keys translate differently elsewhere |
|---|---|---|
| The pilot's iOS build | 4 of 24 (17%), in 2 texts | none: it has no other language |
| Apple's iOS apps (107) | 25.5% (median 14.3% per app); 11.4% of distinct texts | 72% (2,566 of 3,583 texts) |
| `android.jar`, default strings | 22.6%; 6.2% of distinct texts | 87% (129 of 148) |

- When the keys translate the same everywhere, either key gives the right text. When they don't, a test built on the wrong key passes in one language and fails in another.
- On iOS the table name rarely settles it: in 63% of Apple's ambiguous texts, all the keys sit in the same table.
- A second clue does settle it: the same element's text in a second language, or the key the app's code uses for that element.
- An Android string can point at another string (`@string/ok`; `TYPE_REFERENCE` in [AOSP ResourceTypes.h][rt-h], line 302). Both names then show the same text.

### Format strings and plurals

- About 1 string in 10 has a placeholder: 9.5% in Apple's iOS apps (median 6.8% per app) and 9.8% in `android.jar`. In the pilot's table, 33%.
- The screen shows the filled-in text, so a test must match a pattern, not the exact string.
- Placeholders can be numbered and reordered in a translation (`%2$@ … %1$@`). ([Apple: String Resources][apple-strings])
- A plural picks one of its forms at run time, by the number and the language's rules. ([Apple: Localizing strings that contain plurals][apple-plurals], [Android: String resources][and-strings]) In Apple's iOS apps, 3.9% of entries are plurals.

### Text that can't map at all

- Text from a server: it isn't in the build.
- Text built at run time: the value filled into a format string ([Android: String resources][and-strings], "Formatting strings"), strings joined in code, and numbers, dates, and currencies formatted for the region ([Apple: Preparing dates, currencies, and numbers][apple-formatters]).
- The user's own data: names, entries, account details.
- Text typed straight into code or a layout instead of going through a localization API. A SwiftUI literal such as `Text("Save")` is a lookup key ([Apple: Preparing your app's text for translation][apple-prepare]); with no table entry, the key itself shows, so it looks the same in every language.
- iOS storyboard and XIB text in the development language, which sits inside compiled `.nib` files.
- Text the system draws: the permission alert's title and buttons ([Apple: Requesting access to protected resources][apple-purpose]), and system parts such as text menus and date pickers ([Apple QA1828][qa1828]).
- Text in images.
- Android names collapsed by an optimizer.

## 5. At run time

### A locator built from a key

The test names the key, never the text, and turns it into text when it runs. In Java (the pilot's language), with `strings` a small helper that holds the app's tables for the app's language, and made-up keys:

```java
// iOS, exact text. `label` is the element's accessibility label.
String save = strings.text("Localizable", "button.save");                  // table + key
driver.findElement(AppiumBy.iOSNsPredicateString("label == " + quoted(save)));

// iOS, a format string: "Welcome, %@! You have %lu messages." becomes "Welcome, *! You have * messages."
driver.findElement(AppiumBy.iOSNsPredicateString("label LIKE " + quoted(likePattern(format))));

// Android, exact text and a format string as a regular expression
driver.findElement(AppiumBy.androidUIAutomator("new UiSelector().text(" + javaString(save) + ")"));
driver.findElement(AppiumBy.androidUIAutomator("new UiSelector().textMatches(" + javaString(regex(format)) + ")"));
```

- `label` is XCTest's label. `name` is the accessibility identifier when there is one, else the label. ([XCUITest driver: element attributes][xcui-attrs]) An app can set the accessibility label apart from the visible text; then the table text won't match it.
- NSPredicate's `LIKE` takes `?` and `*` as wildcards, and `MATCHES` takes an ICU regular expression. ([Apple: Predicate format string syntax][apple-predicate])
- `UiSelector.textMatches(regex)` matches the element's whole visible text against a regular expression. ([Android: UiSelector][and-uiselector])
- A plural matches any of its forms in that language.
- The literal parts need escaping: quote marks, `*`, `?`, and `\` in `LIKE`, and regular-expression characters in a regex.

### What the test needs to turn a key into text

1. **The app's tables for the build under test**, since keys and texts change between builds. Either the test fetches them when it starts, with Appium's `mobile: getAppStrings`, or the plugin writes a key-to-text file from the build.
2. **The language the app shows.** Setting it is safest:
   - XCUITest: `appium:language` and `appium:locale` pass `-AppleLanguages` and `-AppleLocale` to the app ([`startup.ts`, lines 364–368][xcui-startup]; [XCUITest capabilities, lines 38–39][xcui-caps]).
   - UiAutomator2: `appium:language` with `appium:locale` sets the phone's locale through the Appium Settings app ([UiAutomator2 README, lines 133–135][u2-readme]; [android-driver `common.ts`, lines 257–258][and-common]).
   - Reading it is weaker. iOS's `mobile: deviceInfo` returns the phone's locale (`currentLocale`), not the app's choice ([WebDriverAgent `FBCustomCommands.m`, lines 539–543][wda-info]), and the app's language also depends on which `.lproj` folders it has ([Apple QA1828][qa1828]). On Android, Appium reads the phone's locale ([appium-adb `device-settings.ts`, lines 344–348][adb-lang]), but a per-app language can differ ([Android: Per-app language preferences][and-applang]).
3. **The platform's rule for a missing translation.** iOS uses the best `.lproj` match, else the development region; a key missing there shows as the key itself, or the code's default. Android uses the best `values-<locale>` for each string, else the default.
4. **A pattern for each format string and plural** (above).

### Appium's `mobile: getAppStrings`, as shipped with appium-mcp 1.99.2

| | iOS (XCUITest driver 12.22.8) | Android (UiAutomator2 8.7.3, android-driver 14.2.5, appium-adb 16.1.2) |
|---|---|---|
| Needs | The `app` capability: a `.app` or `.ipa` path on the Appium machine. Without it, it fails. ([`app-strings.ts`, lines 30–36][xcui-strings]) | The `app` capability, or `appPackage`, whose APK it copies from the phone ([`resources.ts`, lines 86–105][and-resources]); and `aapt` or `aapt2` on the Appium machine |
| Reads | Every `.strings` and `.stringsdict` in `<language>.lproj` of the main app bundle only; it unzips an `.ipa` to a temporary folder first (lines 44–90) | The output of `aapt d --values resources`, or of `aapt2 d resources` if that fails ([`apk-utils.ts`, lines 425–475][adb-apk]) |
| Language | An argument; `en` by default ([execute methods, lines 2227–2241][xcui-exec]) | An argument; the phone's language by default ([`resources.ts`, line 21][and-resources]) |
| Limits | Throws when `<language>.lproj` doesn't exist (lines 53–64), so it can't read the pilot's table, which sits outside any language folder. Merges every table into one map, so the same key in two tables collides (line 106). Plurals come back as JSON text (line 149). No fallback to the development language. | Keeps only that language's strings, with no fallback to the default (lines 620 and 634 for `aapt2`; `aapt` did the same on a test APK), so the test must fall back itself. The `aapt2` path misreads styled strings: on a test APK, a styled string swallowed the string after it. |

appium-mcp 1.99.2 itself has no tool that calls `mobile: getAppStrings` (no match in its `dist/` folder). So a Walk can't get the tables through it; the plugin reads the build itself, as in sections 2 and 3.

## How this was checked

- **iOS reader:** compared with `plutil -convert json` on every `.strings` and `.stringsdict` file in this Mac's apps, Xcode left out: 22,602 files, all the same. Both refused the same 19 files.
- **Pilot:** read in place from its `.ipa` with `/usr/bin/python3`. The script printed counts only.
- **Apple's iOS apps:** the 245 apps in the iOS 26.5 simulator runtime were read with the same reader; 107 have 2 or more languages and 20 or more development-language strings. A key is its table plus its name, so the same table copied into several bundles counts once. The development language is the folder that matches `CFBundleDevelopmentRegion`.
- **String Catalogs:** two small catalogs were compiled with Xcode 27.2's `xcstringstool compile`, and the output read with `plistlib`.
- **Missing keys on iOS:** a tiny app bundle with English and French tables, run on this Mac with `-AppleLanguages`.
- **Android readers:** compared with `aapt2 dump resources` (2.20) on the SDK's platform 37 `android.jar` (the one input with compact entries), the Appium Settings APK that ships with the UiAutomator2 driver, and test APKs built here with `aapt2 link` (plain; `--enable-sparse-encoding --enable-compact-entries`, which gave sparse chunks; then `aapt2 optimize --collapse-resource-names`) and `aapt2 link --proto-format`.
- **Appium's Android parser:** `parseAaptStrings` and `parseAapt2Strings` from the installed appium-adb, run on the test APK's `aapt` and `aapt2` printouts.
- **What the measures mean:** "texts with more than one key" counts development-language (iOS) or default (Android) entries whose exact text another key also has. "Translate differently" means at least one other language gives those keys different texts. A format string holds at least one placeholder such as `%@`, `%d`, `%1$s`, or `%#@…@`.
- All scratch files lived outside the repository and were deleted.

## Appendix A: iOS reader (`zipfile`, `plistlib`, and a text-form parser)

`iter_tables("/path/to/App.ipa")` yields `(bundle, language, table, kind, entries)`; `language` is `None` for a table outside any language folder.

<details><summary>final_ios.py (Python 3.9, standard library only)</summary>

```python
"""Read the string tables in an iOS build (.ipa or .app) with Python's standard library only."""
import os
import plistlib
import posixpath
import re
import zipfile

_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
            '"': '"', "'": "'", "\\": "\\"}
_BARE = re.compile(r"[A-Za-z0-9_$+/:.\-]+")


def _decode(data):
    for bom, codec in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"), (b"\xef\xbb\xbf", "utf-8")):
        if data.startswith(bom):
            return data[len(bom):].decode(codec)
    if data[1:2] == b"\x00":
        return data.decode("utf-16-le")
    if data[0:1] == b"\x00":
        return data.decode("utf-16-be")
    return data.decode("utf-8")


def parse_text_strings(text):
    """The text form: "key" = "value"; with /* */ and // comments (plistlib can't read it)."""
    out, i, n = {}, 0, len(text)

    def skip():
        nonlocal i
        while i < n:
            if text[i].isspace():
                i += 1
            elif text.startswith("/*", i):
                end = text.find("*/", i + 2)
                i = n if end < 0 else end + 2
            elif text.startswith("//", i):
                end = text.find("\n", i)
                i = n if end < 0 else end + 1
            else:
                return

    def token():
        nonlocal i
        if text[i] != '"':
            m = _BARE.match(text, i)
            if not m:
                raise ValueError("unexpected character at %d" % i)
            i = m.end()
            return m.group(0)
        i += 1
        buf = []
        while i < n and text[i] != '"':
            c = text[i]
            if c == "\\" and i + 1 < n:
                e = text[i + 1]
                if e in _ESCAPES:
                    buf.append(_ESCAPES[e])
                    i += 2
                elif e in "Uu" and re.match(r"[0-9a-fA-F]", text[i + 2:i + 3]):
                    m = re.match(r"[0-9a-fA-F]{1,4}", text[i + 2:i + 6])
                    buf.append(chr(int(m.group(0), 16)))
                    i += 2 + len(m.group(0))
                elif e in "01234567":
                    m = re.match(r"[0-7]{1,3}", text[i + 1:i + 4])
                    buf.append(chr(int(m.group(0), 8)))
                    i += 1 + len(m.group(0))
                else:
                    buf.append(e)
                    i += 2
            else:
                buf.append(c)
                i += 1
        if i >= n:
            raise ValueError("unterminated string")
        i += 1
        # \U escapes may be UTF-16 surrogate pairs; join them
        return "".join(buf).encode("utf-16-le", "surrogatepass").decode("utf-16-le")

    skip()
    while i < n:
        key = token()
        skip()
        if i < n and text[i] == ";":  # "key"; means key = key
            out[key] = key
        else:
            if i >= n or text[i] != "=":
                raise ValueError("expected '=' at %d" % i)
            i += 1
            skip()
            out[key] = token()
            skip()
            if i >= n or text[i] != ";":
                raise ValueError("expected ';' at %d" % i)
        i += 1
        skip()
    return out


def read_table(data):
    """A .strings or .stringsdict file in any form Xcode writes -> (dict, form)."""
    if data.startswith(b"bplist00"):
        return plistlib.loads(data), "binary plist"
    try:
        return plistlib.loads(data), "XML plist"
    except plistlib.InvalidFileException:
        pass  # not a plist at all: the text form
    except Exception:
        # UTF-16 XML whose header says UTF-8: Apple's reader takes it, expat refuses it
        text = _decode(data)
        if not text.lstrip().startswith(("<?xml", "<plist")):
            raise
        text = re.sub(r'^(\s*<\?xml[^>]*encoding=["\'])[^"\']*', r"\1UTF-8", text, count=1)
        return plistlib.loads(text.encode("utf-8")), "XML plist (UTF-16)"
    return parse_text_strings(_decode(data)), "text"


_LPROJ = re.compile(r"(?:^|/)([^/]+)\.lproj/([^/]+)$")
_BUNDLE = re.compile(r"\.(app|appex|framework|bundle)$")


def iter_tables(path):
    """Yield (bundle, language, table, kind, entries) for every table in an .ipa or .app.

    language is the .lproj folder name, or None for a table outside any language folder.
    kind is "strings" or "stringsdict". Reads an .ipa in place, without unpacking it.
    """
    if os.path.isdir(path):
        names = [os.path.relpath(os.path.join(r, f), path).replace(os.sep, "/")
                 for r, _d, fs in os.walk(path) for f in fs]

        def read(name):
            with open(os.path.join(path, name), "rb") as fh:
                return fh.read()
    else:
        z = zipfile.ZipFile(path)
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        read = z.read
    for name in names:
        base, ext = posixpath.splitext(posixpath.basename(name))
        if ext not in (".strings", ".stringsdict"):
            continue
        m = _LPROJ.search(name)
        language = m.group(1) if m else None
        parts = name.split("/")
        bundle = "/".join(p for p in parts[:-1] if _BUNDLE.search(p))
        entries, _form = read_table(read(name))
        yield bundle, language, base, ext[1:], entries
```

</details>

## Appendix B: Android `resources.arsc` reader (`zipfile` and `struct`)

`strings_from("/path/to/app.apk")` yields `(package, type, name, locale, other, value)`. `locale` is written like a folder qualifier (`""` for the default, `fr`, `pt-rBR`, `b+sr+Latn`); `other` is true when the configuration also sets something besides the locale, such as night mode or screen size.

<details><summary>final_arsc.py (Python 3.9, standard library only)</summary>

```python
"""Read string resources from an Android resources.arsc with Python's standard library only.

Layout: AOSP frameworks/base libs/androidfw/include/androidfw/ResourceTypes.h.
"""
import struct
import zipfile

_PLURAL = {0x01000004: "other", 0x01000005: "zero", 0x01000006: "one",
           0x01000007: "two", 0x01000008: "few", 0x01000009: "many"}


def _pool(buf, off):
    """ResStringPool chunk -> list of str (UTF-8 or UTF-16)."""
    _t, hsize, _s, count, _styles, flags, start, _ = struct.unpack_from("<HHIIIIII", buf, off)
    offsets = struct.unpack_from("<%dI" % count, buf, off + hsize)
    utf8, out = bool(flags & 0x100), []
    for o in offsets:
        p = off + start + o
        if utf8:
            for _ in range(2):  # length in UTF-16 units, then in bytes; 1 or 2 bytes each
                n = buf[p]
                p += 1
                if n & 0x80:
                    n = ((n & 0x7F) << 8) | buf[p]
                    p += 1
            end = p + n
            if end < len(buf) and buf[end] != 0:  # old tools truncated long lengths
                end = bytes(buf[p:]).index(b"\x00") + p
            out.append(bytes(buf[p:end]).decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", buf, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p)[0]
                p += 2
            out.append(bytes(buf[p:p + 2 * n]).decode("utf-16-le", "replace"))
    return out


def _code(two, base):
    """ResTable_config language/region: two letters, or three packed into 15 bits."""
    a, b = two
    if a & 0x80:
        return "".join(chr(c + ord(base)) for c in (b & 0x1F, ((b & 0xE0) >> 5) | ((a & 0x03) << 3), (a & 0x7C) >> 2))
    return two.decode("ascii").rstrip("\x00") if a else ""


def _locale(buf, cfg):
    """The locale of a ResTable_config, written as a values-<qualifier> folder would be."""
    size = struct.unpack_from("<I", buf, cfg)[0]
    lang = _code(bytes(buf[cfg + 8:cfg + 10]), "a")
    region = _code(bytes(buf[cfg + 10:cfg + 12]), "0")
    script = bytes(buf[cfg + 36:cfg + 40]).rstrip(b"\x00").decode("ascii") if size >= 40 else ""
    variant = bytes(buf[cfg + 40:cfg + 48]).rstrip(b"\x00").decode("ascii") if size >= 48 else ""
    if not lang:
        return ""  # the default values/ folder
    if script or variant:
        return "b+" + "+".join(x for x in (lang, script, region, variant) if x)
    return lang + ("-r" + region if region else "")


def _other_qualifiers(buf, cfg):
    """True when the configuration sets more than a locale (night mode, screen size, network, API level...)."""
    size = struct.unpack_from("<I", buf, cfg)[0]
    spans = ((4, 8), (12, 36), (48, 52))
    return any(any(bytes(buf[cfg + a:cfg + min(b, size)])) for a, b in spans if a < size)


def read_arsc(buf):
    """Yield (package, type, name, locale, other, value) for every string and plural.

    other is True when the value belongs to a configuration that sets more than a locale.
    value is a str for a string, a {quantity: str} dict for a plural.
    A string that points at another resource (@string/other) is skipped.
    """
    buf = memoryview(buf)
    off = struct.unpack_from("<H", buf, 2)[0]  # ResTable_header size
    values = None
    while off < len(buf):
        ctype, hsize, csize = struct.unpack_from("<HHI", buf, off)
        if ctype == 0x0001:  # the table's value string pool
            values = _pool(buf, off)
        elif ctype == 0x0200:  # ResTable_package
            package = bytes(buf[off + 12:off + 268]).decode("utf-16-le").split("\x00")[0]
            type_strings, _last, key_strings = struct.unpack_from("<III", buf, off + 268)
            types, keys = _pool(buf, off + type_strings), _pool(buf, off + key_strings)
            sub = off + hsize
            while sub < off + csize:
                stype, shsize, ssize = struct.unpack_from("<HHI", buf, sub)
                if stype == 0x0201:  # ResTable_type: one configuration of one type
                    tid, tflags, _r, count, start = struct.unpack_from("<BBHII", buf, sub + 8)
                    tname = types[tid - 1]
                    if tname in ("string", "plurals"):
                        locale, index = _locale(buf, sub + 20), sub + shsize
                        other = _other_qualifiers(buf, sub + 20)
                        if tflags & 0x01:  # FLAG_SPARSE: (entry index, offset / 4) pairs
                            offs = [struct.unpack_from("<HH", buf, index + 4 * k)[1] * 4 for k in range(count)]
                        elif tflags & 0x02:  # FLAG_OFFSET16: 16-bit offsets / 4, 0xFFFF = none
                            offs = [o * 4 for o in struct.unpack_from("<%dH" % count, buf, index) if o != 0xFFFF]
                        else:
                            offs = [o for o in struct.unpack_from("<%dI" % count, buf, index) if o != 0xFFFFFFFF]
                        for o in offs:
                            e = sub + start + o
                            esize, eflags = struct.unpack_from("<HH", buf, e)
                            if eflags & 0x0008:  # FLAG_COMPACT: key and value inside the entry
                                name = keys[esize]
                                vtype, data = eflags >> 8, struct.unpack_from("<I", buf, e + 4)[0]
                            else:
                                name = keys[struct.unpack_from("<I", buf, e + 4)[0]]
                                if eflags & 0x0001:  # FLAG_COMPLEX: a plural's quantities
                                    count_items = struct.unpack_from("<I", buf, e + 12)[0]
                                    forms, p = {}, e + esize
                                    for _ in range(count_items):
                                        q, _vs, _r0, vt, vd = struct.unpack_from("<IHBBI", buf, p)
                                        p += 12
                                        if vt == 0x03 and q in _PLURAL:
                                            forms[_PLURAL[q]] = values[vd]
                                    yield package, tname, name, locale, other, forms
                                    continue
                                _vs, _r0, vtype, data = struct.unpack_from("<HBBI", buf, e + esize)
                            if vtype == 0x03:  # TYPE_STRING: an index into the value pool
                                yield package, tname, name, locale, other, values[data]
                sub += ssize
        off += csize


def strings_from(path):
    """resources.arsc from an .apk (or a bare .arsc file)."""
    if path.endswith(".arsc"):
        with open(path, "rb") as fh:
            return read_arsc(fh.read())
    with zipfile.ZipFile(path) as z:
        return read_arsc(z.read("resources.arsc"))
```

</details>

## Appendix C: App Bundle `resources.pb` reader (`zipfile` and a protobuf decoder)

`strings_from("/path/to/app.aab")` yields the same rows as Appendix B, with BCP 47 locales (`pt-BR`, `sr-Latn`).

<details><summary>final_pb.py (Python 3.9, standard library only)</summary>

```python
"""Read string resources from an App Bundle module's resources.pb with Python's standard library only.

Field numbers: AOSP frameworks/base tools/aapt2/Resources.proto and Configuration.proto.
"""
import zipfile

_ARITY = {0: "zero", 1: "one", 2: "two", 3: "few", 4: "many", 5: "other"}


def _fields(b):
    """Yield (field number, value) for one protobuf message; value is int or bytes."""
    i, n = 0, len(b)

    def varint():
        nonlocal i
        shift = result = 0
        while True:
            c = b[i]
            i += 1
            result |= (c & 0x7F) << shift
            if not c & 0x80:
                return result
            shift += 7

    while i < n:
        key = varint()
        wire = key & 7
        if wire == 0:
            value = varint()
        elif wire == 2:
            size = varint()
            value, i = bytes(b[i:i + size]), i + size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, i = bytes(b[i:i + size]), i + size
        else:
            raise ValueError("wire type %d" % wire)
        yield key >> 3, value


def _get(b, number, default=b""):
    for f, v in _fields(b):
        if f == number:
            return v
    return default


def _text(item):
    """Item: 2 = String, 3 = RawString, 4 = StyledString; each holds its text in field 1."""
    for f, v in _fields(item):
        if f in (2, 3, 4):
            return _get(v, 1).decode("utf-8")
    return None  # a reference or another kind of value


def read_pb(pb):
    """Yield (package, type, name, locale, other, value) like read_arsc; locale is BCP-47 ("pt-BR")."""
    for f, package in _fields(pb):
        if f != 2:  # ResourceTable.package
            continue
        pname = _get(package, 2).decode("utf-8")
        for f2, typ in _fields(package):
            if f2 != 3:  # Package.type
                continue
            tname = _get(typ, 2).decode("utf-8")
            if tname not in ("string", "plurals"):
                continue
            for f3, entry in _fields(typ):
                if f3 != 3:  # Type.entry
                    continue
                name = _get(entry, 2).decode("utf-8")
                for f4, cv in _fields(entry):
                    if f4 != 6:  # Entry.config_value
                        continue
                    config = _get(cv, 1)
                    locale = _get(config, 3).decode("utf-8")  # Configuration.locale
                    other = any(f != 3 for f, _v in _fields(config))
                    value = _get(cv, 2)
                    item, compound = _get(value, 4, None), _get(value, 5, None)
                    if item is not None:
                        text = _text(item)
                        if text is not None:
                            yield pname, tname, name, locale, other, text
                    elif compound is not None:
                        plural = _get(compound, 5, None)  # CompoundValue.plural
                        if plural is not None:
                            forms = {}
                            for f5, pe in _fields(plural):
                                if f5 == 1:  # Plural.entry: arity = 3, item = 4
                                    forms[_ARITY[_get(pe, 3, 0)]] = _text(_get(pe, 4))
                            yield pname, tname, name, locale, other, forms


def strings_from(path, module="base"):
    """resources.pb from an .aab module (or a proto-format .apk, or a bare .pb file)."""
    if path.endswith(".pb"):
        with open(path, "rb") as fh:
            return read_pb(fh.read())
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        member = module + "/resources.pb" if module + "/resources.pb" in names else "resources.pb"
        return read_pb(z.read(member))
```

</details>

[apple-strings]: https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/LoadingResources/Strings/Strings.html
[apple-prepare]: https://developer.apple.com/documentation/xcode/preparing-your-apps-text-for-translation
[apple-plurals]: https://developer.apple.com/documentation/xcode/localizing-strings-that-contain-plurals
[apple-localizing]: https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPInternational/LocalizingYourApp/LocalizingYourApp.html
[apple-base]: https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPInternational/InternationalizingYourUserInterface/InternationalizingYourUserInterface.html
[wwdc23]: https://developer.apple.com/videos/play/wwdc2023/10155/
[xcspec]: https://github.com/swiftlang/swift-build/blob/9222a8ade22dead4273fa5f10d3550a988e6c094/Sources/SWBApplePlatform/Specs/XCStrings.xcspec#L26
[copystrings]: https://github.com/swiftlang/swift-build/blob/9222a8ade22dead4273fa5f10d3550a988e6c094/Sources/SWBUniversalPlatform/Specs/CopyStringsFile.xcspec#L43-L46
[apple-bsr]: https://developer.apple.com/documentation/xcode/build-settings-reference
[apple-purpose]: https://developer.apple.com/documentation/uikit/requesting-access-to-protected-resources
[apple-formatters]: https://developer.apple.com/documentation/xcode/preparing-dates-numbers-with-formatters
[qa1828]: https://developer.apple.com/library/archive/qa/qa1828/_index.html
[apple-devregion]: https://developer.apple.com/documentation/bundleresources/information-property-list/cfbundledevelopmentregion
[apple-lsfk]: https://developer.apple.com/documentation/foundation/bundle/localizedstring(forkey:value:table:)
[apple-testing]: https://developer.apple.com/documentation/xcode/testing-localizations-when-running-your-app
[apple-predicate]: https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/Predicates/Articles/pSyntax.html
[py-zipfile]: https://docs.python.org/3.9/library/zipfile.html
[py-plistlib]: https://docs.python.org/3.9/library/plistlib.html
[and-providing]: https://developer.android.com/guide/topics/resources/providing-resources
[and-strings]: https://developer.android.com/guide/topics/resources/string-resource
[and-aapt2]: https://developer.android.com/tools/aapt2
[and-aab]: https://developer.android.com/guide/app-bundle/app-bundle-format
[and-l10n]: https://developer.android.com/guide/topics/resources/localization
[and-resolution]: https://developer.android.com/guide/topics/resources/multilingual-support
[and-applang]: https://developer.android.com/guide/topics/resources/app-languages
[and-uiselector]: https://developer.android.com/reference/androidx/test/uiautomator/UiSelector#textMatches(java.lang.String)
[rt-h]: https://android.googlesource.com/platform/frameworks/base/+/1cdfff555f4a21f71ccc978290e2e212e2f8b168/libs/androidfw/include/androidfw/ResourceTypes.h
[rt-cpp]: https://android.googlesource.com/platform/frameworks/base/+/1cdfff555f4a21f71ccc978290e2e212e2f8b168/libs/androidfw/ResourceTypes.cpp#2044
[res-proto]: https://android.googlesource.com/platform/frameworks/base/+/1cdfff555f4a21f71ccc978290e2e212e2f8b168/tools/aapt2/Resources.proto
[cfg-proto]: https://android.googlesource.com/platform/frameworks/base/+/1cdfff555f4a21f71ccc978290e2e212e2f8b168/tools/aapt2/Configuration.proto#141
[agp-optimize]: https://android.googlesource.com/platform/tools/base/+/2a4d17bb5f15e3604abb9dfd628fa3265bcc1895/build-system/gradle-core/src/main/java/com/android/build/gradle/internal/tasks/OptimizeResourcesTask.kt#161
[xcui-strings]: https://github.com/appium/appium-xcuitest-driver/blob/v12.22.8/lib/commands/app-strings.ts#L30-L36
[xcui-startup]: https://github.com/appium/appium-xcuitest-driver/blob/v12.22.8/lib/commands/wda/startup.ts#L364-L368
[xcui-exec]: https://github.com/appium/appium-xcuitest-driver/blob/v12.22.8/docs/reference/execute-methods.md#L2227-L2241
[xcui-attrs]: https://github.com/appium/appium-xcuitest-driver/blob/v12.22.8/docs/reference/element-attributes.md
[xcui-caps]: https://github.com/appium/appium-xcuitest-driver/blob/v12.22.8/docs/reference/capabilities.md#L38-L39
[u2-readme]: https://github.com/appium/appium-uiautomator2-driver/blob/v8.7.3/README.md#L133-L135
[and-resources]: https://github.com/appium/appium-android-driver/blob/v14.2.5/lib/commands/resources.ts#L86-L105
[and-common]: https://github.com/appium/appium-android-driver/blob/v14.2.5/lib/commands/device/common.ts#L257-L258
[adb-apk]: https://github.com/appium/appium-android/blob/dd7fac6576be570d54de8ff2874b88426b4446dd/packages/adb/lib/tools/apk-utils.ts#L425-L475
[adb-lang]: https://github.com/appium/appium-android/blob/dd7fac6576be570d54de8ff2874b88426b4446dd/packages/adb/lib/tools/device-settings.ts#L344-L348
[wda-info]: https://github.com/appium/appium-ios/blob/1da0348a440d0fa66f1b4b65d0334a9d164bc5ab/packages/webdriveragent/WebDriverAgentLib/Commands/FBCustomCommands.m#L539-L543
