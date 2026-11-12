# Changelog

## [Unreleased]

### Added

- Server agents report their limits (requests per second, seconds per job, threads, packet size and protocols) when they come online. Before **Start on server**, the Server Agents page checks the job from the Stress page against each selected server. When the job is over a server's limits, a dialog lists what is over, for example "server-hk-01: rate 500 QPS (limit 100), duration 600 s (limit 300)", and offers **Run within limits**, which sends each server the job lowered to its own limits, or **Cancel**. Servers that do not support the job's protocol are skipped, and when none of the selected servers can run it the job is not sent. Agents from older versions report no limits and get the job as before.
- The **Generate Server Node** dialog sets the node's limits: requests per second (default 100), seconds per job (300) and threads (32). They go into the node's `agent.json`, and the bundle's `README.txt` says what they are and how to change them later.
- The server list's tooltip shows each server's limits, or that an older agent does not report them.

### Changed

- When a server refuses a job, the Server Agents page says why, in the interface language, next to the server and once in the log, for example "the rate is over the agent's limit (500 > 100)". It used to show only "error", while the reason appeared only in the server's own output.
- The agent's refusals say by how much a job is over, for example `rate exceeds agent limit: 500 > 100`, and a rate of 0 is reported as such instead of as over the limit. The agent checks its limits when it starts and stops with a clear message if one is not a positive number.
- The high-rate confirmation asks about the rate actually sent, after lowering to the servers' limits.
- Japanese language pack 1.0.10 translates the new messages.

## [1.2.14] - 2026-11-12

### Added

- The Monitor page can now show a server agent instead of this PC: pick it under **Source** at the top right. Its CPU usage, memory, network download and upload, TCP connections and processes appear in the same cards and charts as the local ones, with the same time windows, pause, clear and CSV export. The servers come from the control channel set on the Server Agents page (a Hub or the MQTT relay), and offline servers are marked. This PC keeps being recorded while a server is shown.
- Server agents sample CPU, memory, network, TCP connections and processes once a second and send the last 15 seconds with every heartbeat, which now comes every 2 seconds (`metrics_interval`, default 2; `"metrics": false` turns it off and brings back the 10-second heartbeat). Because each heartbeat repeats the recent samples, the desktop keeps one point per second when a heartbeat or a poll is missed, and drops the repeats. On Linux the agent reads `/proc`, so no new package is needed; the network figures leave out the loopback interface. The Windows agent uses psutil, now bundled into `FlowBench-Agent.exe`. The samples travel inside the heartbeat, so an existing Hub passes them on without being updated.
- An agent deployed before this version does not send these figures; the Monitor page says so and asks to generate the node again and replace the agent on the server. Waiting for the first data, an offline server (the last data received stays on screen) and an unreachable control channel are explained under the toolbar too.

- The setup wizard, the in-app update window and the uninstaller have a new look. They are frameless windows with their own title bar, over an animated background: soft colour glows and network-traffic waves, with packets riding them along the bottom of the window. The outlines of the logo's three ribbons were traced from `app_logo.png`, so the logo can be built ribbon by ribbon while what shows is still the PNG itself. On the welcome page it forms: points of light gather, the three ribbons flow out one after another from the bottom up, and a highlight sweeps across. "FlowBench" is then written in letter by letter. A globe button in the title bar switches between 简体中文 and English, and the button next to it between light and dark; every text, including an error already on screen, follows at once, with a short cross-fade. The windows still start in FlowBench's own language and theme.
- Setup now has four steps: welcome, **Get started**; the install location, where the default folder is filled in, **Browse…** picks another and **Use the default folder** brings it back, with the space needed and free shown underneath (in red when it is short); progress; and done. While installing, the logo fills up like liquid as the progress bar moves; the bar uses the logo's blue-to-cyan colours with a moving sheen, and shows the file being extracted and the percentage. The last page offers **Finish** and **Open FlowBench**; it used to be a "Launch FlowBench now" box and **Finish**. **Back** returns to the welcome page.
- **Update now** in the app still installs on its own and reopens FlowBench, in a compact window with the same look: the logo fills as the update goes, and gets a green tick when it is done. "Waiting for FlowBench to exit" now stays on screen until FlowBench has closed; it used to disappear at once.
- In the uninstaller the logo drains as the files are removed, and the last page shows it faded with a green tick.
- With Windows' **Animation effects** turned off, nothing moves: the logo is shown whole and pages switch at once. Only the parts that move are repainted, at most 60 times a second and less when the window is in the background, and nothing is painted while it is minimised.

### Changed

- The three node bundles (existing Hub, all-in-one and MQTT relay) take the Linux agent's files from one list, which gained `agent/sysmetrics.py`. A test checks that this list, `FlowBench.spec` and `Agent.spec` cover every module the agent imports, and that each extracted bundle starts on its own.
- Japanese language pack 1.0.9 translates the new messages.

### Verified

- Rendered every page of setup, update and uninstall in Chinese and English, light and dark, including the intro frame by frame, an error, a short disk and a finished install. Automated tests cover the traced logo (more than 95 % overlap with the PNG), each animation stage, the liquid fill and drain, the language and theme buttons on every page, the new steps and the automatic update. The Windows build of the installer was not made here.
- Ran a real Hub and agent process on Linux and read them with the desktop's client: one sample per second arrived without gaps or repeats, and the `/proc` readings matched psutil (memory 81.3 % in both, TCP connections 73 against 74, processes 250 against 249). The relay path was checked with a real signed status message and the desktop's message handler, not against the public broker. Looked at the Monitor page in Chinese and English, light and dark, with a live server, an older agent and no servers.

## [1.2.13] - 2026-11-11

### Changed

- The sidebar name bubble has no shadow any more, just its thin outline.
- Messages at the top of the window (InfoBars) now wrap by their real width, breaking long web addresses after a "/", so a long message no longer runs past the window edge and hides its close button. qfluentwidgets wrapped them by character count, which is about twice too wide for Chinese and cannot break an address.
- A failed **Check for Updates** says why in a short sentence (GitHub is limiting requests, no connection, or an unexpected reply) instead of the raw error with its URL, which is kept in the tooltip. The **Open release page** link is part of the message from the start, so the message is sized and placed with it.
- When the GitHub API has used up its hourly limit of 60 requests, the update check reads the newest version from the release page instead, so it still finds a new version; the update then opens the release page in the browser, because only the API lists the installer.
- When popular searches have no install counts yet, the panel now says that jsDelivr's statistics lag about two days instead of just "No install stats yet", which looked broken right after installing a plugin; the tooltip explains where the counts come from. jsDelivr's public data was two days behind when checked.
- The Plugins page keeps the last six searches instead of five, filling both rows of three in the search history.
- Each local plugin on the Plugins page is now a card with the same frame as the marketplace's, so one plugin is not mistaken for the next. The card shows the icon at marketplace size, the name with its version and a coloured state label (Running, Disabled, Load failed, Not loaded), and the author and description underneath. **Reload** and **Remove** sit in one row with icons, followed by a divider and the on/off switch at the right edge, centred in the card and lined up from card to card; they used to be stacked in two uneven rows with the switch's own "On/Off" text. **Remove** is red. A plugin that failed to load shows the first line of its error under its name; it used to land in front of the icon. The list keeps scrolling on its own when many plugins are installed, with the warning above it and the Import, Open folder and Rescan buttons below it staying in place.
- Japanese language pack 1.0.8 translates the new messages.
- The project moved from `Carlown/FlowBench` to `Flowbench-Org/FlowBench`. The update check, the plugin index and plugin downloads (GitHub and jsDelivr), install statistics, publishing and unpublishing, the plugin index's homepages and the README links now use the new address directly instead of relying on GitHub's redirect from the old one. The official plugins are still published by the `Carlown` account, including entries from an older offline copy of the index.

### Verified

- Ran the full test suite and looked at the Plugins page in Chinese and English, light and dark themes, at 900 and 640 px wide, with running, disabled, failed and not-loaded plugins; with 30 plugins the list scrolled to the last card while the buttons below stayed put. Covered the cards, the action row's alignment, the error line, the switch, incremental refresh, scrolling and theme colours with automated tests.

## [1.2.12] - 2026-11-10

### Changed

- With the sidebar collapsed to icons, hovering an item now shows its name at once in a speech bubble that points at the icon, instead of a plain tooltip after a one-second wait. Moving up and down the sidebar moves the bubble along; leaving, clicking, switching to another app or expanding the sidebar hides it. Pages added by plugins and the menu button get the same bubble, in light and dark themes.
- Opening FlowBench again while it is already running (double-clicking the icon, the Start menu, a shortcut) no longer flashes the splash screen. The new launch asks the running copy to bring its window back, on the page it was showing, and exits. The running copy is allowed to come to the front instead of only flashing its taskbar button, and a maximized window comes back maximized instead of at its normal size.
- Popular searches on the Plugins page now rank plugins by real installs over the last 30 days, with the count next to each one; they used to be just the newest plugins. Plugins in the FlowBench repository now download through jsDelivr, the public CDN for GitHub files, whose public download statistics are where the counts come from. GitHub is still the fallback: when jsDelivr is unreachable or still serves an older copy of a plugin, the download comes from GitHub instead and jsDelivr is asked to refresh its copy. Every download is still checked against the SHA-256 in the index.
- The counts are fetched in the background and kept for six hours. Until any plugin has been installed through jsDelivr, the list says "No install stats yet; newest first" and falls back to the newest plugins.
- Popular searches stay visible next to the search history; they used to disappear after the first search. Plugins that cannot be shown because they need a newer FlowBench are left out, since searching for them found nothing.
- The search suggestions panel is fully opaque, so the plugin cards underneath no longer show through it.
- Japanese language pack 1.0.5 translates the new messages.

### Verified

- Launched a second copy while FlowBench was running: it showed no splash, the running copy got the request to bring its window back, and the second copy exited at once. Covered the launch order, the hand-over, foreground permission on Windows and restoring a maximized window with automated tests.
- Checked jsDelivr live: it serves the same bytes as GitHub for a repository file, and the statistics API answers; no plugin has been downloaded through it yet, so the list starts with "No install stats yet". Covered summing counts over commits and tags, saved and offline counts, falling back to GitHub for an unreachable or outdated CDN copy, and the popular list's ranking, display and search with automated tests.
- Checked the sidebar bubble in Chinese light, Chinese dark and English, and the popular searches panel in Chinese and English, light and dark.
- Passed the full automated test suite (163 tests, 1704 subtests).

## [1.2.11] - 2026-11-10

### Changed

- When the in-app update download fails, the dialog says why in plain words (no connection or the download broke off, GitHub refused the file, the file was incomplete or damaged, or it could not be saved) and adds **Open in browser** next to **Retry**. It opens the release page in the default browser and names the installer file to download and run. The technical error is kept in the message's tooltip.
- If the browser cannot be opened, the dialog shows the release page address instead of closing.
- A failed manual **Check for Updates** now has an **Open release page** link, since a browser may get through where the app cannot (for example behind a proxy).
- Japanese language pack 1.0.4 translates the new messages.

### Verified

- Checked the failed-download dialog in Chinese light, Chinese dark, English and Japanese: three equal-width buttons, and the installer name on its own line.
- Covered error sorting, Retry, opening the release page, a browser that will not open, the full Update now fallback and the failed manual check link with automated tests.
- Passed the full automated test suite (138 tests, 1688 subtests).

## [1.2.10] - 2026-11-04

### Changed

- `FlowBench-Uninstall.exe` now opens the same Fluent window as the setup wizard instead of plain message boxes: confirm (with the install location), a progress bar that names each file being removed, and a done page. It follows FlowBench's own language, light/dark theme and accent colour, falling back to the Windows language.
- The uninstaller can also delete `%APPDATA%\FlowBench` (settings, authorized targets, plugins, logs) when **Also delete my settings, plugins and logs** is ticked; it is unticked by default and the box warns that this cannot be undone.
- A running FlowBench or a locked install folder now gets the same "quit FlowBench, then Retry" prompt as setup. `/S` and the other quiet switches still uninstall without any window.
- Reinstalling with the setup wizard uses the language, theme and accent from an existing FlowBench install instead of the system defaults.
- The uninstaller now bundles Qt, so it is larger than the old standard-library one; unused Qt parts (software OpenGL, PDF, QML, networking, Qt translations) are left out to keep it small.

### Verified

- Checked the uninstall window's confirm, progress and done pages in Chinese light, Chinese dark and English light, and ran the real uninstall entry point end to end (English, dark theme and a custom accent taken from FlowBench's settings).
- Covered the shared uninstall session, keeping or deleting user data, a running FlowBench, a locked folder with Retry, silent mode and preference parsing with automated tests.
- Passed the full automated test suite (133 tests, 1659 subtests).

## [1.2.9] - 2026-11-04

### Changed

- Charts on Home and Monitor zoom smoothly: each wheel notch animates the time axis to 80 % (or 125 %) around the point under the cursor, and the value axis fits the visible part. Once zoomed, drag left or right to pan, including back to where you were; double-click or zoom all the way out to resume live scrolling. The left/right-drag box zoom, which could not be dragged back, is removed. At the full view, scrolling down over a chart scrolls the page.
- Ctrl+1…Ctrl+6 now follow the sidebar order: Ctrl+4 opens Server Agents (previously unbound), Ctrl+5 Monitor and Ctrl+6 Plugins.
- Server Agents: the control URL and token each take a full row under one label column, so a URL is readable at the minimum window width.
- The trend charts on Home and Monitor use the full card height; the Home chart no longer drops axis labels at 1360×860.
- The plugin search box's suggestions button is a drop-down chevron (pointing up while open) instead of the arrow that the sort button next to it also uses.
- "Last error: —" is shown in gray while there is no error, and in red only for a real failure.
- Report target labels read `http://127.0.0.1` instead of `HTTP://127.0.0.1`, and the summed rate cap reads `150 QPS` instead of `150.0 QPS`.

### Fixed

- Switching pages with page animations on leaked several hundred animation objects per switch; they are now freed, and the Qt "animation without target" warnings are gone.
- When a stress test ends, the live card now shows the final totals from the report and resets Live QPS and Active Threads to 0; it used to keep the last half-second snapshot (for example 306 sent while the report said 322, and 12 active threads after the end).
- The report duration no longer includes the time spent waiting for in-flight requests after the test window: a 1-second TCP test against a silent server reported 2.9 seconds.
- On the Stress Test page the Protocol and duration-unit boxes have the same height as the number boxes beside them, so their labels line up.
- The number boxes under Settings → Defaults have one width and line up on the right.
- Clicking **Check for Updates** again within 10 seconds no longer re-enables the button while the second check is still running.
- `add_authorized()` returns `True` for a target that is already authorized and `False` for an empty one, instead of `None`, which callers reported as a save failure.

### Verified

- Ran a live three-target HTTP stress test against a local server (success, refused, HTTP 500) and checked that the live card, per-target line and report agree.
- Checked every page in Chinese and English, light and dark, at 1360×860 and at the 1080×700 minimum size.
- Passed the full automated test suite (124 tests, 1659 subtests).

## [1.2.8] - 2026-11-03

### Added

- Replaced Inno Setup with FlowBench's own installer (`flowbench_installer/`): a bilingual Fluent-style wizard that follows the light/dark theme, installs per user to `%LOCALAPPDATA%\Programs\FlowBench` without administrator rights, swaps the new files in only after they are fully extracted (a failed or cancelled install keeps the previous version), registers Start menu/desktop shortcuts with the FlowBench AppUserModelID and an entry in Settings → Apps, and removes an older Inno Setup install (1.2.7 and earlier) automatically.
- Added in-app updates: **Update now** downloads the release installer with a progress bar (size and, when GitHub provides it, SHA-256 verified; Retry on failure), closes FlowBench, installs in place, and reopens FlowBench. A failed update reopens the existing version. Updating from 1.2.7 or earlier still needs one manual install of 1.2.8.
- Added `FlowBench-Uninstall.exe`, which keeps settings and logs in `%APPDATA%\FlowBench`.
- Updated the Japanese language pack to 1.0.3 with translations for the update download dialog.

### Changed

- `build_installer.ps1` now needs only Python and PyInstaller; `installer.iss` is removed. Upload `FlowBench-Setup-<version>.exe` to the GitHub release under that exact name for the in-app updater.

### Verified

- Built the uninstaller with PyInstaller and ran its two-stage uninstall end to end; covered install, upgrade, rollback, cancel, unsafe paths, legacy migration, update mode, and download verification with automated tests.
- Checked the setup wizard and update window in Chinese and English, light and dark.
- Passed the full automated test suite (109 tests, 1652 subtests).

## [1.2.7] - 2026-11-02

### Added

- Added search to the Server Agents multi-select panel: filter by name, ID, hostname, platform, or online status with space-separated terms; Select All and Clear act on the visible results only.
- Restyled the server checklist with Windows/Linux icons, hostname details, and online/offline status pills in place of the plain gray list.
- Added `build_installer.ps1` to build the installer in one step, checking PyInstaller, Inno Setup, and version consistency before the build starts.
- Updated the Japanese language pack to 1.0.2 with translations for the search and server-row text.

### Fixed

- Releasing a mouse drag over a server row no longer toggles that row; a click must start and end on the same row.
- Duplicate agent IDs now appear once instead of leaving an extra row that search and Select All ignored.
- Malformed agent metadata no longer prevents the selection panel from opening.
- Rows hidden by a search no longer keep their hover highlight when shown again.
- `build_all.ps1` and `build_agent.ps1` now stop at the first failed PyInstaller run instead of continuing with stale or missing outputs.

### Verified

- Verified scrolling and mouse-wheel input with 60 servers and responsive search with 300.
- Passed the full automated test suite (75 tests, 1616 subtests).

## [1.2.6] - 2026-10-24

### Added

- Added multi-server selection for Server Agents with a scrollable checklist, Select All, Clear, and selected-count feedback.
- Added batch start and stop dispatch with per-node failure isolation and aggregated operation results.
- Updated the existing Japanese language pack to 1.0.1 with translations for the multi-server controls.

### Verified

- Verified the Server Agents selection panel with mouse-wheel scrolling and checkbox state retention.
- Verified Chinese and English UI text plus light and dark theme rendering.
- Passed the full automated test suite before packaging.

## [1.2.5] - 2026-09-30

### Added

- Added a live, bilingual Test Plan card below the local runtime status, showing target count, duration, total concurrency, total rate cap, protocol, and authorization progress without starting a test.
- Added regression coverage for all seven built-in pages, primary dialogs, placeholders, tooltips, and startup branding across Chinese/English and light/dark combinations.

### Changed

- Replaced the executable, shortcut, taskbar, and tray branding with the supplied ICO and selected the matching light/dark PNG for the startup splash.
- Unified the application, updater, installer configuration, and current documentation on version 1.2.5.

### Fixed

- Prevented the runtime status from stretching when HTTP/HTTPS-only controls are displayed, and kept the new plan card top-aligned in wide and stacked layouts.
- Preserved the plan card's typography and protocol badge styling after theme switches.
- Made the splash subtitle follow the selected interface language.
- Made the plugin publishing selector use the current language for bilingual plugin names, including disabled plugins, while preserving both translations in the generated JSON.

### Verified

- Passed all 45 automated tests and 147 subtests, including protocol-switch layout and queued-start cancellation regressions.
- Visually checked all seven built-in pages and the splash using the Windows Qt backend in Chinese/English and light/dark modes.
- Built the Windows GUI, Agent, Hub, and operator CLI, and verified packaged GUI startup in all four language/theme combinations.
- Verified the packaged version, Test Plan implementation, nine embedded icon sizes, theme-specific splash assets, and bundled server templates.
- Built the bilingual Windows installer with Inno Setup 6.7.3.

## [1.2.4] - 2026-09-20

### Added

- Added a public MQTT relay mode for Server Agents. Relay node bundles no longer require users to enter a public server IP or domain, and servers only need outbound internet access instead of an open inbound TCP 8787 control port.
- Added automatic relay URL and controller-token generation and persistence so newly generated nodes can be controlled immediately and additional nodes can join the same private relay channel.

### Changed

- Unified the desktop app, updater, installer, documentation, and release metadata on version 1.2.4.
- Made installer migration prompts follow the selected setup language instead of mixing Chinese and English.
- Refined the application layout with more consistent page margins, card spacing, top alignment, navigation reflow, and responsive two-column-to-stacked breakpoints across the primary views.
- Improved the Server Agents and Collaborative Testing pages so controls remain usable and visually balanced at both wide and narrow window sizes.

### Fixed

- Preserved the configured accent color when switching between light and dark themes.
- Cancelled locally queued stress tests when Stop is pressed during the short startup-render delay.
- Cancelled queued collaborative remote starts when a stop command arrives, including starts waiting for a previous job to finish.
- Restored the previous report, or the empty report state, when a test fails or is cancelled before startup.
- Prevented importing an already-installed folder plugin from deleting its own source directory.
- Recognized plugin files with uppercase or mixed-case `.py` extensions.

### Verified

- Added regression coverage for theme accent persistence, queued-start cancellation, version consistency, and English UI text.
- Passed all 29 automated tests, packaged-application startup smoke testing, and the Inno Setup 7 installer build.

## [1.2.2] - 2026-09-07

### Added

- Added a cross-platform headless server-agent mode with an authenticated control Hub, outbound-only polling, heartbeat reporting, and persistent command cursors.
- Added a “Server Agents” page to the desktop GUI for refreshing agents, starting and stopping authorized jobs, and viewing live node status and operation logs.
- Added one-click server-node bundle generation for Windows and Linux, including Hub and Agent credentials, generated HTTPS certificates, startup scripts, and bounded configuration.
- Added server-node status tiles for total requests, successes, live QPS, and total traffic, with per-node state and traffic details.
- Added agent job progress reporting and completion summaries, including total packets, successes, failures, latency, and sent bytes.
- Added Docker deployment files and Railway guidance for Hub/Agent bundles.
- Added English release notes and updated bilingual documentation in `SERVER_AGENT.md`.

### Changed

- Kept the Stress Test page focused on local jobs; server jobs now display their live status on the Server Agents page.
- Preserved the Collaborative Testing node-status card and added total sent traffic.
- Improved dark-mode consistency for dialogs, status editors, theme-color swatches, and hover backgrounds.
- Unified application, installer, updater, and release metadata to version 1.2.2.

### Fixed

- Fixed a packaged-application startup failure where the first PySide6.QtCore import could fail with a missing DLL entry point; bundled PySide6/shiboken6 DLL directories are now registered and Shiboken/QtCore are preloaded in frozen builds.
- Excluded incompatible bundled ICU DLLs so Qt uses the Windows-provided ICU runtime instead of a conflicting ICU 78 runtime.
- Removed automatic marketplace PR merging; external submissions now remain open for manual maintainer review.
- Kept the legacy marketplace workflow filename as a no-op guard so older clients cannot re-enable automatic merging.
- Fixed server-node completion logs repeating after polling resumed; completion entries are now deduplicated per node and job.
- Fixed startup races in all-in-one Linux bundles by waiting for the Hub `/health` endpoint before starting the Agent.
- Fixed self-signed control-plane TLS handling with a GUI compatibility retry when the generated CA file is unavailable; controller tokens are still required.
- Fixed Hub handling of aborted TLS handshakes and HTTP clients pointing at HTTPS ports, preventing noisy disconnect errors.
- Fixed Hub and Agent binding/URL mismatch in generated bundles when a non-default public port was specified.
- Fixed dark-mode dialog fallback and a potential settings-card color import issue.
- Prevented page controls from remaining transparent after rapid page switches or missed animation callbacks.

### Verified

- Agent/Hub unit tests passed: 6/6.
- Python compilation and AST checks passed.
- Light/dark UI smoke tests passed across all primary pages and dialogs.
- Windows Agent, Hub, CLI, desktop GUI, and Inno Setup installer builds completed.

## [1.2.0] - 2026-08-24

### Added

- Added smooth staggered control reveals to startup and every main page, including the plugin marketplace.
- Added a bilingual Page Animations setting that applies immediately and is included in preference backup, restore, and reset workflows.

### Changed

- Registered the system tray icon at application startup so it remains available throughout the running session.
- Disabled both page transitions and control reveals when Page Animations is turned off.

### Fixed

- Prevented page text from appearing for one frame before its entrance animation begins.
- Cleaned up active effects during rapid page switches and when animations are disabled at runtime.
- Added transition-stop compatibility for current and newer QFluentWidgets stacked-widget implementations.
- Included first-launch dashboard animation and first-visit marketplace animation.
- Corrected stale marketplace checksums for the Traditional Chinese, Lucky Wheel, and Quick Notes plugins.

### Verified

- Confirmed that disabling automatic update checks takes effect immediately without restarting the application.
- Passed English-mode runtime scans across all main pages and both marketplace tabs with no visible Chinese text.
- Passed dark-mode rendering checks across all main pages.
- Passed animation cleanup, rapid-switching, settings persistence, and tray startup checks.

## [1.1.9] - 2026-08-24

### Changed

- Removed the unused "Copy Public Address" button from collaborative host controls.
- Kept invite-code and LAN-address copy actions unchanged.

## [1.1.8] - 2026-08-24

### Fixed

- Hardened concurrent settings writes to prevent background tasks from overwriting each other's changes.
- Improved collaborative host and port validation, including IPv6 support.
- Fixed UDP testing for IPv6 targets and corrected HTTP request-body traffic accounting.
- Completed English labels for the built-in CVE plugin.
- Improved tray behavior with stable Windows app identity and safer delayed registration.
- Kept dynamically loaded plugin pages in the scrollable navigation area.

## [1.1.7] - 2026-08-23

### Fixed

- Fixed marketplace index requests falling back to the offline cache when an expired GitHub token was present.
- Kept the cached GitHub login available for author-only marketplace actions after token cleanup.
- Synced the Settings page immediately after disabling automatic update checks from the update prompt.

### Verified

- English-mode UI smoke check passed with no visible Chinese text in the loaded application widgets.
- Dark-mode theme switching passed in the Qt offscreen regression check.
- Marketplace index fetch returned live entries instead of the local cache.

## [1.1.6] - 2026-08-22

### Fixed

- Fixed marketplace refreshes returning stale indexes immediately after a plugin publish.
- Fixed rapid marketplace refresh clicks duplicating plugin cards in the UI.
- Preserved registered plugin protocols when importing stress-test configurations.
- Clamped imported ports, thread counts, rate limits, and durations to supported ranges.
- Rejected malformed bracketed IPv6 host/port values in collaborative direct-connect mode.
- Reset relay mode state cleanly when a collaborative host session is shut down.
- Completed English labels in the built-in Test Environment plugin, including localized time-zone display.
- Updated the marketplace checksum for the corrected Test Environment plugin.

### Improved

- Refined tray-menu positioning so the complete menu remains visible near screen edges.
- Made plugin lists independently scrollable while keeping page controls visible.
- Improved plugin-market and report-menu animation compatibility with current QFluentWidgets versions.
- Updated installer metadata, documentation, and the displayed application version to `1.1.6`.

### Verification

- Python bytecode compilation passed for the application and marketplace plugins.
- Qt offscreen smoke startup passed for the main window and all primary views.
- English-mode UI smoke check found no visible Chinese text in the loaded application widgets.
